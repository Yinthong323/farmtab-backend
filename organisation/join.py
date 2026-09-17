from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from database.connection import get_connection
from auth.security import verify_access_token, verify_setup_token


router = APIRouter(
    prefix="/organisations",
    tags=["Organisation Membership"],
)

security = HTTPBearer()


def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> int:

    token = credentials.credentials

    try:
        return verify_access_token(token)
    except Exception:
        return verify_setup_token(token)


@router.post("/{organisation_id}/join")
def request_to_join(
    organisation_id: int,
    user_id: int = Depends(get_current_user_id),
):

    conn = get_connection()

    try:
        with conn.cursor() as cursor:

            # Check organisation exists and is SHARED.
            cursor.execute(
                """
                SELECT id, name
                FROM organisations
                WHERE id = %s
                  AND organisation_type = 'SHARED'
                """,
                (organisation_id,),
            )

            organisation = cursor.fetchone()

            if not organisation:
                raise HTTPException(
                    status_code=404,
                    detail="Shared organisation not found.",
                )

            organisation_id, organisation_name = organisation

            # Check whether user already has a membership row.
            cursor.execute(
                """
                SELECT membership_status
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

            if membership:

                membership_status = membership[0]

                if membership_status == "PENDING":
                    raise HTTPException(
                        status_code=409,
                        detail="You already have a pending request "
                               "for this organisation.",
                    )

                if membership_status == "APPROVED":
                    raise HTTPException(
                        status_code=409,
                        detail="You are already a member of this organisation.",
                    )

                if membership_status == "REJECTED":

                    # Allow the user to request again.
                    cursor.execute(
                        """
                        UPDATE organisation_members
                        SET membership_status = 'PENDING',
                            role = 'STAFF'
                        WHERE organisation_id = %s
                          AND user_id = %s
                        """,
                        (
                            organisation_id,
                            user_id,
                        ),
                    )

                else:
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid membership status.",
                    )

            else:

                # New join request.
                cursor.execute(
                    """
                    INSERT INTO organisation_members (
                        organisation_id,
                        user_id,
                        role,
                        membership_status
                    )
                    VALUES (%s, %s, 'STAFF', 'PENDING')
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
        "message": (
            f"Your request to join {organisation_name} "
            "has been submitted and is waiting for approval."
        ),
        "organisation_id": organisation_id,
        "organisation_name": organisation_name,
        "membership_status": "PENDING",
    }
@router.get("/{organisation_id}/join/status")
def get_join_request_status(
    organisation_id: int,
    user_id: int = Depends(get_current_user_id),
):
    conn = get_connection()

    try:
        with conn.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    o.id,
                    o.name,
                    om.role,
                    om.membership_status
                FROM organisation_members om
                JOIN organisations o
                    ON o.id = om.organisation_id
                WHERE om.organisation_id = %s
                  AND om.user_id = %s
                """,
                (
                    organisation_id,
                    user_id,
                ),
            )

            membership = cursor.fetchone()

    finally:
        conn.close()

    if not membership:
        return {
            "organisation_id": organisation_id,
            "membership_status": "NONE",
        }

    return {
        "organisation_id": membership[0],
        "organisation_name": membership[1],
        "role": membership[2],
        "membership_status": membership[3],
    }

@router.delete("/{organisation_id}/join")
def cancel_join_request(
    organisation_id: int,
    user_id: int = Depends(get_current_user_id),
):

    conn = get_connection()

    try:
        with conn.cursor() as cursor:

            cursor.execute(
                """
                SELECT o.name
                FROM organisation_members om
                JOIN organisations o
                    ON o.id = om.organisation_id
                WHERE om.organisation_id = %s
                  AND om.user_id = %s
                  AND om.membership_status = 'PENDING'
                """,
                (
                    organisation_id,
                    user_id,
                ),
            )

            organisation = cursor.fetchone()

            if not organisation:
                raise HTTPException(
                    status_code=404,
                    detail="No pending join request found.",
                )

            organisation_name = organisation[0]

            cursor.execute(
                """
                DELETE FROM organisation_members
                WHERE organisation_id = %s
                  AND user_id = %s
                  AND membership_status = 'PENDING'
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
        "message": (
            f"Your request to join {organisation_name} "
            "has been cancelled."
        ),
        "organisation_id": organisation_id,
        "membership_status": "NONE",
    }

