import os
import uuid

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    File,
    Form,
    UploadFile,
)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from database.connection import get_connection
from auth.security import verify_access_token

from shelves.schemas import (
    CreateShelfRequest,
    UpdateShelfRequest,
    UpdateShelfThresholdRequest,
)

router = APIRouter(
    prefix="/sites",
    tags=["Shelves"],
)

security = HTTPBearer()


def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> int:
    token = credentials.credentials
    return verify_access_token(token)


def check_site_admin_or_staff(
    connection,
    site_id: int,
    user_id: int,
):
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            o.id,
            om.role,
            om.membership_status
        FROM sites s
        JOIN organisations o
            ON s.organisation_id = o.id
        JOIN organisation_members om
            ON om.organisation_id = o.id
        WHERE s.id = %s
          AND om.user_id = %s
          AND om.membership_status = 'APPROVED'
        """,
        (site_id, user_id),
    )

    membership = cursor.fetchone()
    cursor.close()

    if not membership:
        raise HTTPException(
            status_code=403,
            detail="You are not an approved member of this organisation.",
        )

    return membership


# ============================================================
# GET ALL SHELVES
# ============================================================

@router.get("/{site_id}/shelves")
def get_shelves(
    site_id: int,
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
                id,
                site_id,
                name,
                description,
                crop_type,
                device_serial_number,
                image,
                ph_min,
                ph_max,
                ec_min,
                ec_max,
                temperature_min,
                temperature_max,
                orp_min,
                orp_max,
                created_at,
                updated_at
            FROM shelves
            WHERE site_id = %s
            ORDER BY created_at DESC
            """,
            (site_id,),
        )

        rows = cursor.fetchall()

        cursor.close()

        columns = [
            "id",
            "site_id",
            "name",
            "description",
            "crop_type",
            "device_serial_number",
            "image",
            "ph_min",
            "ph_max",
            "ec_min",
            "ec_max",
            "temperature_min",
            "temperature_max",
            "orp_min",
            "orp_max",
            "created_at",
            "updated_at",
        ]

        return [
            dict(zip(columns, row))
            for row in rows
        ]

    finally:
        connection.close()


# ============================================================
# CREATE SHELF
# ============================================================

