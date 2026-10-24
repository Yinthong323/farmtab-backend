import hashlib
import os
from datetime import datetime, timedelta, timezone
from io import BytesIO

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    UploadFile,
)

from database.connection import get_connection
from shelves.shelves import (
    check_site_admin_or_staff,
    get_current_user_id,
)

router = APIRouter(
    tags=["Layers"],
)

MYT = timezone(timedelta(hours=8))          # Malaysia time, used for folder and file names
MAX_IMAGE_BYTES = 15 * 1024 * 1024          # 15 MB per photo
ALLOWED_CAPTURE_TYPES = {"scheduled", "start", "harvest", "manual"}
UPLOAD_ROOT = "uploads/layers"              # inside the container: /app/uploads/layers


def read_image_size(data: bytes):
    """Return (width, height) if Pillow is installed, otherwise (None, None)."""
    try:
        from PIL import Image

        with Image.open(BytesIO(data)) as img:
            return img.width, img.height
    except Exception:
        return None, None


# ============================================================
# UPLOAD IMAGE FROM A RASPBERRY PI CAMERA
# ============================================================
# Called by the Pi, not by the app. The Pi proves who it is with its
# own device token (header X-Device-Token), not with a user login.

@router.post("/layers/upload")
def upload_layer_image(
    layer_code: str = Form(...),
    captured_at: str = Form(...),
    capture_type: str = Form("scheduled"),
    image: UploadFile = File(...),
    x_device_token: str | None = Header(None),
):
    # ----------------------------------------------------
    # Validate the request before touching the database
    # ----------------------------------------------------

    if not x_device_token:
        raise HTTPException(
            status_code=401,
            detail="Missing device token.",
        )

    try:
        captured = datetime.fromisoformat(captured_at.strip())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="captured_at must look like 2026-10-25T20:00:00+08:00.",
        )

    if captured.tzinfo is None:
        raise HTTPException(
            status_code=400,
            detail="captured_at must include a time zone offset, e.g. +08:00.",
        )

    if captured > datetime.now(timezone.utc) + timedelta(minutes=10):
        raise HTTPException(
            status_code=400,
            detail="captured_at is in the future. Check the Pi's clock.",
        )

    capture_type = capture_type.strip().lower()

    if capture_type not in ALLOWED_CAPTURE_TYPES:
        raise HTTPException(
            status_code=400,
            detail="capture_type must be scheduled, start, harvest or manual.",
        )

    if image.content_type != "image/jpeg":
        raise HTTPException(
            status_code=400,
            detail="Only JPG images are allowed.",
        )

    data = image.file.read()

    if not data:
        raise HTTPException(
            status_code=400,
            detail="Image file is empty.",
        )

    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Image is larger than 15 MB.",
        )

    token_hash = hashlib.sha256(x_device_token.encode()).hexdigest()

    connection = get_connection()
    file_on_disk = None
    file_written = False

    try:
        cursor = connection.cursor()

        # ----------------------------------------------------
        # Identify the layer from its device token
        # ----------------------------------------------------

        cursor.execute(
            """
            SELECT
                id,
                shelf_id,
                code
            FROM layers
            WHERE upload_token_hash = %s
            """,
            (token_hash,),
        )

        layer = cursor.fetchone()

        if not layer:
            cursor.close()

            raise HTTPException(
                status_code=401,
                detail="Invalid device token.",
            )

        layer_id, shelf_id, code = layer

        if code != layer_code.strip():
            cursor.close()

            raise HTTPException(
                status_code=403,
                detail="This token does not belong to that layer.",
            )

        # ----------------------------------------------------
        # Attach to the active cycle, if one is running
        # ----------------------------------------------------

        local_time = captured.astimezone(MYT)

        cursor.execute(
            """
            SELECT id
            FROM layer_cycles
            WHERE layer_id = %s
              AND status = 'ACTIVE'
              AND transplant_date <= %s
            """,
            (
                layer_id,
                local_time.date(),
            ),
        )

        cycle = cursor.fetchone()
        cycle_id = cycle[0] if cycle else None

        # ----------------------------------------------------
        # Decide where the file goes
        # uploads/layers/<shelf>/<code>/<YYYY-MM>/<code>_<date>_<time>_<type>.jpg
        # ----------------------------------------------------

        relative_directory = os.path.join(
            UPLOAD_ROOT,
            str(shelf_id),
            code,
            local_time.strftime("%Y-%m"),
        )

        filename = (
            f"{code}_{local_time.strftime('%Y%m%d_%H%M%S')}_{capture_type}.jpg"
        )

        file_on_disk = os.path.join(relative_directory, filename)
        file_url = "/" + file_on_disk.replace(os.sep, "/")

        width, height = read_image_size(data)

        # ----------------------------------------------------
        # Insert the record. A retried upload of the same photo
        # (same layer + same captured_at) is ignored, not duplicated.
        # ----------------------------------------------------

        cursor.execute(
            """
            INSERT INTO layer_images (
                layer_id,
                cycle_id,
                captured_at,
                capture_type,
                file_path,
                width,
                height,
                file_size_bytes,
                sha256
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (layer_id, captured_at) DO NOTHING
            RETURNING id
            """,
            (
                layer_id,
                cycle_id,
                captured,
                capture_type,
                file_url,
                width,
                height,
                len(data),
                hashlib.sha256(data).hexdigest(),
            ),
        )

        row = cursor.fetchone()

        if not row:
            connection.rollback()
            cursor.close()

            return {
                "message": "Image was already uploaded.",
                "duplicate": True,
            }

        image_id = row[0]

        # ----------------------------------------------------
        # Save the file, then commit
        # ----------------------------------------------------

        os.makedirs(relative_directory, exist_ok=True)

        with open(file_on_disk, "wb") as file:
            file.write(data)

        file_written = True

        connection.commit()
        cursor.close()

        return {
            "message": "Image uploaded successfully.",
            "duplicate": False,
            "image": {
                "id": image_id,
                "layer_code": code,
                "cycle_id": cycle_id,
                "captured_at": local_time.isoformat(),
                "capture_type": capture_type,
                "image_url": file_url,
                "width": width,
                "height": height,
            },
        }

    except HTTPException:
        connection.rollback()
        raise

    except Exception as e:
        connection.rollback()

        # Do not leave a file behind if the database record failed
        if file_written and file_on_disk and os.path.exists(file_on_disk):
            os.remove(file_on_disk)

        raise HTTPException(
            status_code=500,
            detail=f"Unable to upload image: {str(e)}",
        )

    finally:
        connection.close()


