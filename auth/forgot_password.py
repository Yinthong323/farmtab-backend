import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database.connection import get_connection
from auth.security import (
    hash_password,
    create_password_reset_token,
    verify_password_reset_token,
)
from services.email_service import send_password_reset_email


router = APIRouter(
    prefix="/auth",
    tags=["Authentication"],
)


class ForgotPasswordRequest(BaseModel):
    email: str


class VerifyResetCodeRequest(BaseModel):
    email: str
    code: str


class ResendResetCodeRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    reset_token: str
    new_password: str


@router.post("/forgot-password")
async def forgot_password(request: ForgotPasswordRequest):

    email = request.email.strip().lower()

    conn = get_connection()

    try:
        with conn.cursor() as cursor:

            cursor.execute(
                """
                SELECT id, is_verified
                FROM users
                WHERE LOWER(email) = LOWER(%s)
                """,
                (email,),
            )

            user = cursor.fetchone()

            if not user:
                raise HTTPException(
                    status_code=404,
                    detail="This email address is not registered.",
                )

            user_id, is_verified = user

            if not is_verified:
                raise HTTPException(
                    status_code=400,
                    detail="Please verify your email before resetting your password.",
                )

            cursor.execute(
                """
                SELECT password_reset_sent_at
                FROM users
                WHERE id = %s
                """,
                (user_id,),
            )

            result = cursor.fetchone()
            password_reset_sent_at = result[0]

            now = datetime.now(timezone.utc)

            # Prevent requesting a new code more than once every 60 seconds.
            if password_reset_sent_at:

                elapsed = now - password_reset_sent_at

                if elapsed < timedelta(seconds=60):

                    remaining = 60 - int(
                        elapsed.total_seconds()
                    )

                    raise HTTPException(
                        status_code=429,
                        detail=(
                            f"Please wait {remaining} seconds "
                            "before requesting another reset code."
                        ),
                    )

            # Generate a secure 6-digit code.
            reset_code = f"{secrets.randbelow(1_000_000):06d}"

            reset_expires_at = now + timedelta(minutes=10)

            cursor.execute(
                """
                UPDATE users
                SET password_reset_code = %s,
                    password_reset_expires_at = %s,
                    password_reset_sent_at = %s
                WHERE id = %s
                """,
                (
                    reset_code,
                    reset_expires_at,
                    now,
                    user_id,
                ),
            )

            conn.commit()

    finally:
        conn.close()

    # Send the reset code after the database update succeeds.
    await send_password_reset_email(
        to_email=email,
        reset_code=reset_code,
    )

    return {
        "message": "A password reset code has been sent to your email."
    }

@router.post("/reset-password/resend-code")
async def resend_reset_code(request: ResendResetCodeRequest):

    email = request.email.strip().lower()

    conn = get_connection()

    try:
        with conn.cursor() as cursor:

            cursor.execute(
                """
                SELECT id, is_verified, password_reset_sent_at
                FROM users
                WHERE LOWER(email) = LOWER(%s)
                """,
                (email,),
            )

            user = cursor.fetchone()

            if not user:
                raise HTTPException(
                    status_code=404,
                    detail="This email address is not registered.",
                )

            user_id, is_verified, password_reset_sent_at = user

            if not is_verified:
                raise HTTPException(
                    status_code=400,
                    detail="Please verify your email before resetting your password.",
                )

            now = datetime.now(timezone.utc)

            # Server-side 60-second cooldown.
            if password_reset_sent_at:
                elapsed = now - password_reset_sent_at

                if elapsed < timedelta(seconds=60):

                    remaining = 60 - int(
                        elapsed.total_seconds()
                    )

                    raise HTTPException(
                        status_code=429,
                        detail=(
                            f"Please wait {remaining} seconds "
                            "before requesting another reset code."
                        ),
                    )

            reset_code = f"{secrets.randbelow(1_000_000):06d}"

            reset_expires_at = now + timedelta(minutes=10)

            cursor.execute(
                """
                UPDATE users
                SET password_reset_code = %s,
                    password_reset_expires_at = %s,
                    password_reset_sent_at = %s
                WHERE id = %s
                """,
                (
                    reset_code,
                    reset_expires_at,
                    now,
                    user_id,
                ),
            )

            conn.commit()

    finally:
        conn.close()

    await send_password_reset_email(
        to_email=email,
        reset_code=reset_code,
    )

    return {
        "message": "A new password reset code has been sent to your email."
    }


@router.post("/reset-password/verify")
def verify_reset_code(
    request: VerifyResetCodeRequest,
):

    email = request.email.strip().lower()
    code = request.code.strip()

    if len(code) != 6 or not code.isdigit():
        raise HTTPException(
            status_code=400,
            detail="Verification code must contain exactly 6 digits.",
        )

    conn = get_connection()

    try:
        with conn.cursor() as cursor:

            cursor.execute(
                """
                SELECT id,
                       password_reset_code,
                       password_reset_expires_at
                FROM users
                WHERE LOWER(email) = LOWER(%s)
                """,
                (email,),
            )

            user = cursor.fetchone()

            if not user:
                raise HTTPException(
                    status_code=404,
                    detail="This email address is not registered.",
                )

            user_id, stored_code, expires_at = user

            if not stored_code:
                raise HTTPException(
                    status_code=400,
                    detail="No password reset code is active.",
                )

            now = datetime.now(timezone.utc)

            if expires_at is None or now > expires_at:
                raise HTTPException(
                    status_code=400,
                    detail="Password reset code has expired.",
                )

            if code != stored_code:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid password reset code.",
                )

            # Code is correct, so create a short-lived reset token.
            reset_token = create_password_reset_token(user_id)

            # Prevent the same code from being reused.
            cursor.execute(
                """
                UPDATE users
                SET password_reset_code = NULL,
                    password_reset_expires_at = NULL,
                    password_reset_sent_at = NULL
                WHERE id = %s
                """,
                (user_id,),
            )

            conn.commit()

    finally:
        conn.close()

    return {
        "message": "Password reset code verified successfully.",
        "reset_token": reset_token,
    }


@router.post("/reset-password")
def reset_password(
    request: ResetPasswordRequest,
):

    if len(request.new_password) < 8:
        raise HTTPException(
            status_code=400,
            detail="Password must contain at least 8 characters.",
        )

    user_id = verify_password_reset_token(
        request.reset_token
    )

    hashed_password = hash_password(
        request.new_password
    )

    conn = get_connection()

    try:
        with conn.cursor() as cursor:

            cursor.execute(
                """
                UPDATE users
                SET password_hash = %s
                WHERE id = %s
                """,
                (
                    hashed_password,
                    user_id,
                ),
            )

            if cursor.rowcount == 0:
                raise HTTPException(
                    status_code=404,
                    detail="User account not found.",
                )

            conn.commit()

    finally:
        conn.close()

    return {
        "message": "Password has been reset successfully."
    }
