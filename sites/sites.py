import os
from fastapi import APIRouter, Depends, HTTPException, File, Form, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from database.connection import get_connection
from auth.security import verify_access_token
from sites.schemas import CreateSiteRequest

router = APIRouter(
    prefix="/organisations",
    tags=["Sites"],
)

security = HTTPBearer()


def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> int:
    token = credentials.credentials
    return verify_access_token(token)


@router.get("/{organisation_id}/sites")
def get_sites(
    organisation_id: int,
    user_id: int = Depends(get_current_user_id),
):
    conn = get_connection()

    try:
        with conn.cursor() as cursor:

            # Check whether the user belongs to this organisation
            cursor.execute(
                """
                SELECT role, membership_status
                FROM organisation_members
                WHERE organisation_id = %s
                  AND user_id = %s
                """,
                (organisation_id, user_id),
            )

            membership = cursor.fetchone()

            if not membership:
                raise HTTPException(
                    status_code=403,
                    detail="You are not a member of this organisation.",
                )

            role, membership_status = membership

            if membership_status != "APPROVED":
                raise HTTPException(
                    status_code=403,
                    detail="Your organisation membership is not approved.",
                )

            # Get all sites belonging to this organisation
            cursor.execute(
                """
                SELECT
                    id,
                    organisation_id,
                    name,
                    description,
                    image,
                    created_at,
                    updated_at
                FROM sites
                WHERE organisation_id = %s
                ORDER BY created_at DESC
                """,
                (organisation_id,),
            )

            rows = cursor.fetchall()

            sites = []

            for row in rows:
                sites.append(
                    {
                        "id": row[0],
                        "organisation_id": row[1],
                        "name": row[2],
                        "description": row[3],
                        "image": row[4],
                        "created_at": row[5].isoformat(),
                        "updated_at": row[6].isoformat(),
                    }
                )

            return {
                "sites": sites
            }

    finally:
        conn.close()


@router.post("/{organisation_id}/sites")
def create_site(
    organisation_id: int,
    name: str = Form(...),
    description: str | None = Form(None),
    image: UploadFile = File(...),
    user_id: int = Depends(get_current_user_id),
):
    conn = get_connection()

    try:
        with conn.cursor() as cursor:

            # ============================================================
            # CHECK MEMBERSHIP
            # ============================================================

            cursor.execute(
                """
                SELECT role, membership_status
                FROM organisation_members
                WHERE organisation_id = %s
                  AND user_id = %s
                """,
                (organisation_id, user_id),
            )

            membership = cursor.fetchone()

            if not membership:
                raise HTTPException(
                    status_code=403,
                    detail="You are not a member of this organisation.",
                )

            role, membership_status = membership

            if membership_status != "APPROVED":
                raise HTTPException(
                    status_code=403,
                    detail="Your organisation membership is not approved.",
                )

            if role != "ADMIN":
                raise HTTPException(
                    status_code=403,
                    detail="Only organisation admins can create sites.",
                )

            # ============================================================
            # VALIDATE SITE NAME
            # ============================================================

            site_name = name.strip()

            if not site_name:
                raise HTTPException(
                    status_code=400,
                    detail="Site name is required.",
                )

            # ============================================================
            # VALIDATE IMAGE TYPE
            # ============================================================

            allowed_content_types = {
                "image/jpeg",
                "image/png",
                "image/webp",
            }

            if image.content_type not in allowed_content_types:
                raise HTTPException(
                    status_code=400,
                    detail="Only JPG, PNG, and WEBP images are allowed.",
                )

            # ============================================================
            # CREATE UPLOAD DIRECTORY
            # ============================================================

            import os
            import uuid

            upload_directory = "uploads/sites"

            os.makedirs(upload_directory, exist_ok=True)

            # ============================================================
            # CREATE UNIQUE FILE NAME
            # ============================================================

            extension = os.path.splitext(image.filename or "")[1].lower()

            if not extension:
                extension = ".jpg"

            unique_filename = f"{uuid.uuid4().hex}{extension}"

            file_path = os.path.join(
                upload_directory,
                unique_filename,
            )

            # ============================================================
            # SAVE IMAGE TO EC2
            # ============================================================

            image_data = image.file.read()

            with open(file_path, "wb") as file:
                file.write(image_data)

            # ============================================================
            # IMAGE PATH FOR DATABASE
            # ============================================================

            image_path = f"/uploads/sites/{unique_filename}"

            # ============================================================
            # INSERT SITE INTO DATABASE
            # ============================================================

            cursor.execute(
                """
                INSERT INTO sites (
                    organisation_id,
                    name,
                    description,
                    image
                )
                VALUES (%s, %s, %s, %s)
                RETURNING
                    id,
                    organisation_id,
                    name,
                    description,
                    image,
                    created_at,
                    updated_at
                """,
                (
                    organisation_id,
                    site_name,
                    description.strip() if description else None,
                    image_path,
                ),
            )

            row = cursor.fetchone()

            conn.commit()

            # ============================================================
            # RETURN CREATED SITE
            # ============================================================

            return {
                "message": "Site created successfully.",
                "site": {
                    "id": row[0],
                    "organisation_id": row[1],
                    "name": row[2],
                    "description": row[3],
                    "image": row[4],
                    "created_at": row[5].isoformat(),
                    "updated_at": row[6].isoformat(),
                },
            }

    finally:
        conn.close()


