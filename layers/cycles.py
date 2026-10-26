import hashlib
from datetime import datetime, timedelta, timezone

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
)

from database.connection import get_connection
from layers.schemas import (
    AbortLayerCycleRequest,
    StartLayerCycleRequest,
)
from shelves.shelves import (
    check_site_admin_or_staff,
    get_current_user_id,
)

router = APIRouter(
    tags=["Layer Cycles"],
)

MYT = timezone(timedelta(hours=8))

SCHEDULED_CAPTURE_HOUR = 20       # 8:00 PM daily capture
MAX_BACKDATE_DAYS = 60            # a transplant date may be at most this far in the past
REQUEST_EXPIRES_MINUTES = 10      # a "capture now" job nobody answered is cancelled

# Photo types that belong to a growing cycle. 'manual' photos are tests and never join one.
CYCLE_CAPTURE_TYPES = ["scheduled", "start", "harvest"]


def is_unique_violation(error: Exception) -> bool:
    """True for a PostgreSQL unique-constraint error (works with psycopg 2 and 3)."""
    return (
        getattr(error, "sqlstate", None) == "23505"
        or getattr(error, "pgcode", None) == "23505"
    )


def get_layer_in_shelf(cursor, site_id: int, shelf_id: int, layer_id: int):
    """Return (id, code) if the layer really belongs to this shelf and site."""
    cursor.execute(
        """
        SELECT l.id, l.code
        FROM layers l
        JOIN shelves s
            ON s.id = l.shelf_id
        WHERE l.id = %s
          AND l.shelf_id = %s
          AND s.site_id = %s
        """,
        (layer_id, shelf_id, site_id),
    )

    layer = cursor.fetchone()

    if not layer:
        raise HTTPException(
            status_code=404,
            detail="Layer not found.",
        )

    return layer


# ============================================================
# START A GROWING CYCLE ON ONE LAYER
# ============================================================