@router.get("/{organisation_id}/members")
def get_organisation_members(
    organisation_id: int,
    user_id: int = Depends(get_current_user_id),
):
    conn = get_connection()

    try:
        with conn.cursor() as cursor:

            # Make sure the organisation exists.
            cursor.execute(
                """
                SELECT id, name
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

            # Make sure the current user belongs to this organisation.
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

            current_membership = cursor.fetchone()

            if not current_membership:
                raise HTTPException(
                    status_code=403,
                    detail="You are not a member of this organisation.",
                )

            if current_membership[1] != "APPROVED":
                raise HTTPException(
                    status_code=403,
                    detail="Your organisation membership is not approved.",
                )

            # Get all approved members.
            cursor.execute(
                """
                SELECT
                    u.id,
                    u.username,
                    u.email,
                    om.role
                FROM organisation_members om
                JOIN users u
                    ON u.id = om.user_id
                WHERE om.organisation_id = %s
                  AND om.membership_status = 'APPROVED'
                ORDER BY
                    CASE
                        WHEN om.role = 'ADMIN' THEN 1
                        ELSE 2
                    END,
                    LOWER(u.username)
                """,
                (organisation_id,),
            )

            members = cursor.fetchall()

    finally:
        conn.close()

    return {
        "organisation_id": organisation_id,
        "organisation_name": organisation[1],
        "members": [
            {
                "user_id": member[0],
                "username": member[1],
                "email": member[2],
                "role": member[3],
            }
            for member in members
        ],
    }

@router.get("/{organisation_id}/join-requests")
def get_join_requests(
    organisation_id: int,
    user_id: int = Depends(get_current_user_id),
):
    conn = get_connection()

    try:
        with conn.cursor() as cursor:

            # Make sure the organisation exists.
            cursor.execute(
                """
                SELECT id, name
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

            # Make sure the current user is an approved member.
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

            current_membership = cursor.fetchone()

            if not current_membership:
                raise HTTPException(
                    status_code=403,
                    detail="You are not a member of this organisation.",
                )

            if current_membership[1] != "APPROVED":
                raise HTTPException(
                    status_code=403,
                    detail="Your organisation membership is not approved.",
                )

            # Only ADMIN can view join requests.
            if current_membership[0] != "ADMIN":
                raise HTTPException(
                    status_code=403,
                    detail="Only organisation admins can view join requests.",
                )

            # Get all pending join requests.
            cursor.execute(
                """
                SELECT
                    u.id,
                    u.username,
                    u.email,
                    om.created_at
                FROM organisation_members om
                JOIN users u
                    ON u.id = om.user_id
                WHERE om.organisation_id = %s
                  AND om.membership_status = 'PENDING'
                ORDER BY om.created_at ASC
                """,
                (organisation_id,),
            )

            requests = cursor.fetchall()

    finally:
        conn.close()

    return {
        "organisation_id": organisation_id,
        "organisation_name": organisation[1],
        "requests": [
            {
                "user_id": request[0],
                "username": request[1],
                "email": request[2],
                "created_at": request[3],
            }
            for request in requests
        ],
    }

@router.put("/{organisation_id}/join-requests/{user_id}/approve")
def approve_join_request(
    organisation_id: int,
    user_id: int,
    admin_user_id: int = Depends(get_current_user_id),
):
    conn = get_connection()

    try:
        with conn.cursor() as cursor:

            # Check that the organisation exists
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

            # Check that current user is an approved admin
            cursor.execute(
                """
                SELECT role, membership_status
                FROM organisation_members
                WHERE organisation_id = %s
                  AND user_id = %s
                """,
                (organisation_id, admin_user_id),
            )

            admin_membership = cursor.fetchone()

            if not admin_membership:
                raise HTTPException(
                    status_code=403,
                    detail="You are not a member of this organisation.",
                )

            if admin_membership[1] != "APPROVED":
                raise HTTPException(
                    status_code=403,
                    detail="Your organisation membership is not approved.",
                )

            if admin_membership[0] != "ADMIN":
                raise HTTPException(
                    status_code=403,
                    detail="Only organisation admins can approve join requests.",
                )

            # Check the requested user's membership
            cursor.execute(
                """
                SELECT membership_status
                FROM organisation_members
                WHERE organisation_id = %s
                  AND user_id = %s
                """,
                (organisation_id, user_id),
            )

            membership = cursor.fetchone()

            if not membership:
                raise HTTPException(
                    status_code=404,
                    detail="Join request not found.",
                )

            if membership[0] != "PENDING":
                raise HTTPException(
                    status_code=400,
                    detail="This join request is no longer pending.",
                )

            # Approve the request
            cursor.execute(
                """
                UPDATE organisation_members
                SET membership_status = 'APPROVED',
                    role = 'STAFF'
                WHERE organisation_id = %s
                  AND user_id = %s
                """,
                (organisation_id, user_id),
            )

        conn.commit()

    finally:
        conn.close()

    return {
        "message": "Join request approved successfully.",
        "organisation_id": organisation_id,
        "user_id": user_id,
        "membership_status": "APPROVED",
        "role": "STAFF",
    }

@router.put("/{organisation_id}/join-requests/{user_id}/reject")
def reject_join_request(
    organisation_id: int,
    user_id: int,
    admin_user_id: int = Depends(get_current_user_id),
):
    conn = get_connection()

    try:
        with conn.cursor() as cursor:

            # Check that the organisation exists
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

            # Check that current user is an approved admin
            cursor.execute(
                """
                SELECT role, membership_status
                FROM organisation_members
                WHERE organisation_id = %s
                  AND user_id = %s
                """,
                (organisation_id, admin_user_id),
            )

            admin_membership = cursor.fetchone()

            if not admin_membership:
                raise HTTPException(
                    status_code=403,
                    detail="You are not a member of this organisation.",
                )

            if admin_membership[1] != "APPROVED":
                raise HTTPException(
                    status_code=403,
                    detail="Your organisation membership is not approved.",
                )

            if admin_membership[0] != "ADMIN":
                raise HTTPException(
                    status_code=403,
                    detail="Only organisation admins can reject join requests.",
                )

            # Check the requested user's membership
            cursor.execute(
                """
                SELECT membership_status
                FROM organisation_members
                WHERE organisation_id = %s
                  AND user_id = %s
                """,
                (organisation_id, user_id),
            )

            membership = cursor.fetchone()

            if not membership:
                raise HTTPException(
                    status_code=404,
                    detail="Join request not found.",
                )

            if membership[0] != "PENDING":
                raise HTTPException(
                    status_code=400,
                    detail="This join request is no longer pending.",
                )

            # Reject the request
            cursor.execute(
                """
                UPDATE organisation_members
                SET membership_status = 'REJECTED'
                WHERE organisation_id = %s
                  AND user_id = %s
                """,
                (organisation_id, user_id),
            )

        conn.commit()

    finally:
        conn.close()

    return {
        "message": "Join request rejected.",
        "organisation_id": organisation_id,
        "user_id": user_id,
        "membership_status": "REJECTED",
    }

@router.put("/{organisation_id}/members/{user_id}/role")
def change_member_role(
    organisation_id: int,
    user_id: int,
    new_role: str,
    admin_user_id: int = Depends(get_current_user_id),
):
    if new_role not in ("ADMIN", "STAFF"):
        raise HTTPException(
            status_code=400,
            detail="Role must be ADMIN or STAFF.",
        )

    conn = get_connection()

    try:
        with conn.cursor() as cursor:

            # Check requester is an approved admin
            cursor.execute(
                """
                SELECT role, membership_status
                FROM organisation_members
                WHERE organisation_id = %s
                  AND user_id = %s
                """,
                (organisation_id, admin_user_id),
            )

            admin_membership = cursor.fetchone()

            if not admin_membership:
                raise HTTPException(
                    status_code=403,
                    detail="You are not a member of this organisation.",
                )

            if admin_membership[1] != "APPROVED":
                raise HTTPException(
                    status_code=403,
                    detail="Your organisation membership is not approved.",
                )

            if admin_membership[0] != "ADMIN":
                raise HTTPException(
                    status_code=403,
                    detail="Only organisation admins can change member roles.",
                )

            # Prevent changing your own role
            if user_id == admin_user_id:
                raise HTTPException(
                    status_code=400,
                    detail="You cannot change your own role.",
                )

            # Check target member
            cursor.execute(
                """
                SELECT role, membership_status
                FROM organisation_members
                WHERE organisation_id = %s
                  AND user_id = %s
                """,
                (organisation_id, user_id),
            )

            member = cursor.fetchone()

            if not member:
                raise HTTPException(
                    status_code=404,
                    detail="Member not found.",
                )

            if member[1] != "APPROVED":
                raise HTTPException(
                    status_code=400,
                    detail="Only approved members can have their role changed.",
                )

            # Prevent removing the last admin
            if member[0] == "ADMIN" and new_role == "STAFF":
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM organisation_members
                    WHERE organisation_id = %s
                      AND role = 'ADMIN'
                      AND membership_status = 'APPROVED'
                    """,
                    (organisation_id,),
                )

                admin_count = cursor.fetchone()[0]

                if admin_count <= 1:
                    raise HTTPException(
                        status_code=400,
                        detail="The organisation must have at least one admin.",
                    )

            # Update role
            cursor.execute(
                """
                UPDATE organisation_members
                SET role = %s
                WHERE organisation_id = %s
                  AND user_id = %s
                """,
                (new_role, organisation_id, user_id),
            )

        conn.commit()

    finally:
        conn.close()

    return {
        "message": "Member role updated successfully.",
        "organisation_id": organisation_id,
        "user_id": user_id,
        "role": new_role,
    }

@router.delete("/{organisation_id}/members/{user_id}")
def remove_member(
    organisation_id: int,
    user_id: int,
    admin_user_id: int = Depends(get_current_user_id),
):
    conn = get_connection()

    try:
        with conn.cursor() as cursor:

            # Check requester is an approved admin
            cursor.execute(
                """
                SELECT role, membership_status
                FROM organisation_members
                WHERE organisation_id = %s
                  AND user_id = %s
                """,
                (organisation_id, admin_user_id),
            )

            admin_membership = cursor.fetchone()

            if not admin_membership:
                raise HTTPException(
                    status_code=403,
                    detail="You are not a member of this organisation.",
                )

            if admin_membership[1] != "APPROVED":
                raise HTTPException(
                    status_code=403,
                    detail="Your organisation membership is not approved.",
                )

            if admin_membership[0] != "ADMIN":
                raise HTTPException(
                    status_code=403,
                    detail="Only organisation admins can remove members.",
                )

            # Prevent removing yourself
            if user_id == admin_user_id:
                raise HTTPException(
                    status_code=400,
                    detail="You cannot remove yourself from the organisation.",
                )

            # Check target member
            cursor.execute(
                """
                SELECT role, membership_status
                FROM organisation_members
                WHERE organisation_id = %s
                  AND user_id = %s
                """,
                (organisation_id, user_id),
            )

            member = cursor.fetchone()

            if not member:
                raise HTTPException(
                    status_code=404,
                    detail="Member not found.",
                )

            if member[1] != "APPROVED":
                raise HTTPException(
                    status_code=400,
                    detail="Only approved members can be removed.",
                )

            # If target is an admin, make sure another admin remains
            if member[0] == "ADMIN":
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM organisation_members
                    WHERE organisation_id = %s
                      AND role = 'ADMIN'
                      AND membership_status = 'APPROVED'
                    """,
                    (organisation_id,),
                )

                admin_count = cursor.fetchone()[0]

                if admin_count <= 1:
                    raise HTTPException(
                        status_code=400,
                        detail="The organisation must have at least one admin.",
                    )

            # Remove membership
            cursor.execute(
                """
                DELETE FROM organisation_members
                WHERE organisation_id = %s
                  AND user_id = %s
                """,
                (organisation_id, user_id),
            )

        conn.commit()

    finally:
        conn.close()

    return {
        "message": "Member removed successfully.",
        "organisation_id": organisation_id,
        "user_id": user_id,
    }