@router.put("/{organisation_id}/sites/{site_id}")
def update_site(
    organisation_id: int,
    site_id: int,
    name: str = Form(...),
    description: str | None = Form(None),
    image: UploadFile | None = File(None),
    user_id: int = Depends(get_current_user_id),
):
    conn = get_connection()

    old_image_path = None
    new_image_path = None

    try:
        with conn.cursor() as cursor:

            # ============================================================
            # CHECK MEMBERSHIP
            # ============================================================

            cursor.execute(
                """
                SELECT role, membership_status
                FROM organisation_members
                WHERE organisation_id = %s
                  AND user_id = %s
                """,
                (organisation_id, user_id),
            )

            membership = cursor.fetchone()

            if not membership:
                raise HTTPException(
                    status_code=403,
                    detail="You are not a member of this organisation.",
                )

            role, membership_status = membership

            if membership_status != "APPROVED":
                raise HTTPException(
                    status_code=403,
                    detail="Your organisation membership is not approved.",
                )

            if role != "ADMIN":
                raise HTTPException(
                    status_code=403,
                    detail="Only organisation admins can edit sites.",
                )

            # ============================================================
            # GET EXISTING SITE
            # ============================================================

            cursor.execute(
                """
                SELECT id, name, description, image
                FROM sites
                WHERE id = %s
                  AND organisation_id = %s
                """,
                (site_id, organisation_id),
            )

            site = cursor.fetchone()

            if not site:
                raise HTTPException(
                    status_code=404,
                    detail="Site not found.",
                )

            old_image_path = site[3]

            # ============================================================
            # VALIDATE SITE NAME
            # ============================================================

            site_name = name.strip()

            if not site_name:
                raise HTTPException(
                    status_code=400,
                    detail="Site name is required.",
                )

            # ============================================================
            # HANDLE NEW IMAGE
            # ============================================================

            if image is not None:

                allowed_content_types = {
                    "image/jpeg",
                    "image/png",
                    "image/webp",
                }

                if image.content_type not in allowed_content_types:
                    raise HTTPException(
                        status_code=400,
                        detail="Only JPG, PNG, and WEBP images are allowed.",
                    )

                import os
                import uuid

                upload_directory = "uploads/sites"

                os.makedirs(upload_directory, exist_ok=True)

                extension = os.path.splitext(
                    image.filename or ""
                )[1].lower()

                if not extension:
                    extension = ".jpg"

                unique_filename = f"{uuid.uuid4().hex}{extension}"

                file_path = os.path.join(
                    upload_directory,
                    unique_filename,
                )

                image_data = image.file.read()

                with open(file_path, "wb") as file:
                    file.write(image_data)

                new_image_path = f"/uploads/sites/{unique_filename}"

            else:
                new_image_path = old_image_path

            # ============================================================
            # UPDATE DATABASE
            # ============================================================

            cursor.execute(
                """
                UPDATE sites
                SET
                    name = %s,
                    description = %s,
                    image = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND organisation_id = %s
                RETURNING
                    id,
                    organisation_id,
                    name,
                    description,
                    image,
                    created_at,
                    updated_at
                """,
                (
                    site_name,
                    description.strip() if description else None,
                    new_image_path,
                    site_id,
                    organisation_id,
                ),
            )

            row = cursor.fetchone()

            conn.commit()

            # ============================================================
            # DELETE OLD IMAGE AFTER SUCCESSFUL DATABASE UPDATE
            # ============================================================

            if (
                image is not None
                and old_image_path
                and old_image_path != new_image_path
            ):
                import os

                old_file_path = old_image_path.lstrip("/")

                if os.path.exists(old_file_path):
                    os.remove(old_file_path)

            return {
                "message": "Site updated successfully.",
                "site": {
                    "id": row[0],
                    "organisation_id": row[1],
                    "name": row[2],
                    "description": row[3],
                    "image": row[4],
                    "created_at": row[5].isoformat(),
                    "updated_at": row[6].isoformat(),
                },
            }

    except Exception:
        # If a new image was saved but something failed,
        # remove the new file so it does not become an orphan.
        if new_image_path:
            import os

            new_file_path = new_image_path.lstrip("/")

            if os.path.exists(new_file_path):
                os.remove(new_file_path)

        conn.rollback()
        raise

    finally:
        conn.close()

