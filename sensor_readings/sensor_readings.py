from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from datetime import date, datetime
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
    start_date: date | None = None,
    end_date: date | None = None,
    interval_minutes: int = 10,
    user_id: int = Depends(get_current_user_id),
):
    connection = get_connection()

    try:
        # ----------------------------------------------------
        # Validate interval
        # ----------------------------------------------------

        allowed_intervals = {
            5,
            10,
            15,
            30,
            60,
            360,
            1440,
        }

        if interval_minutes not in allowed_intervals:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Invalid interval. "
                    "Allowed values: 5, 10, 15, 30, 60, 360, 1440 minutes."
                ),
            )

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
        # Default date range
        #
        # If no date range is provided:
        # use the most recent 24 hours.
        # ----------------------------------------------------

        if start_date is None and end_date is None:
            cursor.execute(
                """
                SELECT
                    MIN(recorded_at::date),
                    MAX(recorded_at::date)
                FROM sensor_readings
                WHERE shelf_id = %s
                """,
                (shelf_id,),
            )

            date_range = cursor.fetchone()

            if date_range and date_range[0] is not None:
                start_date = date_range[0]
                end_date = date_range[1]
            else:
                cursor.close()
                return []

        elif start_date is None:
            start_date = end_date

        elif end_date is None:
            end_date = start_date

        # ----------------------------------------------------
        # Validate date range
        # ----------------------------------------------------

        if start_date > end_date:
            cursor.close()

            raise HTTPException(
                status_code=400,
                detail="start_date cannot be later than end_date.",
            )

        # ----------------------------------------------------
        # Get averaged readings
        # ----------------------------------------------------

        cursor.execute(
            """
            SELECT
            date_bin(
                %s * INTERVAL '1 minute',
                recorded_at,
                TIMESTAMPTZ '2000-01-01 00:00:00+00'
            ) AS interval_start,

                AVG(ph) AS ph,
                AVG(ec) AS ec,
                AVG(orp) AS orp,
                AVG(temperature) AS temperature

            FROM sensor_readings

            WHERE shelf_id = %s

              AND recorded_at >= %s::date

              AND recorded_at <
                  (%s::date + INTERVAL '1 day')

            GROUP BY interval_start

            ORDER BY interval_start ASC
            """,
            (
                interval_minutes,
                shelf_id,
                start_date,
                end_date,
            ),
        )

        rows = cursor.fetchall()

        cursor.close()

        # ----------------------------------------------------
        # Return averaged readings
        # ----------------------------------------------------

        return [
            {
                "recorded_at": row[0],
                "ph": float(row[1]) if row[1] is not None else None,
                "ec": float(row[2]) if row[2] is not None else None,
                "orp": float(row[3]) if row[3] is not None else None,
                "temperature": (
                    float(row[4])
                    if row[4] is not None
                    else None
                ),
            }
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