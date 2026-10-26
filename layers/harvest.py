from datetime import datetime, timedelta, timezone

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
)

from canopy.canopy import DEFAULT_METHOD
from canopy.daily import rebuild_cycle_records
from database.connection import get_connection
from layers.cycles import (
    CYCLE_CAPTURE_TYPES,
    REQUEST_EXPIRES_MINUTES,
    get_layer_in_shelf,
)
from layers.schemas import HarvestLayerCycleRequest
from shelves.shelves import (
    check_site_admin_or_staff,
    get_current_user_id,
)

router = APIRouter(
    tags=["Layer Harvest"],
)

MYT = timezone(timedelta(hours=8))

BASE = "/sites/{site_id}/shelves/{shelf_id}/layers/{layer_id}/cycles/{cycle_id}"


def get_active_cycle(cursor, layer_id: int, cycle_id: int, lock: bool = False):
    """Return (id, transplant_date) of the ACTIVE cycle, or raise 404."""
    cursor.execute(
        f"""
        SELECT id, transplant_date
        FROM layer_cycles
        WHERE id = %s
          AND layer_id = %s
          AND status = 'ACTIVE'
        {"FOR UPDATE" if lock else ""}
        """,
        (cycle_id, layer_id),
    )

    cycle = cursor.fetchone()

    if not cycle:
        raise HTTPException(
            status_code=404,
            detail="Active growing cycle not found for this layer.",
        )

    return cycle


def photo_payload(row) -> dict:
    """row = (id, file_path, captured_at, capture_type, pixels, cm2, overlay_path, status)"""
    return {
        "id": row[0],
        "image_url": row[1],
        "captured_at": row[2].astimezone(MYT).isoformat(),
        "capture_type": row[3],
        "canopy": {
            "pixels": row[4],
            "cm2": row[5],
            "overlay_url": row[6],
            "status": row[7],
        },
    }


def photo_state(row) -> dict:
    """Is this photo ready to be used as the final harvest photo?"""
    status = row[7]

    if status is None:
        return {"state": "measuring"}          # canopy is still being measured

    if status != "ok":
        return {"state": "measure_failed", "image": photo_payload(row)}

    return {"state": "ready", "image": photo_payload(row)}


PHOTO_COLUMNS = """
    i.id,
    i.file_path,
    i.captured_at,
    i.capture_type,
    m.canopy_pixels,
    m.canopy_cm2,
    m.overlay_path,
    m.status
"""


# ============================================================
# WHICH PHOTO WILL BE THE FINAL HARVEST PHOTO?
# ============================================================
# - without request_id: look for a usable photo taken today
# - with request_id:    follow a "capture now" job the user started

