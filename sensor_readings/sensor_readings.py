from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from auth.security import verify_access_token
from database.connection import get_connection
from .schemas import CreateSensorReadingRequest


router = APIRouter(
    prefix="/sites",
    tags=["Sensor Readings"],
)

security = HTTPBearer()


def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> int:
    token = credentials.credentials
    return verify_access_token(token)


def check_shelf_access(
    connection,
    shelf_id: int,
    user_id: int,
):
    """
    Check whether the current user is an approved member
    of the organisation that owns this shelf.

    Returns:
        organisation_id, role, membership_status
    """

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            o.id,
            om.role,
            om.membership_status
        FROM shelves s
        JOIN sites si
            ON s.site_id = si.id
        JOIN organisations o
            ON si.organisation_id = o.id
        JOIN organisation_members om
            ON om.organisation_id = o.id
        WHERE s.id = %s
          AND om.user_id = %s
        """,
        (shelf_id, user_id),
    )

    membership = cursor.fetchone()

    cursor.close()

    if not membership:
        raise HTTPException(
            status_code=403,
            detail="You do not have access to this Shelf.",
        )

    organisation_id, role, membership_status = membership

    if membership_status != "APPROVED":
        raise HTTPException(
            status_code=403,
            detail="Your organisation membership is not approved.",
        )

    return organisation_id, role, membership_status


# ============================================================
# Create Sensor Reading
# ============================================================

@router.post("/{site_id}/shelves/{shelf_id}/sensor-readings")
def create_sensor_reading(
    site_id: int,
    shelf_id: int,
    request: CreateSensorReadingRequest,
    user_id: int = Depends(get_current_user_id),
):
    connection = get_connection()

    try:
        # ----------------------------------------------------
        # Check shelf + user access
        # ----------------------------------------------------

        membership = check_shelf_access(
            connection,
            shelf_id,
            user_id,
        )

        # ----------------------------------------------------
        # Make sure the shelf belongs to this site
        # ----------------------------------------------------

        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT id
            FROM shelves
            WHERE id = %s
              AND site_id = %s
            """,
            (shelf_id, site_id),
        )

        shelf = cursor.fetchone()

        if not shelf:
            cursor.close()

            raise HTTPException(
                status_code=404,
                detail="Shelf not found in this Site.",
            )

        # ----------------------------------------------------
        # Validate sensor values
        # ----------------------------------------------------

        if request.ph < 0 or request.ph > 14:
            cursor.close()

            raise HTTPException(
                status_code=400,
                detail="pH must be between 0 and 14.",
            )

        if request.ec < 0:
            cursor.close()

            raise HTTPException(
                status_code=400,
                detail="EC cannot be negative.",
            )

        # ORP can theoretically be negative,
        # so we don't reject negative ORP values.

        if request.temperature < -50 or request.temperature > 100:
            cursor.close()

            raise HTTPException(
                status_code=400,
                detail="Temperature must be between -50 and 100 °C.",
            )

        # ----------------------------------------------------
        # Insert reading
        # ----------------------------------------------------

        cursor.execute(
            """
            INSERT INTO sensor_readings (
                shelf_id,
                ph,
                ec,
                orp,
                temperature
            )
            VALUES (%s, %s, %s, %s, %s)
            RETURNING
                id,
                shelf_id,
                recorded_at,
                ph,
                ec,
                orp,
                temperature
            """,
            (
                shelf_id,
                request.ph,
                request.ec,
                request.orp,
                request.temperature,
            ),
        )

        row = cursor.fetchone()

        connection.commit()

        cursor.close()

        columns = [
            "id",
            "shelf_id",
            "recorded_at",
            "ph",
            "ec",
            "orp",
            "temperature",
        ]

        return dict(zip(columns, row))

    except HTTPException:
        connection.rollback()
        raise

    except Exception as e:
        connection.rollback()

        raise HTTPException(
            status_code=500,
            detail=f"Unable to create sensor reading: {str(e)}",
        )

    finally:
        connection.close()


# ============================================================
# Get Latest Sensor Reading
# ============================================================

@router.get("/{site_id}/shelves/{shelf_id}/sensor-readings/latest")
def get_latest_sensor_reading(
    site_id: int,
    shelf_id: int,
    user_id: int = Depends(get_current_user_id),
):
    connection = get_connection()

    try:
        # ----------------------------------------------------
        # Check shelf access
        # ----------------------------------------------------

        check_shelf_access(
            connection,
            shelf_id,
            user_id,
        )

        cursor = connection.cursor()

        # ----------------------------------------------------
        # Make sure shelf belongs to site
        # ----------------------------------------------------

        cursor.execute(
            """
            SELECT id
            FROM shelves
            WHERE id = %s
              AND site_id = %s
            """,
            (shelf_id, site_id),
        )

        shelf = cursor.fetchone()

        if not shelf:
            cursor.close()

            raise HTTPException(
                status_code=404,
                detail="Shelf not found in this Site.",
            )

        # ----------------------------------------------------
        # Get latest reading
        # ----------------------------------------------------

        cursor.execute(
            """
            SELECT
                id,
                shelf_id,
                recorded_at,
                ph,
                ec,
                orp,
                temperature
            FROM sensor_readings
            WHERE shelf_id = %s
            ORDER BY recorded_at DESC
            LIMIT 1
            """,
            (shelf_id,),
        )

        row = cursor.fetchone()

        cursor.close()

        if not row:
            return None

        columns = [
            "id",
            "shelf_id",
            "recorded_at",
            "ph",
            "ec",
            "orp",
            "temperature",
        ]

        return dict(zip(columns, row))

    except HTTPException:
        connection.rollback()
        raise

    except Exception as e:
        connection.rollback()

        raise HTTPException(
            status_code=500,
            detail=f"Unable to get latest sensor reading: {str(e)}",
        )

    finally:
        connection.close()


# ============================================================
# Get Sensor Reading History
# ============================================================

@router.get("/{site_id}/shelves/{shelf_id}/sensor-readings")
def get_sensor_readings(
    site_id: int,
    shelf_id: int,
    user_id: int = Depends(get_current_user_id),
):
    connection = get_connection()

    try:
        # ----------------------------------------------------
        # Check shelf access
        # ----------------------------------------------------

        check_shelf_access(
            connection,
            shelf_id,
            user_id,
        )

        cursor = connection.cursor()

        # ----------------------------------------------------
        # Make sure shelf belongs to site
        # ----------------------------------------------------

        cursor.execute(
            """
            SELECT id
            FROM shelves
            WHERE id = %s
              AND site_id = %s
            """,
            (shelf_id, site_id),
        )

        shelf = cursor.fetchone()

        if not shelf:
            cursor.close()

            raise HTTPException(
                status_code=404,
                detail="Shelf not found in this Site.",
            )

        # ----------------------------------------------------
        # Get readings
        # ----------------------------------------------------

        cursor.execute(
            """
            SELECT
                id,
                shelf_id,
                recorded_at,
                ph,
                ec,
                orp,
                temperature
            FROM sensor_readings
            WHERE shelf_id = %s
            ORDER BY recorded_at DESC
            """,
            (shelf_id,),
        )

        rows = cursor.fetchall()

        cursor.close()

        columns = [
            "id",
            "shelf_id",
            "recorded_at",
            "ph",
            "ec",
            "orp",
            "temperature",
        ]

        return [
            dict(zip(columns, row))
            for row in rows
        ]

    except HTTPException:
        connection.rollback()
        raise

    except Exception as e:
        connection.rollback()

        raise HTTPException(
            status_code=500,
            detail=f"Unable to get sensor readings: {str(e)}",
        )

    finally:
        connection.close()