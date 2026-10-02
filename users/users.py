import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel

from database.connection import get_connection
from auth.dependencies import get_current_user_id
from auth.security import verify_password, hash_password
from users.schemas import UserProfileResponse, UpdateProfileRequest

router = APIRouter(
    prefix="/users",
    tags=["Users"],
)


# ============================================================
# GET CURRENT USER
# ============================================================

@router.get("/me", response_model=UserProfileResponse)
def get_current_user(
    user_id: int = Depends(get_current_user_id),
):
    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                id,
                username,
                email,
                is_verified,
                created_at,
                profile_image
            FROM users
            WHERE id = %s
            """,
            (user_id,),
        )

        user = cursor.fetchone()

        cursor.close()

        if not user:
            raise HTTPException(
                status_code=404,
                detail="User not found.",
            )

        return {
            "id": user[0],
            "username": user[1],
            "email": user[2],
            "is_verified": user[3],
            "created_at": user[4].isoformat(),
            "profile_image": user[5],
        }

    finally:
        connection.close()

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

@router.post("/me/change-password")
def change_password(
    request: ChangePasswordRequest,
    user_id: int = Depends(get_current_user_id),
):
    conn = get_connection()

    try:
        cursor = conn.cursor()

        cursor.execute(
            "SELECT password_hash FROM users WHERE id = %s",
            (user_id,),
        )

        user = cursor.fetchone()

        if not user:
            raise HTTPException(
                status_code=404,
                detail="User not found.",
            )

        current_password_hash = user[0]

        if not verify_password(
            request.current_password,
            current_password_hash,
        ):
            raise HTTPException(
                status_code=400,
                detail="Current password is incorrect.",
            )

        if len(request.new_password) < 8:
            raise HTTPException(
                status_code=400,
                detail="New password must be at least 8 characters.",
            )

        if request.current_password == request.new_password:
            raise HTTPException(
                status_code=400,
                detail="New password must be different from the current password.",
            )

        new_password_hash = hash_password(request.new_password)

        cursor.execute(
            """
            UPDATE users
            SET password_hash = %s
            WHERE id = %s
            """,
            (new_password_hash, user_id),
        )

        conn.commit()

        return {
            "message": "Password changed successfully."
        }

    finally:
        cursor.close()
        conn.close()

# ============================================================
# CHANGE PASSWORD
# ============================================================

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.post("/me/change-password")
def change_password(
    request: ChangePasswordRequest,
    user_id: int = Depends(get_current_user_id),
):
    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT password_hash
            FROM users
            WHERE id = %s
            """,
            (user_id,),
        )

        user = cursor.fetchone()

        if not user:
            raise HTTPException(
                status_code=404,
                detail="User not found.",
            )

        current_password_hash = user[0]

        # Verify current password
        if not verify_password(
            request.current_password,
            current_password_hash,
        ):
            raise HTTPException(
                status_code=400,
                detail="Current password is incorrect.",
            )

        # Validate new password
        if len(request.new_password) < 8:
            raise HTTPException(
                status_code=400,
                detail="New password must be at least 8 characters.",
            )

        if request.current_password == request.new_password:
            raise HTTPException(
                status_code=400,
                detail="New password must be different from the current password.",
            )

        # Hash new password
        new_password_hash = hash_password(
            request.new_password
        )

        cursor.execute(
            """
            UPDATE users
            SET password_hash = %s
            WHERE id = %s
            """,
            (
                new_password_hash,
                user_id,
            ),
        )

        connection.commit()
        cursor.close()

        return {
            "message": "Password changed successfully."
        }

    except HTTPException:
        connection.rollback()
        raise

    except Exception as e:
        connection.rollback()

        raise HTTPException(
            status_code=500,
            detail=f"Unable to change password: {str(e)}",
        )

    finally:
        connection.close()

# ============================================================
# DELETE ACCOUNT
# ============================================================

@router.delete("/me")
def delete_account(
    user_id: int = Depends(get_current_user_id),
):
    connection = get_connection()

    try:
        cursor = connection.cursor()

        # ----------------------------------------------------
        # Check whether the user is the only approved ADMIN
        # of any organisation.
        # ----------------------------------------------------
        cursor.execute(
            """
            SELECT
                om.organisation_id,
                o.name
            FROM organisation_members om
            JOIN organisations o
                ON o.id = om.organisation_id
            WHERE om.user_id = %s
              AND om.role = 'ADMIN'
              AND om.membership_status = 'APPROVED'
              AND NOT EXISTS (
                  SELECT 1
                  FROM organisation_members other_admin
                  WHERE other_admin.organisation_id = om.organisation_id
                    AND other_admin.user_id != %s
                    AND other_admin.role = 'ADMIN'
                    AND other_admin.membership_status = 'APPROVED'
              )
            """,
            (user_id, user_id),
        )

        admin_organisations = cursor.fetchall()

        if admin_organisations:
            organisation_names = [
                row[1] for row in admin_organisations
            ]

            raise HTTPException(
                status_code=400,
                detail=(
                    "You cannot delete your account because you are "
                    "the only approved ADMIN of: "
                    + ", ".join(organisation_names)
                    + ". Please assign another approved ADMIN first."
                ),
            )

        # ----------------------------------------------------
        # Delete the user.
        #
        # organisation_members.user_id has ON DELETE CASCADE,
        # so the user's membership records will automatically
        # be removed.
        # ----------------------------------------------------
        cursor.execute(
            """
            DELETE FROM users
            WHERE id = %s
            RETURNING id
            """,
            (user_id,),
        )

        deleted_user = cursor.fetchone()

        if not deleted_user:
            raise HTTPException(
                status_code=404,
                detail="User not found.",
            )

        connection.commit()

        cursor.close()

        return {
            "message": "Account deleted successfully."
        }

    except HTTPException:
        connection.rollback()
        raise

    except Exception as e:
        connection.rollback()

        raise HTTPException(
            status_code=500,
            detail=f"Unable to delete account: {str(e)}",
        )

    finally:
        connection.close()