@router.post("/{site_id}/shelves")
def create_shelf(
    site_id: int,
    name: str = Form(...),
    description: str | None = Form(None),
    crop_type: str = Form(...),
    device_serial_number: str = Form(...),
    image: UploadFile = File(...),
    user_id: int = Depends(get_current_user_id),
):
    connection = get_connection()

    try:
        membership = check_site_admin_or_staff(
            connection,
            site_id,
            user_id,
        )

        # membership is a tuple:
        # (organisation_id, role, membership_status)
        if membership[1] != "ADMIN":
            raise HTTPException(
                status_code=403,
                detail="Only organisation admins can create Shelves.",
            )

        # ----------------------------------------------------
        # Validate required text fields
        # ----------------------------------------------------

        if not name.strip():
            raise HTTPException(
                status_code=400,
                detail="Shelf name is required.",
            )

        if not crop_type.strip():
            raise HTTPException(
                status_code=400,
                detail="Crop type is required.",
            )

        if not device_serial_number.strip():
            raise HTTPException(
                status_code=400,
                detail="Device serial number is required.",
            )

        # ----------------------------------------------------
        # Validate image
        # ----------------------------------------------------

        if image is None:
            raise HTTPException(
                status_code=400,
                detail="Shelf image is required.",
            )

        allowed_types = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }

        if image.content_type not in allowed_types:
            raise HTTPException(
                status_code=400,
                detail="Only JPG, PNG, and WEBP images are allowed.",
            )

        # ----------------------------------------------------
        # Create upload directory
        # ----------------------------------------------------

        upload_directory = "uploads/shelves"

        os.makedirs(
            upload_directory,
            exist_ok=True,
        )

        # ----------------------------------------------------
        # Generate unique image filename
        # ----------------------------------------------------

        extension = allowed_types[image.content_type]

        unique_filename = (
            f"{uuid.uuid4()}{extension}"
        )

        file_path = os.path.join(
            upload_directory,
            unique_filename,
        )

        # ----------------------------------------------------
        # Save image to EC2
        # ----------------------------------------------------

        image_data = image.file.read()

        with open(file_path, "wb") as file:
            file.write(image_data)

        # Path stored in database
        image_url = (
            f"/uploads/shelves/{unique_filename}"
        )

        # ----------------------------------------------------
        # Insert Shelf into database
        # ----------------------------------------------------

        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO shelves (
                site_id,
                name,
                description,
                crop_type,
                device_serial_number,
                image
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING
                id,
                site_id,
                name,
                description,
                crop_type,
                device_serial_number,
                image,
                ph_min,
                ph_max,
                ec_min,
                ec_max,
                temperature_min,
                temperature_max,
                orp_min,
                orp_max,
                created_at,
                updated_at
            """,
            (
                site_id,
                name.strip(),
                description.strip()
                if description
                else None,
                crop_type.strip(),
                device_serial_number.strip(),
                image_url,
            ),
        )

        shelf = cursor.fetchone()

        # ----------------------------------------------------
        # Commit database transaction
        # ----------------------------------------------------

        connection.commit()

        cursor.close()

        # ----------------------------------------------------
        # Convert tuple to dictionary
        # ----------------------------------------------------

        columns = [
            "id",
            "site_id",
            "name",
            "description",
            "crop_type",
            "device_serial_number",
            "image",
            "ph_min",
            "ph_max",
            "ec_min",
            "ec_max",
            "temperature_min",
            "temperature_max",
            "orp_min",
            "orp_max",
            "created_at",
            "updated_at",
        ]

        shelf_data = dict(
            zip(columns, shelf)
        )

        return {
            "message": "Shelf created successfully.",
            "shelf": shelf_data,
        }

    except HTTPException:
        connection.rollback()
        raise

    except Exception as e:
        connection.rollback()

        raise HTTPException(
            status_code=500,
            detail=f"Unable to create Shelf: {str(e)}",
        )

    finally:
        connection.close()


# ============================================================
# GET ONE SHELF
# ============================================================

@router.get("/{site_id}/shelves/{shelf_id}")
def get_shelf(
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
                id,
                site_id,
                name,
                description,
                crop_type,
                device_serial_number,
                image,
                ph_min,
                ph_max,
                ec_min,
                ec_max,
                temperature_min,
                temperature_max,
                orp_min,
                orp_max,
                created_at,
                updated_at
            FROM shelves
            WHERE id = %s
              AND site_id = %s
            """,
            (
                shelf_id,
                site_id,
            ),
        )

        row = cursor.fetchone()

        cursor.close()

        if not row:
            raise HTTPException(
                status_code=404,
                detail="Shelf not found.",
            )

        columns = [
            "id",
            "site_id",
            "name",
            "description",
            "crop_type",
            "device_serial_number",
            "image",
            "ph_min",
            "ph_max",
            "ec_min",
            "ec_max",
            "temperature_min",
            "temperature_max",
            "orp_min",
            "orp_max",
            "created_at",
            "updated_at",
        ]

        return dict(
            zip(columns, row)
        )

    finally:
        connection.close()


# ============================================================
# UPDATE SHELF
# ============================================================