@router.put("/{organisation_id}/sites/{site_id}")
def update_site(
    organisation_id: int,
    site_id: int,
    name: str = Form(...),
    description: str | None = Form(None),
    image: UploadFile | None = File(None),
    user_id: int = Depends(get_current_user_id),
):
    conn = get_connection()

    old_image_path = None
    new_image_path = None

    try:
        with conn.cursor() as cursor:

            # ============================================================
            # CHECK MEMBERSHIP
            # ============================================================

            cursor.execute(
                """
                SELECT role, membership_status
                FROM organisation_members
                WHERE organisation_id = %s
                  AND user_id = %s
                """,
                (organisation_id, user_id),
            )

            membership = cursor.fetchone()

            if not membership:
                raise HTTPException(
                    status_code=403,
                    detail="You are not a member of this organisation.",
                )

            role, membership_status = membership

            if membership_status != "APPROVED":
                raise HTTPException(
                    status_code=403,
                    detail="Your organisation membership is not approved.",
                )

            if role != "ADMIN":
                raise HTTPException(
                    status_code=403,
                    detail="Only organisation admins can edit sites.",
                )

            # ============================================================
            # GET EXISTING SITE
            # ============================================================

            cursor.execute(
                """
                SELECT id, name, description, image
                FROM sites
                WHERE id = %s
                  AND organisation_id = %s
                """,
                (site_id, organisation_id),
            )

            site = cursor.fetchone()

            if not site:
                raise HTTPException(
                    status_code=404,
                    detail="Site not found.",
                )

            old_image_path = site[3]

            # ============================================================
            # VALIDATE SITE NAME
            # ============================================================

            site_name = name.strip()

            if not site_name:
                raise HTTPException(
                    status_code=400,
                    detail="Site name is required.",
                )

            # ============================================================
            # HANDLE NEW IMAGE
            # ============================================================

            if image is not None:

                allowed_content_types = {
                    "image/jpeg",
                    "image/png",
                    "image/webp",
                }

                if image.content_type not in allowed_content_types:
                    raise HTTPException(
                        status_code=400,
                        detail="Only JPG, PNG, and WEBP images are allowed.",
                    )

                import os
                import uuid

                upload_directory = "uploads/sites"

                os.makedirs(upload_directory, exist_ok=True)

                extension = os.path.splitext(
                    image.filename or ""
                )[1].lower()

                if not extension:
                    extension = ".jpg"

                unique_filename = f"{uuid.uuid4().hex}{extension}"

                file_path = os.path.join(
                    upload_directory,
                    unique_filename,
                )

                image_data = image.file.read()

                with open(file_path, "wb") as file:
                    file.write(image_data)

                new_image_path = f"/uploads/sites/{unique_filename}"

            else:
                new_image_path = old_image_path

            # ============================================================
            # UPDATE DATABASE
            # ============================================================

            cursor.execute(
                """
                UPDATE sites
                SET
                    name = %s,
                    description = %s,
                    image = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND organisation_id = %s
                RETURNING
                    id,
                    organisation_id,
                    name,
                    description,
                    image,
                    created_at,
                    updated_at
                """,
                (
                    site_name,
                    description.strip() if description else None,
                    new_image_path,
                    site_id,
                    organisation_id,
                ),
            )

            row = cursor.fetchone()

            conn.commit()

            # ============================================================
            # DELETE OLD IMAGE AFTER SUCCESSFUL DATABASE UPDATE
            # ============================================================

            if (
                image is not None
                and old_image_path
                and old_image_path != new_image_path
            ):
                import os

                old_file_path = old_image_path.lstrip("/")

                if os.path.exists(old_file_path):
                    os.remove(old_file_path)

            return {
                "message": "Site updated successfully.",
                "site": {
                    "id": row[0],
                    "organisation_id": row[1],
                    "name": row[2],
                    "description": row[3],
                    "image": row[4],
                    "created_at": row[5].isoformat(),
                    "updated_at": row[6].isoformat(),
                },
            }

    except Exception:
        # If a new image was saved but something failed,
        # remove the new file so it does not become an orphan.
        if new_image_path:
            import os

            new_file_path = new_image_path.lstrip("/")

            if os.path.exists(new_file_path):
                os.remove(new_file_path)

        conn.rollback()
        raise

    finally:
        conn.close()