@router.get(BASE + "/harvest-photo")
def get_harvest_photo(
    site_id: int,
    shelf_id: int,
    layer_id: int,
    cycle_id: int,
    request_id: int | None = Query(None),
    user_id: int = Depends(get_current_user_id),
):
    connection = get_connection()

    try:
        check_site_admin_or_staff(connection, site_id, user_id)

        cursor = connection.cursor()

        get_layer_in_shelf(cursor, site_id, shelf_id, layer_id)
        get_active_cycle(cursor, layer_id, cycle_id)

        # ----------------------------------------------------
        # Following a "capture now" job
        # ----------------------------------------------------
        if request_id is not None:
            # A job nobody picked up in time is cancelled
            cursor.execute(
                """
                UPDATE capture_requests
                SET status = 'EXPIRED'
                WHERE layer_id = %s
                  AND status IN ('PENDING', 'CLAIMED')
                  AND requested_at < CURRENT_TIMESTAMP - make_interval(mins => %s)
                """,
                (layer_id, REQUEST_EXPIRES_MINUTES),
            )
            connection.commit()

            cursor.execute(
                """
                SELECT status, image_id
                FROM capture_requests
                WHERE id = %s
                  AND layer_id = %s
                """,
                (request_id, layer_id),
            )

            job = cursor.fetchone()

            if not job:
                raise HTTPException(
                    status_code=404,
                    detail="Capture request not found.",
                )

            job_status, image_id = job

            if job_status in ("PENDING", "CLAIMED"):
                return {"state": "waiting"}

            if job_status == "EXPIRED" or image_id is None:
                return {"state": "expired"}

            cursor.execute(
                f"""
                SELECT {PHOTO_COLUMNS}
                FROM layer_images i
                LEFT JOIN canopy_measurements m
                    ON m.image_id = i.id
                   AND m.method = %s
                WHERE i.id = %s
                """,
                (DEFAULT_METHOD, image_id),
            )

            row = cursor.fetchone()

            return photo_state(row) if row else {"state": "expired"}

        # ----------------------------------------------------
        # Is there already a usable photo today?
        # Best one: harvest > start > the 8 PM photo; newest first.
        # ----------------------------------------------------
        today = datetime.now(MYT).date()

        cursor.execute(
            f"""
            SELECT {PHOTO_COLUMNS}
            FROM layer_images i
            LEFT JOIN canopy_measurements m
                ON m.image_id = i.id
               AND m.method = %s
            WHERE i.cycle_id = %s
              AND i.capture_type = ANY(%s)
              AND (i.captured_at AT TIME ZONE 'Asia/Kuala_Lumpur')::date = %s
            ORDER BY
                CASE i.capture_type
                    WHEN 'harvest' THEN 3
                    WHEN 'start' THEN 2
                    ELSE 1
                END DESC,
                i.captured_at DESC
            LIMIT 1
            """,
            (DEFAULT_METHOD, cycle_id, CYCLE_CAPTURE_TYPES, today),
        )

        row = cursor.fetchone()
        cursor.close()

        if not row:
            return {"state": "needs_capture"}

        return photo_state(row)

    except HTTPException:
        connection.rollback()
        raise

    except Exception as e:
        connection.rollback()

        raise HTTPException(
            status_code=500,
            detail=f"Unable to check the harvest photo: {str(e)}",
        )

    finally:
        connection.close()


# ============================================================
# ASK THE CAMERA FOR A NEW PHOTO (the grow lights must be on)
# ============================================================

@router.post(BASE + "/harvest/capture")
def request_harvest_capture(
    site_id: int,
    shelf_id: int,
    layer_id: int,
    cycle_id: int,
    user_id: int = Depends(get_current_user_id),
):
    connection = get_connection()

    try:
        check_site_admin_or_staff(connection, site_id, user_id)

        cursor = connection.cursor()

        get_layer_in_shelf(cursor, site_id, shelf_id, layer_id)
        get_active_cycle(cursor, layer_id, cycle_id)

        # Pressing the button twice must not create two photos
        cursor.execute(
            """
            SELECT id
            FROM capture_requests
            WHERE layer_id = %s
              AND reason = 'harvest'
              AND status IN ('PENDING', 'CLAIMED')
              AND requested_at > CURRENT_TIMESTAMP - make_interval(mins => %s)
            ORDER BY id DESC
            LIMIT 1
            """,
            (layer_id, REQUEST_EXPIRES_MINUTES),
        )

        open_request = cursor.fetchone()

        if open_request:
            request_id = open_request[0]
        else:
            cursor.execute(
                """
                INSERT INTO capture_requests (
                    layer_id,
                    cycle_id,
                    reason,
                    requested_by
                )
                VALUES (%s, %s, 'harvest', %s)
                RETURNING id
                """,
                (layer_id, cycle_id, user_id),
            )

            request_id = cursor.fetchone()[0]

        connection.commit()
        cursor.close()

        return {"request_id": request_id}

    except HTTPException:
        connection.rollback()
        raise

    except Exception as e:
        connection.rollback()

        raise HTTPException(
            status_code=500,
            detail=f"Unable to request a harvest photo: {str(e)}",
        )

    finally:
        connection.close()


# ============================================================
# HARVEST: END THE CYCLE AND RECORD ITS FINAL RESULT
# ============================================================