# ============================================================
# UPDATE PROFILE
# ============================================================

@router.put("/me/profile")
def update_profile(
    request: UpdateProfileRequest,
    user_id: int = Depends(get_current_user_id),
):
    username = request.username.strip()

    if not username:
        raise HTTPException(
            status_code=400,
            detail="Username is required.",
        )

    if len(username) > 50:
        raise HTTPException(
            status_code=400,
            detail="Username must not exceed 50 characters.",
        )

    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT id
            FROM users
            WHERE LOWER(username) = LOWER(%s)
              AND id != %s
            """,
            (username, user_id),
        )

        existing_user = cursor.fetchone()

        if existing_user:
            cursor.close()

            raise HTTPException(
                status_code=409,
                detail="Username is already taken.",
            )

        cursor.execute(
            """
            UPDATE users
            SET username = %s
            WHERE id = %s
            RETURNING
                id,
                username,
                email,
                is_verified,
                created_at,
                profile_image
            """,
            (username, user_id),
        )

        user = cursor.fetchone()

        if not user:
            connection.rollback()
            cursor.close()

            raise HTTPException(
                status_code=404,
                detail="User not found.",
            )

        connection.commit()
        cursor.close()

        return {
            "message": "Profile updated successfully.",
            "user": {
                "id": user[0],
                "username": user[1],
                "email": user[2],
                "is_verified": user[3],
                "created_at": user[4].isoformat(),
                "profile_image": user[5],
            },
        }

    except HTTPException:
        connection.rollback()
        raise

    except Exception as e:
        connection.rollback()

        raise HTTPException(
            status_code=500,
            detail=f"Unable to update profile: {str(e)}",
        )

    finally:
        connection.close()


# ============================================================
# UPLOAD PROFILE IMAGE
# ============================================================

@router.post("/me/profile-image")
def upload_profile_image(
    file: UploadFile = File(...),
    user_id: int = Depends(get_current_user_id),
):
    allowed_types = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }

    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail="Only JPG, PNG, and WEBP images are allowed.",
        )

    extension = allowed_types[file.content_type]

    filename = f"{uuid.uuid4()}{extension}"

    upload_directory = "uploads/users"

    os.makedirs(upload_directory, exist_ok=True)

    file_path = os.path.join(
        upload_directory,
        filename,
    )

    try:
        image_data = file.file.read()

        with open(file_path, "wb") as image_file:
            image_file.write(image_data)

        image_url = f"/uploads/users/{filename}"

        connection = get_connection()

        try:
            cursor = connection.cursor()

            cursor.execute(
                """
                SELECT profile_image
                FROM users
                WHERE id = %s
                """,
                (user_id,),
            )

            user = cursor.fetchone()

            if not user:
                cursor.close()
                connection.close()

                os.remove(file_path)

                raise HTTPException(
                    status_code=404,
                    detail="User not found.",
                )

            old_profile_image = user[0]

            cursor.execute(
                """
                UPDATE users
                SET profile_image = %s
                WHERE id = %s
                RETURNING
                    id,
                    username,
                    email,
                    is_verified,
                    created_at,
                    profile_image
                """,
                (image_url, user_id),
            )

            updated_user = cursor.fetchone()

            connection.commit()
            cursor.close()

        except Exception:
            connection.rollback()
            cursor.close()
            connection.close()

            if os.path.exists(file_path):
                os.remove(file_path)

            raise

        finally:
            try:
                connection.close()
            except Exception:
                pass

        # Delete the old image only after the new image
        # has successfully been saved to the database.
        if old_profile_image:
            old_file_path = old_profile_image.lstrip("/")

            if os.path.exists(old_file_path):
                try:
                    os.remove(old_file_path)
                except Exception:
                    pass

        return {
            "message": "Profile image uploaded successfully.",
            "user": {
                "id": updated_user[0],
                "username": updated_user[1],
                "email": updated_user[2],
                "is_verified": updated_user[3],
                "created_at": updated_user[4].isoformat(),
                "profile_image": updated_user[5],
            },
        }

    except HTTPException:
        raise

    except Exception as e:
        if os.path.exists(file_path):
            os.remove(file_path)

        raise HTTPException(
            status_code=500,
            detail=f"Unable to upload profile image: {str(e)}",
        )