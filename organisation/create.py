from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from database.connection import get_connection
from auth.security import verify_setup_token, verify_access_token

from organisation.schemas import CreateOrganisationRequest


router = APIRouter(
    prefix="/organisations",
    tags=["Organisations"],
)

security = HTTPBearer()


def get_onboarding_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> int:

    token = credentials.credentials

    # First try the short-lived setup token.
    try:
        return verify_setup_token(token)
    except HTTPException:
        pass

    # If that fails, allow a normal access token.
    return verify_access_token(token)

def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> int:
    token = credentials.credentials
    return verify_access_token(token)
    
@router.post("")
def create_organisation(
    request: CreateOrganisationRequest,
    user_id: int = Depends(get_onboarding_user_id),
):

    organisation_type = request.organisation_type.upper()

    if organisation_type not in ("PERSONAL", "SHARED"):
        raise HTTPException(
            status_code=400,
            detail="Organisation type must be PERSONAL or SHARED.",
        )

    conn = get_connection()

    try:
        with conn.cursor() as cursor:

            # Check whether organisation name already exists.
            cursor.execute(
                """
                SELECT id
                FROM organisations
                WHERE LOWER(name) = LOWER(%s)
                """,
                (request.name.strip(),),
            )

            existing = cursor.fetchone()

            if existing:
                raise HTTPException(
                    status_code=409,
                    detail="Organisation name already exists. "
                           "Please choose another name.",
                )

            # Create organisation.
            cursor.execute(
                """
                INSERT INTO organisations (
                    name,
                    description,
                    website,
                    phone_number,
                    organisation_type
                )
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, name, organisation_type
                """,
                (
                    request.name.strip(),
                    request.description.strip(),
                    str(request.website) if request.website else None,
                    request.phone_number,
                    organisation_type,
                ),
            )

            organisation_id, name, created_type = cursor.fetchone()

            # Creator automatically becomes ADMIN + APPROVED.
            cursor.execute(
                """
                INSERT INTO organisation_members (
                    organisation_id,
                    user_id,
                    role,
                    membership_status
                )
                VALUES (%s, %s, 'ADMIN', 'APPROVED')
                """,
                (
                    organisation_id,
                    user_id,
                ),
            )

            conn.commit()

    finally:
        conn.close()

    return {
        "message": "Organisation created successfully.",
        "organisation_id": organisation_id,
        "organisation_name": name,
        "organisation_type": created_type,
        "role": "ADMIN",
        "membership_status": "APPROVED",
    }

@router.put("/{organisation_id}")
def update_organisation(
    organisation_id: int,
    request: CreateOrganisationRequest,
    user_id: int = Depends(get_current_user_id),
):
    print("========== UPDATE ORGANISATION ==========")
    print("Organisation ID:", organisation_id)
    print("Request:", request)
    print("=========================================")
    organisation_type = request.organisation_type.upper()

    if organisation_type not in ("PERSONAL", "SHARED"):
        raise HTTPException(
            status_code=400,
            detail="Organisation type must be PERSONAL or SHARED.",
        )

    conn = get_connection()

    try:
        with conn.cursor() as cursor:

            # Check that the organisation exists.
            cursor.execute(
                """
                SELECT id
                FROM organisations
                WHERE id = %s
                """,
                (organisation_id,),
            )

            organisation = cursor.fetchone()

            if not organisation:
                raise HTTPException(
                    status_code=404,
                    detail="Organisation not found.",
                )

            # Check that the current user is an approved ADMIN.
            cursor.execute(
                """
                SELECT role, membership_status
                FROM organisation_members
                WHERE organisation_id = %s
                  AND user_id = %s
                """,
                (
                    organisation_id,
                    user_id,
                ),
            )

            membership = cursor.fetchone()

            if not membership:
                raise HTTPException(
                    status_code=403,
                    detail="You are not a member of this organisation.",
                )

            if membership[1] != "APPROVED":
                raise HTTPException(
                    status_code=403,
                    detail="Your organisation membership is not approved.",
                )

            if membership[0] != "ADMIN":
                raise HTTPException(
                    status_code=403,
                    detail="Only organisation admins can edit organisation information.",
                )

            # Check whether another organisation already uses this name.
            cursor.execute(
                """
                SELECT id
                FROM organisations
                WHERE LOWER(name) = LOWER(%s)
                  AND id != %s
                """,
                (
                    request.name.strip(),
                    organisation_id,
                ),
            )

            existing = cursor.fetchone()

            if existing:
                raise HTTPException(
                    status_code=409,
                    detail="Organisation name already exists. Please choose another name.",
                )

            # Update organisation information.
            cursor.execute(
                """
                UPDATE organisations
                SET
                    name = %s,
                    description = %s,
                    website = %s,
                    phone_number = %s
                WHERE id = %s
                """,
                (
                    request.name.strip(),
                    request.description.strip(),
                    str(request.website) if request.website else None,
                    request.phone_number,
                    organisation_id,
                ),
            )

            conn.commit()

    finally:
        conn.close()

    return {
        "message": "Organisation updated successfully.",
        "organisation_id": organisation_id,
        "organisation_name": request.name.strip(),
        "description": request.description.strip(),
        "website": str(request.website) if request.website else None,
        "phone_number": request.phone_number,
        "organisation_type": organisation_type,
    }