@router.post(BASE + "/harvest")
def harvest_layer_cycle(
    site_id: int,
    shelf_id: int,
    layer_id: int,
    cycle_id: int,
    request: HarvestLayerCycleRequest,
    user_id: int = Depends(get_current_user_id),
):
    connection = get_connection()

    try:
        check_site_admin_or_staff(connection, site_id, user_id)

        cursor = connection.cursor()

        get_layer_in_shelf(cursor, site_id, shelf_id, layer_id)

        # FOR UPDATE: two people pressing Harvest together cannot both succeed
        _, transplant_date = get_active_cycle(
            cursor, layer_id, cycle_id, lock=True
        )

        today = datetime.now(MYT).date()

        pixels = None
        cm2 = None

        # ----------------------------------------------------
        # The chosen final photo must be a real, measured photo
        # of this cycle taken today
        # ----------------------------------------------------
        if request.image_id is not None:
            cursor.execute(
                """
                SELECT m.canopy_pixels, m.canopy_cm2
                FROM layer_images i
                JOIN canopy_measurements m
                    ON m.image_id = i.id
                   AND m.method = %s
                   AND m.status = 'ok'
                WHERE i.id = %s
                  AND i.layer_id = %s
                  AND i.cycle_id = %s
                  AND i.capture_type = ANY(%s)
                  AND (i.captured_at AT TIME ZONE 'Asia/Kuala_Lumpur')::date = %s
                """,
                (
                    DEFAULT_METHOD,
                    request.image_id,
                    layer_id,
                    cycle_id,
                    CYCLE_CAPTURE_TYPES,
                    today,
                ),
            )

            photo = cursor.fetchone()

            if not photo:
                raise HTTPException(
                    status_code=400,
                    detail="That photo cannot be used as the final harvest photo.",
                )

            pixels, cm2 = photo

        note = request.harvest_note.strip() if request.harvest_note else None

        if request.image_id is None:
            note = (
                "[harvested without a final photo] " + note
                if note
                else "[harvested without a final photo]"
            )

        # ----------------------------------------------------
        # End the cycle
        # ----------------------------------------------------
        cursor.execute(
            """
            UPDATE layer_cycles
            SET
                status = 'HARVESTED',
                harvest_date = %s,
                harvest_image_id = %s,
                harvest_canopy_pixels = %s,
                harvest_canopy_cm2 = %s,
                final_weight_g = %s,
                harvest_note = %s,
                ended_by = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (
                today,
                request.image_id,
                pixels,
                cm2,
                request.final_weight_g,
                note,
                user_id,
                cycle_id,
            ),
        )

        # Cancel any "capture now" job still waiting for this layer
        cursor.execute(
            """
            UPDATE capture_requests
            SET status = 'EXPIRED'
            WHERE layer_id = %s
              AND status IN ('PENDING', 'CLAIMED')
            """,
            (layer_id,),
        )

        connection.commit()
        cursor.close()

        # ----------------------------------------------------
        # Finish the daily records: the harvest day gets its final
        # row (marked is_harvest_day). A problem here must not undo
        # the harvest, so it is only reported.
        # ----------------------------------------------------
        try:
            rebuild_cycle_records(cycle_id, DEFAULT_METHOD, force_date=today)
        except Exception as daily_error:
            print(f"daily records failed after harvest of cycle {cycle_id}: {daily_error}")

        return {
            "message": "Growing cycle harvested.",
            "cycle": {
                "id": cycle_id,
                "harvest_date": today,
                "growth_days": (today - transplant_date).days + 1,
                "harvest_canopy_pixels": pixels,
                "harvest_canopy_cm2": cm2,
                "final_weight_g": request.final_weight_g,
            },
        }

    except HTTPException:
        connection.rollback()
        raise

    except Exception as e:
        connection.rollback()

        raise HTTPException(
            status_code=500,
            detail=f"Unable to harvest growing cycle: {str(e)}",
        )

    finally:
        connection.close()