@router.post("/sites/{site_id}/shelves/{shelf_id}/layers/{layer_id}/cycles")
def start_layer_cycle(
    site_id: int,
    shelf_id: int,
    layer_id: int,
    request: StartLayerCycleRequest,
    user_id: int = Depends(get_current_user_id),
):
    connection = get_connection()

    try:
        # Any approved member of the organisation (admin or staff) may start a cycle
        check_site_admin_or_staff(
            connection,
            site_id,
            user_id,
        )

        cursor = connection.cursor()

        layer = get_layer_in_shelf(cursor, site_id, shelf_id, layer_id)

        # ----------------------------------------------------
        # Validate the transplant date (Malaysia time)
        # ----------------------------------------------------

        now_myt = datetime.now(MYT)
        today = now_myt.date()

        if request.transplant_date > today:
            raise HTTPException(
                status_code=400,
                detail="Transplant date cannot be in the future.",
            )

        if request.transplant_date < today - timedelta(days=MAX_BACKDATE_DAYS):
            raise HTTPException(
                status_code=400,
                detail=f"Transplant date cannot be more than {MAX_BACKDATE_DAYS} days ago.",
            )

        # ----------------------------------------------------
        # Only one ACTIVE cycle per layer
        # ----------------------------------------------------

        cursor.execute(
            """
            SELECT id
            FROM layer_cycles
            WHERE layer_id = %s
              AND status = 'ACTIVE'
            """,
            (layer_id,),
        )

        if cursor.fetchone():
            raise HTTPException(
                status_code=409,
                detail="This layer already has an active growing cycle.",
            )

        # ----------------------------------------------------
        # Create the cycle
        # ----------------------------------------------------

        cursor.execute(
            """
            INSERT INTO layer_cycles (
                layer_id,
                transplant_date,
                plant_count,
                variety,
                notes,
                started_by
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                layer_id,
                request.transplant_date,
                request.plant_count,
                request.variety.strip() or "looseleaf lettuce",
                request.notes.strip() if request.notes else None,
                user_id,
            ),
        )

        cycle_id = cursor.fetchone()[0]

        # ----------------------------------------------------
        # Attach photos already taken since the transplant date.
        # Only real captures; 'manual' test photos are never attached.
        # ----------------------------------------------------

        cursor.execute(
            """
            UPDATE layer_images
            SET cycle_id = %s
            WHERE layer_id = %s
              AND cycle_id IS NULL
              AND capture_type = ANY(%s)
              AND (captured_at AT TIME ZONE 'Asia/Kuala_Lumpur')::date >= %s
            """,
            (
                cycle_id,
                layer_id,
                CYCLE_CAPTURE_TYPES,
                request.transplant_date,
            ),
        )

        attached_images = cursor.rowcount

        # ----------------------------------------------------
        # When does the first photo of this cycle arrive?
        #   - started today after 8 PM  -> take one now (lights are on)
        #   - photos already exist      -> nothing to wait for
        #   - otherwise                 -> the 8 PM capture tonight
        # ----------------------------------------------------

        capture_request_id = None

        if (
            request.transplant_date == today
            and now_myt.hour >= SCHEDULED_CAPTURE_HOUR
        ):
            cursor.execute(
                """
                INSERT INTO capture_requests (
                    layer_id,
                    cycle_id,
                    reason,
                    requested_by
                )
                VALUES (%s, %s, 'start', %s)
                RETURNING id
                """,
                (layer_id, cycle_id, user_id),
            )

            capture_request_id = cursor.fetchone()[0]
            first_capture = "requested"

        elif attached_images > 0:
            first_capture = "existing"

        else:
            first_capture = "tonight_8pm"

        connection.commit()
        cursor.close()

        return {
            "message": "Growing cycle started.",
            "cycle": {
                "id": cycle_id,
                "layer_id": layer_id,
                "layer_code": layer[1],
                "transplant_date": request.transplant_date,
                "plant_count": request.plant_count,
                "variety": request.variety,
                "status": "ACTIVE",
            },
            "attached_images": attached_images,
            "first_capture": first_capture,
            "capture_request_id": capture_request_id,
        }

    except HTTPException:
        connection.rollback()
        raise

    except Exception as e:
        connection.rollback()

        # Two people pressing Start at the same moment: the database rule wins
        if is_unique_violation(e):
            raise HTTPException(
                status_code=409,
                detail="This layer already has an active growing cycle.",
            )

        raise HTTPException(
            status_code=500,
            detail=f"Unable to start growing cycle: {str(e)}",
        )

    finally:
        connection.close()


# ============================================================
# ABORT AN ACTIVE CYCLE (started by mistake, or failed batch)
# ============================================================
# An aborted cycle keeps its photos and records for reference,
# but it is excluded from the machine learning dataset.

@router.post(
    "/sites/{site_id}/shelves/{shelf_id}/layers/{layer_id}/cycles/{cycle_id}/abort"
)
def abort_layer_cycle(
    site_id: int,
    shelf_id: int,
    layer_id: int,
    cycle_id: int,
    request: AbortLayerCycleRequest,
    user_id: int = Depends(get_current_user_id),
):
    connection = get_connection()

    try:
        check_site_admin_or_staff(
            connection,
            site_id,
            user_id,
        )

        cursor = connection.cursor()

        get_layer_in_shelf(cursor, site_id, shelf_id, layer_id)

        reason = request.reason.strip() if request.reason else None

        cursor.execute(
            """
            UPDATE layer_cycles
            SET
                status = 'ABORTED',
                notes = CASE
                    WHEN %s::text IS NULL THEN notes
                    WHEN notes IS NULL THEN 'Aborted: ' || %s::text
                    ELSE notes || E'\\nAborted: ' || %s::text
                END,
                ended_by = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
              AND layer_id = %s
              AND status = 'ACTIVE'
            RETURNING id
            """,
            (reason, reason, reason, user_id, cycle_id, layer_id),
        )

        if not cursor.fetchone():
            raise HTTPException(
                status_code=404,
                detail="Active growing cycle not found for this layer.",
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

        return {"message": "Growing cycle aborted."}

    except HTTPException:
        connection.rollback()
        raise

    except Exception as e:
        connection.rollback()

        raise HTTPException(
            status_code=500,
            detail=f"Unable to abort growing cycle: {str(e)}",
        )

    finally:
        connection.close()


# ============================================================
# USED BY THE RASPBERRY PI: "IS THERE A PHOTO WAITING FOR ME?"
# ============================================================
# Each Pi asks about once a minute, using its own device token.
# If a request is waiting, it is handed to this Pi once (marked CLAIMED)
# so the same job is never done twice.

@router.get("/layers/capture-requests/pending")
def get_pending_capture_request(
    x_device_token: str | None = Header(None),
):
    if not x_device_token:
        raise HTTPException(
            status_code=401,
            detail="Missing device token.",
        )

    token_hash = hashlib.sha256(x_device_token.encode()).hexdigest()

    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT id
            FROM layers
            WHERE upload_token_hash = %s
            """,
            (token_hash,),
        )

        layer = cursor.fetchone()

        if not layer:
            raise HTTPException(
                status_code=401,
                detail="Invalid device token.",
            )

        layer_id = layer[0]

        # Old jobs nobody answered are cancelled, so a Pi that was offline
        # does not take a surprise photo ten minutes later
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

        # Take the oldest waiting job and mark it CLAIMED in one step
        cursor.execute(
            """
            UPDATE capture_requests
            SET status = 'CLAIMED',
                claimed_at = CURRENT_TIMESTAMP
            WHERE id = (
                SELECT id
                FROM capture_requests
                WHERE layer_id = %s
                  AND status = 'PENDING'
                ORDER BY requested_at
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id, reason
            """,
            (layer_id,),
        )

        row = cursor.fetchone()

        connection.commit()
        cursor.close()

        if not row:
            return {"request": None}

        return {
            "request": {
                "id": row[0],
                "reason": row[1],
            }
        }

    except HTTPException:
        connection.rollback()
        raise

    except Exception as e:
        connection.rollback()

        raise HTTPException(
            status_code=500,
            detail=f"Unable to check capture requests: {str(e)}",
        )

    finally:
        connection.close()