# ============================================================
# GET THE SIX LAYERS OF A SHELF WITH THEIR LATEST IMAGE
# ============================================================
# Called by the app (Growth Monitoring page). Uses the normal user login.

@router.get("/sites/{site_id}/shelves/{shelf_id}/layers")
def get_shelf_layers(
    site_id: int,
    shelf_id: int,
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

        cursor.execute(
            """
            SELECT
                l.id,
                l.code,
                l.display_name,
                l.sort_order,
                img.id,
                img.file_path,
                img.captured_at,
                img.capture_type,
                c.id,
                c.transplant_date,
                c.plant_count
            FROM layers l
            JOIN shelves s
                ON s.id = l.shelf_id
            LEFT JOIN LATERAL (
                SELECT
                    id,
                    file_path,
                    captured_at,
                    capture_type
                FROM layer_images
                WHERE layer_id = l.id
                ORDER BY captured_at DESC
                LIMIT 1
            ) img ON TRUE
            LEFT JOIN layer_cycles c
                ON c.layer_id = l.id
               AND c.status = 'ACTIVE'
            WHERE l.shelf_id = %s
              AND s.site_id = %s
            ORDER BY l.sort_order
            """,
            (
                shelf_id,
                site_id,
            ),
        )

        rows = cursor.fetchall()

        cursor.close()

        layers = []

        for row in rows:
            latest_image = None

            if row[4] is not None:
                latest_image = {
                    "id": row[4],
                    "image_url": row[5],
                    "captured_at": row[6].astimezone(MYT).isoformat(),
                    "capture_type": row[7],
                }

            active_cycle = None

            if row[8] is not None:
                active_cycle = {
                    "id": row[8],
                    "transplant_date": row[9],
                    "plant_count": row[10],
                }

            layers.append(
                {
                    "layer_id": row[0],
                    "code": row[1],
                    "display_name": row[2],
                    "sort_order": row[3],
                    "latest_image": latest_image,
                    "active_cycle": active_cycle,
                }
            )

        return layers

    finally:
        connection.close()
