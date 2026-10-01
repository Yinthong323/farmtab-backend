import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File

from database.connection import get_connection
from auth.dependencies import get_current_user_id

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