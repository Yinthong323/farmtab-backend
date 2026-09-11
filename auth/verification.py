from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database.connection import get_connection


router = APIRouter(prefix="/auth", tags=["Authentication"])


class VerificationRequest(BaseModel):
    email: str
    code: str


@router.post("/verify")
def verify_email(request: VerificationRequest):

    conn = get_connection()

    try:
        with conn.cursor() as cursor:

            # Find the user by email
            cursor.execute(
                """
                SELECT id, is_verified, verification_code,
                       verification_expires_at
                FROM users
                WHERE email = %s
                """,
                (request.email,),
            )

            user = cursor.fetchone()

            if not user:
                raise HTTPException(
                    status_code=404,
                    detail="User not found."
                )

            user_id, is_verified, verification_code, expires_at = user

            # Check if email is already verified
            if is_verified:
                raise HTTPException(
                    status_code=400,
                    detail="Email is already verified."
                )

            # Check verification code
            if verification_code != request.code:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid verification code."
                )

            # Check whether the code has expired
            if expires_at is None or expires_at <= datetime.now(timezone.utc):
                raise HTTPException(
                    status_code=400,
                    detail="Verification code has expired."
                )

            # Mark email as verified
            cursor.execute(
                """
                UPDATE users
                SET is_verified = TRUE,
                    verification_code = NULL,
                    verification_expires_at = NULL,
                    verification_sent_at = NULL
                WHERE id = %s
                """,
                (user_id,),
            )

            conn.commit()

    finally:
        conn.close()

    return {
        "message": "Email verified successfully."
    }
