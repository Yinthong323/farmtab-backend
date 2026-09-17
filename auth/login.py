from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database.connection import get_connection
from auth.security import verify_password, create_access_token


router = APIRouter(
    prefix="/auth",
    tags=["Authentication"],
)


class LoginRequest(BaseModel):
    login: str
    password: str


@router.post("/login")
def login(request: LoginRequest):

    conn = get_connection()

    try:
        with conn.cursor() as cursor:

            # Find user by username OR email.
            cursor.execute(
                """
                SELECT id, username, email, password_hash, is_verified
                FROM users
                WHERE LOWER(username) = LOWER(%s)
                   OR LOWER(email) = LOWER(%s)
                """,
                (
                    request.login.strip(),
                    request.login.strip(),
                ),
            )

            user = cursor.fetchone()

            if not user:
                raise HTTPException(
                    status_code=401,
                    detail="Invalid username/email or password.",
                )

            (
                user_id,
                username,
                email,
                password_hash,
                is_verified,
            ) = user

            # Check password.
            if not verify_password(
                request.password,
                password_hash,
            ):
                raise HTTPException(
                    status_code=401,
                    detail="Invalid username/email or password.",
                )

            # User must verify email first.
            if not is_verified:
                raise HTTPException(
                    status_code=403,
                    detail="Please verify your email before logging in.",
                )

            # -------------------------------------------------
            # 1. Check for an APPROVED organisation first.
            # -------------------------------------------------

            cursor.execute(
                """
                SELECT
                    o.id,
                    o.name,
                    o.description,
                    o.website,
                    o.phone_number,
                    o.organisation_type,
                    om.role,
                    om.membership_status
                FROM organisation_members om
                JOIN organisations o
                    ON o.id = om.organisation_id
                WHERE om.user_id = %s
                  AND om.membership_status = 'APPROVED'
                ORDER BY om.created_at
                LIMIT 1
                """,
                (user_id,),
            )

            approved_organisation = cursor.fetchone()

            if approved_organisation:

                (
                    organisation_id,
                    organisation_name,
                    organisation_description,
                    organisation_website,
                    organisation_phone,
                    organisation_type,
                    role,
                    membership_status,
                ) = approved_organisation

                organisation_status = "APPROVED"

                organisation = {
                    "id": organisation_id,
                    "name": organisation_name,
                    "description": organisation_description,
                    "website": organisation_website,
                    "phone_number": organisation_phone,
                    "organisation_type": organisation_type,
                    "role": role,
                    "membership_status": membership_status,
                }

            else:

                # -------------------------------------------------
                # 2. No approved organisation.
                #    Check whether there is a PENDING request.
                # -------------------------------------------------

                cursor.execute(
                    """
                    SELECT
                        o.id,
                        o.name,
                        o.description,
                        o.website,
                        o.phone_number,
                        o.organisation_type,
                        om.role,
                        om.membership_status
                    FROM organisation_members om
                    JOIN organisations o
                        ON o.id = om.organisation_id
                    WHERE om.user_id = %s
                      AND om.membership_status = 'PENDING'
                    ORDER BY om.created_at DESC
                    LIMIT 1
                    """,
                    (user_id,),
                )

                pending_organisation = cursor.fetchone()

                if pending_organisation:

                    (
                        organisation_id,
                        organisation_name,
                        organisation_description,
                        organisation_website,
                        organisation_phone,
                        organisation_type,
                        role,
                        membership_status,
                    ) = pending_organisation

                    organisation_status = "PENDING"

                    organisation = {
                        "id": organisation_id,
                        "name": organisation_name,
                        "description": organisation_description,
                        "website": organisation_website,
                        "phone_number": organisation_phone,
                        "organisation_type": organisation_type,
                        "role": role,
                        "membership_status": membership_status,
                    }

                else:

                    # -------------------------------------------------
                    # 3. No approved organisation and no pending request.
                    # -------------------------------------------------

                    organisation_status = "NONE"
                    organisation = None

    finally:
        conn.close()

    # -------------------------------------------------
    # Login is successful for all verified users.
    #
    # The organisation status determines where Flutter
    # sends the user next.
    # -------------------------------------------------

    access_token = create_access_token(user_id)

    return {
        "message": "Login successful.",
        "access_token": access_token,

        "user": {
            "id": user_id,
            "username": username,
            "email": email,
        },

        "organisation_status": organisation_status,

        "organisation": organisation,
    }