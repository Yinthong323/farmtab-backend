from fastapi import APIRouter, Depends, HTTPException, Query

from database.connection import get_connection
from notifications.schemas import NotificationResponse
from auth.dependencies import get_current_user_id

router = APIRouter(
    prefix="/notifications",
    tags=["Notifications"],
)


# ------------------------------------------------------------
# GET NOTIFICATIONS
# ------------------------------------------------------------

@router.get(
    "",
    response_model=list[NotificationResponse],
)
def get_notifications(
    user_id: int = Depends(get_current_user_id),
    limit: int = Query(default=50, ge=1, le=100),
):
    """
    Get notifications for organisations that the current user
    is an APPROVED member of.
    """

    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                a.id,
                s.id,
                s.site_id,

                si.name,
                s.name,
                s.crop_type,

                a.sensor_type,
                a.alert_type,

                a.value,
                a.threshold_value,

                a.status,
                a.is_read,

                a.created_at,
                a.resolved_at

            FROM alerts a

            INNER JOIN shelves s
                ON a.shelf_id = s.id

            INNER JOIN sites si
                ON s.site_id = si.id

            INNER JOIN organisation_members om
                ON si.organisation_id = om.organisation_id

            WHERE om.user_id = %s
              AND om.membership_status = 'APPROVED'

            ORDER BY a.created_at DESC

            LIMIT %s
            """,
            (
                user_id,
                limit,
            ),
        )

        rows = cursor.fetchall()

        notifications = []

        for row in rows:

            notifications.append(
                {
                    "id": row[0],
                    "shelf_id": row[1],
                    "site_id": row[2],

                    "site_name": row[3],
                    "shelf_name": row[4],
                    "crop_type": row[5],

                    "sensor_type": row[6],
                    "alert_type": row[7],

                    "value": float(row[8]),
                    "threshold_value": float(row[9]),

                    "status": row[10],
                    "is_read": row[11],

                    "created_at": row[12],
                    "resolved_at": row[13],
                }
            )

        cursor.close()

        return notifications

    except Exception as e:

        connection.rollback()

        raise HTTPException(
            status_code=500,
            detail=f"Unable to get notifications: {str(e)}",
        )

    finally:

        connection.close()


# ------------------------------------------------------------
# MARK NOTIFICATION AS READ
# ------------------------------------------------------------

@router.put(
    "/{notification_id}/read",
)
def mark_notification_as_read(
    notification_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """
    Mark one notification as read.

    The notification must belong to an organisation
    that the current user is an APPROVED member of.
    """

    connection = get_connection()

    try:

        cursor = connection.cursor()

        cursor.execute(
            """
            UPDATE alerts a

            SET is_read = TRUE

            FROM shelves s
            INNER JOIN sites si
                ON s.site_id = si.id

            INNER JOIN organisation_members om
                ON si.organisation_id = om.organisation_id

            WHERE a.id = %s
              AND a.shelf_id = s.id
              AND om.user_id = %s
              AND om.membership_status = 'APPROVED'

            RETURNING a.id
            """,
            (
                notification_id,
                user_id,
            ),
        )

        result = cursor.fetchone()

        if not result:

            connection.rollback()

            raise HTTPException(
                status_code=404,
                detail="Notification not found.",
            )

        connection.commit()

        cursor.close()

        return {
            "message": "Notification marked as read.",
            "notification_id": notification_id,
        }

    except HTTPException:

        raise

    except Exception as e:

        connection.rollback()

        raise HTTPException(
            status_code=500,
            detail=f"Unable to mark notification as read: {str(e)}",
        )

    finally:

        connection.close()

@router.get(
    "/shelf/{shelf_id}",
    response_model=list[NotificationResponse],
)
def get_shelf_notifications(
    shelf_id: int,
    user_id: int = Depends(get_current_user_id),
    limit: int = Query(default=50, ge=1, le=100),
):
    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                a.id,
                s.id,
                s.site_id,
                si.name,
                s.name,
                s.crop_type,
                a.sensor_type,
                a.alert_type,
                a.value,
                a.threshold_value,
                a.status,
                a.is_read,
                a.created_at,
                a.resolved_at
            FROM alerts a
            INNER JOIN shelves s
                ON a.shelf_id = s.id
            INNER JOIN sites si
                ON s.site_id = si.id
            INNER JOIN organisation_members om
                ON si.organisation_id = om.organisation_id
            WHERE a.shelf_id = %s
              AND om.user_id = %s
              AND om.membership_status = 'APPROVED'
            ORDER BY a.created_at DESC
            LIMIT %s
            """,
            (shelf_id, user_id, limit),
        )

        rows = cursor.fetchall()

        notifications = []

        for row in rows:
            notifications.append({
                "id": row[0],
                "shelf_id": row[1],
                "site_id": row[2],
                "site_name": row[3],
                "shelf_name": row[4],
                "crop_type": row[5],
                "sensor_type": row[6],
                "alert_type": row[7],
                "value": float(row[8]),
                "threshold_value": float(row[9]),
                "status": row[10],
                "is_read": row[11],
                "created_at": row[12],
                "resolved_at": row[13],
            })

        cursor.close()

        return notifications

    except Exception as e:
        connection.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Unable to get shelf notifications: {str(e)}",
        )

    finally:
        connection.close()


@router.put("/shelf/{shelf_id}/read-all")
def mark_shelf_notifications_as_read(
    shelf_id: int,
    user_id: int = Depends(get_current_user_id),
):
    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            UPDATE alerts a
            SET is_read = TRUE
            FROM shelves s
            INNER JOIN sites si
                ON s.site_id = si.id
            INNER JOIN organisation_members om
                ON si.organisation_id = om.organisation_id
            WHERE a.shelf_id = %s
              AND a.shelf_id = s.id
              AND om.user_id = %s
              AND om.membership_status = 'APPROVED'
              AND a.is_read = FALSE
            RETURNING a.id
            """,
            (shelf_id, user_id),
        )

        updated_rows = cursor.fetchall()

        connection.commit()
        cursor.close()

        return {
            "message": "Shelf notifications marked as read.",
            "shelf_id": shelf_id,
            "marked_count": len(updated_rows),
        }

    except Exception as e:
        connection.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Unable to mark shelf notifications as read: {str(e)}",
        )

    finally:
        connection.close()