@router.delete("/{organisation_id}/sites/{site_id}")
def delete_site(
    organisation_id: int,
    site_id: int,
    user_id: int = Depends(get_current_user_id),
):
    conn = get_connection()
    cursor = conn.cursor()

    try:
        # Check that the user is an approved member and Admin
        cursor.execute(
            """
            SELECT role, membership_status
            FROM organisation_members
            WHERE organisation_id = %s
              AND user_id = %s
            """,
            (organisation_id, user_id),
        )

        membership = cursor.fetchone()

        if not membership:
            raise HTTPException(
                status_code=403,
                detail="You are not a member of this organisation.",
            )

        role, membership_status = membership

        if membership_status != "APPROVED":
            raise HTTPException(
                status_code=403,
                detail="Your membership is not approved.",
            )

        if role != "ADMIN":
            raise HTTPException(
                status_code=403,
                detail="Only Admins can delete Sites.",
            )

        # Get the site
        cursor.execute(
            """
            SELECT image
            FROM sites
            WHERE id = %s
              AND organisation_id = %s
            """,
            (site_id, organisation_id),
        )

        site = cursor.fetchone()

        if not site:
            raise HTTPException(
                status_code=404,
                detail="Site not found.",
            )

        image_path = site[0]

        # Delete the site from PostgreSQL
        cursor.execute(
            """
            DELETE FROM sites
            WHERE id = %s
              AND organisation_id = %s
            """,
            (site_id, organisation_id),
        )

        conn.commit()

        # Delete the image file from EC2
        if image_path:
            full_image_path = image_path.lstrip("/")

            if os.path.exists(full_image_path):
                os.remove(full_image_path)

        return {
            "message": "Site deleted successfully."
        }

    except HTTPException:
        conn.rollback()
        raise

    except Exception as e:
        conn.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Unable to delete Site: {str(e)}",
        )

    finally:
        cursor.close()
        conn.close()