@router.put("/{site_id}/shelves/{shelf_id}")
def update_shelf(
    site_id: int,
    shelf_id: int,
    name: str = Form(...),
    description: str | None = Form(None),
    crop_type: str = Form(...),
    device_serial_number: str = Form(...),
    image: UploadFile | None = File(None),
    user_id: int = Depends(get_current_user_id),
):
    connection = get_connection()

    try:
        membership = check_site_admin_or_staff(
            connection,
            site_id,
            user_id,
        )

        role = membership[1]

        if role != "ADMIN":
            raise HTTPException(
                status_code=403,
                detail="Only organisation admins can update shelves.",
            )

        # -----------------------------
        # Validate normal information
        # -----------------------------

        name = name.strip()
        crop_type = crop_type.strip()
        device_serial_number = device_serial_number.strip()

        if not name:
            raise HTTPException(
                status_code=400,
                detail="Shelf name is required.",
            )

        if not crop_type:
            raise HTTPException(
                status_code=400,
                detail="Crop type is required.",
            )

        if not device_serial_number:
            raise HTTPException(
                status_code=400,
                detail="Device serial number is required.",
            )

        cursor = connection.cursor()

        # -----------------------------
        # Get current shelf
        # -----------------------------

        cursor.execute(
            """
            SELECT image
            FROM shelves
            WHERE id = %s
              AND site_id = %s
            """,
            (
                shelf_id,
                site_id,
            ),
        )

        existing_shelf = cursor.fetchone()

        if not existing_shelf:
            cursor.close()

            raise HTTPException(
                status_code=404,
                detail="Shelf not found.",
            )

        old_image = existing_shelf[0]

        # -----------------------------
        # Handle optional new image
        # -----------------------------

        new_image_url = old_image
        new_image_path = None

        if image is not None:

            allowed_types = {
                "image/jpeg": ".jpg",
                "image/png": ".png",
                "image/webp": ".webp",
            }

            if image.content_type not in allowed_types:
                cursor.close()

                raise HTTPException(
                    status_code=400,
                    detail="Only JPG, PNG, and WEBP images are allowed.",
                )

            upload_directory = "uploads/shelves"

            os.makedirs(
                upload_directory,
                exist_ok=True,
            )

            extension = allowed_types[image.content_type]

            unique_filename = f"{uuid.uuid4()}{extension}"

            new_image_path = os.path.join(
                upload_directory,
                unique_filename,
            )

            image_data = image.file.read()

            with open(new_image_path, "wb") as file:
                file.write(image_data)

            new_image_url = f"/uploads/shelves/{unique_filename}"

        # -----------------------------
        # Update database
        # -----------------------------

        cursor.execute(
            """
            UPDATE shelves
            SET
                name = %s,
                description = %s,
                crop_type = %s,
                device_serial_number = %s,
                image = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
              AND site_id = %s
            RETURNING
                id,
                site_id,
                name,
                description,
                crop_type,
                device_serial_number,
                image,
                ph_min,
                ph_max,
                ec_min,
                ec_max,
                temperature_min,
                temperature_max,
                orp_min,
                orp_max,
                created_at,
                updated_at
            """,
            (
                name,
                description.strip() if description else None,
                crop_type,
                device_serial_number,
                new_image_url,
                shelf_id,
                site_id,
            ),
        )

        row = cursor.fetchone()

        connection.commit()

        cursor.close()

        # -----------------------------
        # Delete old image
        # -----------------------------

        if (
            image is not None
            and old_image
            and old_image != new_image_url
        ):
            old_image_file = old_image.lstrip("/")

            if os.path.exists(old_image_file):
                os.remove(old_image_file)

        # -----------------------------
        # Convert result to dictionary
        # -----------------------------

        columns = [
            "id",
            "site_id",
            "name",
            "description",
            "crop_type",
            "device_serial_number",
            "image",
            "ph_min",
            "ph_max",
            "ec_min",
            "ec_max",
            "temperature_min",
            "temperature_max",
            "orp_min",
            "orp_max",
            "created_at",
            "updated_at",
        ]

        return dict(
            zip(columns, row)
        )

    except HTTPException:
        connection.rollback()
        raise

    except Exception as e:
        connection.rollback()

        raise HTTPException(
            status_code=500,
            detail=f"Unable to update Shelf: {str(e)}",
        )

    finally:
        connection.close()

@router.put("/{site_id}/shelves/{shelf_id}/thresholds")
def update_shelf_thresholds(
    site_id: int,
    shelf_id: int,
    request: UpdateShelfThresholdRequest,
    user_id: int = Depends(get_current_user_id),
):
    connection = get_connection()

    try:
        membership = check_site_admin_or_staff(
            connection,
            site_id,
            user_id,
        )

        # -----------------------------
        # Validate threshold ranges
        # -----------------------------

        if request.ph_min >= request.ph_max:
            raise HTTPException(
                status_code=400,
                detail="pH minimum must be lower than pH maximum.",
            )

        if request.ec_min >= request.ec_max:
            raise HTTPException(
                status_code=400,
                detail="EC minimum must be lower than EC maximum.",
            )

        if request.orp_min >= request.orp_max:
            raise HTTPException(
                status_code=400,
                detail="ORP minimum must be lower than ORP maximum.",
            )

        if request.temperature_min >= request.temperature_max:
            raise HTTPException(
                status_code=400,
                detail="Temperature minimum must be lower than temperature maximum.",
            )

        # -----------------------------
        # Update thresholds
        # -----------------------------

        cursor = connection.cursor()

        cursor.execute(
            """
            UPDATE shelves
            SET
                ph_min = %s,
                ph_max = %s,
                ec_min = %s,
                ec_max = %s,
                orp_min = %s,
                orp_max = %s,
                temperature_min = %s,
                temperature_max = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
              AND site_id = %s
            RETURNING
                id,
                site_id,
                name,
                ph_min,
                ph_max,
                ec_min,
                ec_max,
                orp_min,
                orp_max,
                temperature_min,
                temperature_max,
                updated_at
            """,
            (
                request.ph_min,
                request.ph_max,
                request.ec_min,
                request.ec_max,
                request.orp_min,
                request.orp_max,
                request.temperature_min,
                request.temperature_max,
                shelf_id,
                site_id,
            ),
        )

        row = cursor.fetchone()

        if not row:
            connection.rollback()
            cursor.close()

            raise HTTPException(
                status_code=404,
                detail="Shelf not found.",
            )

        connection.commit()

        cursor.close()

        columns = [
            "id",
            "site_id",
            "name",
            "ph_min",
            "ph_max",
            "ec_min",
            "ec_max",
            "orp_min",
            "orp_max",
            "temperature_min",
            "temperature_max",
            "updated_at",
        ]

        return {
            "message": "Shelf thresholds updated successfully.",
            "shelf": dict(
                zip(columns, row)
            ),
        }

    except HTTPException:
        connection.rollback()
        raise

    except Exception as e:
        connection.rollback()

        raise HTTPException(
            status_code=500,
            detail=f"Unable to update Shelf thresholds: {str(e)}",
        )

    finally:
        connection.close()
