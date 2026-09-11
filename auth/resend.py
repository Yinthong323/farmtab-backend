import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database.connection import get_connection
from services.email_service import send_email


router = APIRouter(prefix="/auth", tags=["Authentication"])


class ResendCodeRequest(BaseModel):
    email: str


@router.post("/resend-code")
async def resend_code(request: ResendCodeRequest):

    conn = get_connection()

    try:
        with conn.cursor() as cursor:

            # 1. Find the user
            cursor.execute(
                """
                SELECT id, username, is_verified, verification_sent_at
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

            user_id, username, is_verified, verification_sent_at = user

            # 2. Don't resend if email is already verified
            if is_verified:
                raise HTTPException(
                    status_code=400,
                    detail="Email is already verified."
                )

            # 3. Check the 60-second cooldown
            if verification_sent_at is not None:
                seconds_since_last_send = (
                    datetime.now(timezone.utc) - verification_sent_at
                ).total_seconds()

                if seconds_since_last_send < 60:
                    remaining_seconds = 60 - int(seconds_since_last_send)

                    raise HTTPException(
                        status_code=429,
                        detail=f"Please wait {remaining_seconds} seconds "
                               "before requesting another code."
                    )

            # 4. Generate a new 6-digit code
            verification_code = f"{secrets.randbelow(1_000_000):06d}"

            # 5. Set a new 10-minute expiry
            now = datetime.now(timezone.utc)

            verification_expires_at = now + timedelta(minutes=10)

            # 6. Replace the old code
            cursor.execute(
                """
                UPDATE users
                SET verification_code = %s,
                    verification_expires_at = %s,
                    verification_sent_at = %s
                WHERE id = %s
                """,
                (
                    verification_code,
                    verification_expires_at,
                    now,
                    user_id,
                ),
            )

            conn.commit()

    finally:
        conn.close()

    # 7. Send the new verification email
    await send_email(
        to_email=request.email,
        subject="FarmTab New Verification Code",
        body=(
            f"Hello {username},\n\n"
            f"Your new FarmTab verification code is:\n\n"
            f"{verification_code}\n\n"
            f"This code will expire in 10 minutes.\n\n"
            f"If you did not request this code, please ignore this email."
        ),
    )

    return {
        "message": "A new verification code has been sent to your email."
    }
