from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from database.connection import get_connection
from auth.security import verify_access_token


router = APIRouter(
    prefix="/organisations",
    tags=["Devices"],
)

security = HTTPBearer()


def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> int:
    token = credentials.credentials
    return verify_access_token(token)


@router.get("/{organisation_id}/devices")
def get_organisation_devices(
    organisation_id: int,
    user_id: int = Depends(get_current_user_id),
):
    connection = get_connection()

    try:
        cursor = connection.cursor()

        # ---------------------------------------------------------
        # Check that the user is an approved member
        # ---------------------------------------------------------

        cursor.execute(
            """
            SELECT role
            FROM organisation_members
            WHERE organisation_id = %s
              AND user_id = %s
              AND membership_status = 'APPROVED'
            """,
            (organisation_id, user_id),
        )

        membership = cursor.fetchone()

        if not membership:
            raise HTTPException(
                status_code=403,
                detail="You are not an approved member of this organisation.",
            )

        # ---------------------------------------------------------
        # Get all registered devices
        # ---------------------------------------------------------

        cursor.execute(
            """
            SELECT
                s.id AS shelf_id,
                s.name AS shelf_name,
                s.device_serial_number,
                s.site_id,
                si.name AS site_name
            FROM shelves s
            JOIN sites si
                ON si.id = s.site_id
            WHERE si.organisation_id = %s
              AND s.device_serial_number IS NOT NULL
              AND TRIM(s.device_serial_number) <> ''
            ORDER BY si.name ASC, s.name ASC
            """,
            (organisation_id,),
        )

        rows = cursor.fetchall()

        cursor.close()

        columns = [
            "shelf_id",
            "shelf_name",
            "device_serial_number",
            "site_id",
            "site_name",
        ]

        return [
            dict(zip(columns, row))
            for row in rows
        ]

    finally:
        connection.close()