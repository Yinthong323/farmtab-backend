import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException

from auth.schemas import SignupRequest
from auth.security import hash_password
from database.connection import get_connection
from services.email_service import send_email


router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/signup")
async def signup(request: SignupRequest):

    # 1. Check whether username or email already exists
    conn = get_connection()

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id
                FROM users
                WHERE username = %s OR email = %s
                """,
                (request.username, request.email),
            )

            existing_user = cursor.fetchone()

            if existing_user:
                raise HTTPException(
                    status_code=400,
                    detail="Username or email already exists."
                )

            # 2. Hash the password
            password_hash = hash_password(request.password)

            # 3. Generate a secure 6-digit verification code
            verification_code = f"{secrets.randbelow(1_000_000):06d}"

            # 4. Set code expiry to 10 minutes
            verification_expires_at = (
                datetime.now(timezone.utc) + timedelta(minutes=10)
            )

            # 5. Save the new user
            cursor.execute(
                """
                INSERT INTO users (
                    username,
                    email,
                    password_hash,
                    is_verified,
                    verification_code,
                    verification_expires_at,
                    verification_sent_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    request.username,
                    request.email,
                    password_hash,
                    False,
                    verification_code,
                    verification_expires_at,
                    datetime.now(timezone.utc),
                ),
            )

            conn.commit()

    finally:
        conn.close()

    # 6. Send verification email
    await send_email(
        to_email=request.email,
        subject="FarmTab Email Verification",
        body=(
            f"Hello {request.username},\n\n"
            f"Your FarmTab verification code is:\n\n"
            f"{verification_code}\n\n"
            f"This code will expire in 10 minutes.\n\n"
            f"If you did not create a FarmTab account, "
            f"please ignore this email."
        ),
    )

    # 7. Return success response
    return {
        "message": "Account created successfully. "
                   "Please check your email for the verification code."
    }