# ============================================================
# UPDATE DEVICE SERIAL NUMBER
# ============================================================

@router.put("/{site_id}/shelves/{shelf_id}/device")
def update_device_serial_number(
    site_id: int,
    shelf_id: int,
    device_serial_number: str,
    user_id: int = Depends(get_current_user_id),
):
    connection = get_connection()

    try:
        # ----------------------------------------------------
        # Check organisation membership
        # ----------------------------------------------------

        membership = check_site_admin_or_staff(
            connection,
            site_id,
            user_id,
        )

        # ----------------------------------------------------
        # Admin only
        # ----------------------------------------------------

        if membership[1] != "ADMIN":
            raise HTTPException(
                status_code=403,
                detail="Only organisation admins can manage devices.",
            )

        # ----------------------------------------------------
        # Validate serial number
        # ----------------------------------------------------

        device_serial_number = device_serial_number.strip()

        if not device_serial_number:
            raise HTTPException(
                status_code=400,
                detail="Device serial number is required.",
            )

        cursor = connection.cursor()

        # ----------------------------------------------------
        # Check that shelf exists
        # ----------------------------------------------------

        cursor.execute(
            """
            SELECT
                id,
                site_id,
                name,
                device_serial_number
            FROM shelves
            WHERE id = %s
              AND site_id = %s
            """,
            (
                shelf_id,
                site_id,
            ),
        )

        shelf = cursor.fetchone()

        if not shelf:
            cursor.close()

            raise HTTPException(
                status_code=404,
                detail="Shelf not found.",
            )

        # ----------------------------------------------------
        # Update device serial number
        # ----------------------------------------------------

        cursor.execute(
            """
            UPDATE shelves
            SET
                device_serial_number = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
              AND site_id = %s
            RETURNING
                id,
                site_id,
                name,
                device_serial_number,
                updated_at
            """,
            (
                device_serial_number,
                shelf_id,
                site_id,
            ),
        )

        updated_shelf = cursor.fetchone()

        connection.commit()

        cursor.close()

        # ----------------------------------------------------
        # Return updated device information
        # ----------------------------------------------------

        return {
            "message": "Device serial number updated successfully.",
            "device": {
                "shelf_id": updated_shelf[0],
                "site_id": updated_shelf[1],
                "shelf_name": updated_shelf[2],
                "device_serial_number": updated_shelf[3],
                "updated_at": updated_shelf[4],
            },
        }

    except HTTPException:
        connection.rollback()
        raise

    except Exception as e:
        connection.rollback()

        raise HTTPException(
            status_code=500,
            detail=f"Unable to update device serial number: {str(e)}",
        )

    finally:
        connection.close()
        
# ============================================================
# DELETE SHELF
# ============================================================

@router.delete("/{site_id}/shelves/{shelf_id}")
def delete_shelf(
    site_id: int,
    shelf_id: int,
    user_id: int = Depends(get_current_user_id),
):
    connection = get_connection()

    try:
        membership = check_site_admin_or_staff(
            connection,
            site_id,
            user_id,
        )

        role = membership[1]

        if role != "ADMIN":
            raise HTTPException(
                status_code=403,
                detail="Only organisation admins can delete shelves.",
            )

        cursor = connection.cursor()

        cursor.execute(
            """
            DELETE FROM shelves
            WHERE id = %s
              AND site_id = %s
            RETURNING id
            """,
            (
                shelf_id,
                site_id,
            ),
        )

        row = cursor.fetchone()

        if not row:
            connection.rollback()

            cursor.close()

            raise HTTPException(
                status_code=404,
                detail="Shelf not found.",
            )

        connection.commit()

        cursor.close()

        return {
            "message": "Shelf deleted successfully."
        }

    except HTTPException:
        raise

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


