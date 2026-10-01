from datetime import timedelta
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from auth.security import verify_access_token
from database.connection import get_connection

from .schemas import (
    GrowingCycleResponse,
    CreateGrowingCycleRequest,
    StopGrowingCycleRequest,
)


router = APIRouter(
    prefix="/sites",
    tags=["Growing Cycles"],
)

security = HTTPBearer()


# ============================================================
# AUTHENTICATION
# ============================================================

def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> int:
    token = credentials.credentials
    return verify_access_token(token)


# ============================================================
# SHELF + MEMBERSHIP CHECK
# ============================================================

def _get_shelf_and_membership(
    connection,
    shelf_id: int,
    user_id: int,
):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                s.id,
                s.site_id,
                s.crop_type,
                om.role,
                om.membership_status
            FROM shelves s
            JOIN sites si
                ON si.id = s.site_id
            JOIN organisation_members om
                ON om.organisation_id = si.organisation_id
            WHERE s.id = %s
              AND om.user_id = %s
            """,
            (shelf_id, user_id),
        )

        result = cursor.fetchone()

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Shelf not found or you do not have access to it.",
        )

    (
        shelf_id_result,
        site_id,
        crop_type,
        role,
        membership_status,
    ) = result

    if membership_status != "APPROVED":
        raise HTTPException(
            status_code=403,
            detail="Your organisation membership is not approved.",
        )

    return (
        shelf_id_result,
        site_id,
        crop_type,
        role,
    )


# ============================================================
# RESPONSE HELPER
# ============================================================

def _build_cycle_response(row):
    return GrowingCycleResponse(
        id=row[0],
        shelf_id=row[1],
        cycle_number=row[2],
        crop_type=row[3],
        start_date=row[4],
        target_harvest_days=row[5],
        target_harvest_date=row[6],
        actual_harvest_date=row[7],
        actual_growth_days=row[8],
        status=row[9],
        stop_reason=row[10],
        created_at=row[11],
        updated_at=row[12],
    )


# ============================================================
# CREATE GROWING CYCLE
# ============================================================

@router.post(
    "/{site_id}/shelves/{shelf_id}/growing-cycles",
    response_model=GrowingCycleResponse,
)
def create_growing_cycle(
    site_id: int,
    shelf_id: int,
    request: CreateGrowingCycleRequest,
    user_id: int = Depends(get_current_user_id),
):
    connection = get_connection()

    try:
        (
            shelf_id_result,
            actual_site_id,
            crop_type,
            role,
        ) = _get_shelf_and_membership(
            connection,
            shelf_id,
            user_id,
        )

        # ----------------------------------------------------
        # Check site
        # ----------------------------------------------------

        if actual_site_id != site_id:
            raise HTTPException(
                status_code=404,
                detail="Shelf does not belong to this site.",
            )

        # ----------------------------------------------------
        # Only Admin can start a cycle
        # ----------------------------------------------------

        if role != "ADMIN":
            raise HTTPException(
                status_code=403,
                detail="Only organisation admins can start a growing cycle.",
            )

        with connection.cursor() as cursor:

            # ------------------------------------------------
            # Check active cycle
            # ------------------------------------------------

            cursor.execute(
                """
                SELECT id
                FROM growing_cycles
                WHERE shelf_id = %s
                  AND status = 'ACTIVE'
                LIMIT 1
                """,
                (shelf_id,),
            )

            active_cycle = cursor.fetchone()

            if active_cycle is not None:
                raise HTTPException(
                    status_code=409,
                    detail="This shelf already has an active growing cycle.",
                )

            # ------------------------------------------------
            # Get next cycle number
            # ------------------------------------------------

            cursor.execute(
                """
                SELECT COALESCE(MAX(cycle_number), 0) + 1
                FROM growing_cycles
                WHERE shelf_id = %s
                """,
                (shelf_id,),
            )

            next_cycle_number = cursor.fetchone()[0]

            # ------------------------------------------------
            # Calculate target harvest date
            # ------------------------------------------------

            target_harvest_date = (
                request.start_date
                + timedelta(
                    days=request.target_harvest_days
                )
            )

            # ------------------------------------------------
            # Create cycle
            # ------------------------------------------------

            cursor.execute(
                """
                INSERT INTO growing_cycles (
                    shelf_id,
                    cycle_number,
                    crop_type,
                    start_date,
                    target_harvest_days,
                    target_harvest_date,
                    status
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    'ACTIVE'
                )
                RETURNING
                    id,
                    shelf_id,
                    cycle_number,
                    crop_type,
                    start_date,
                    target_harvest_days,
                    target_harvest_date,
                    actual_harvest_date,
                    actual_growth_days,
                    status,
                    stop_reason,
                    created_at,
                    updated_at
                """,
                (
                    shelf_id,
                    next_cycle_number,
                    crop_type,
                    request.start_date,
                    request.target_harvest_days,
                    target_harvest_date,
                ),
            )

            row = cursor.fetchone()

        connection.commit()

        return _build_cycle_response(row)

    except HTTPException:
        connection.rollback()
        raise

    except Exception as e:
        connection.rollback()

        raise HTTPException(
            status_code=500,
            detail=str(e),
        )

    finally:
        connection.close()


# ============================================================
# GET ACTIVE CYCLE
# ============================================================

@router.get(
    "/{site_id}/shelves/{shelf_id}/growing-cycles/active",
    response_model=GrowingCycleResponse | None,
)
def get_active_growing_cycle(
    site_id: int,
    shelf_id: int,
    user_id: int = Depends(get_current_user_id),
):
    connection = get_connection()

    try:
        (
            shelf_id_result,
            actual_site_id,
            crop_type,
            role,
        ) = _get_shelf_and_membership(
            connection,
            shelf_id,
            user_id,
        )

        if actual_site_id != site_id:
            raise HTTPException(
                status_code=404,
                detail="Shelf does not belong to this site.",
            )

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    shelf_id,
                    cycle_number,
                    crop_type,
                    start_date,
                    target_harvest_days,
                    target_harvest_date,
                    actual_harvest_date,
                    actual_growth_days,
                    status,
                    stop_reason,
                    created_at,
                    updated_at
                FROM growing_cycles
                WHERE shelf_id = %s
                  AND status = 'ACTIVE'
                ORDER BY cycle_number DESC
                LIMIT 1
                """,
                (shelf_id,),
            )

            row = cursor.fetchone()

        if row is None:
            return None

        return _build_cycle_response(row)

    finally:
        connection.close()


# ============================================================
# GET ALL CYCLES
# ============================================================

@router.get(
    "/{site_id}/shelves/{shelf_id}/growing-cycles",
    response_model=list[GrowingCycleResponse],
)
def get_growing_cycles(
    site_id: int,
    shelf_id: int,
    user_id: int = Depends(get_current_user_id),
):
    connection = get_connection()

    try:
        (
            shelf_id_result,
            actual_site_id,
            crop_type,
            role,
        ) = _get_shelf_and_membership(
            connection,
            shelf_id,
            user_id,
        )

        if actual_site_id != site_id:
            raise HTTPException(
                status_code=404,
                detail="Shelf does not belong to this site.",
            )

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    shelf_id,
                    cycle_number,
                    crop_type,
                    start_date,
                    target_harvest_days,
                    target_harvest_date,
                    actual_harvest_date,
                    actual_growth_days,
                    status,
                    stop_reason,
                    created_at,
                    updated_at
                FROM growing_cycles
                WHERE shelf_id = %s
                ORDER BY cycle_number DESC
                """,
                (shelf_id,),
            )

            rows = cursor.fetchall()

        return [
            _build_cycle_response(row)
            for row in rows
        ]

    finally:
        connection.close()

@router.put(
    "/{site_id}/shelves/{shelf_id}/growing-cycles/{cycle_id}/stop",
    response_model=GrowingCycleResponse,
)
def stop_growing_cycle(
    site_id: int,
    shelf_id: int,
    cycle_id: int,
    request: StopGrowingCycleRequest,
    user_id: int = Depends(get_current_user_id),
):
    connection = get_connection()

    try:
        shelf_id_result, actual_site_id, crop_type, role = (
            _get_shelf_and_membership(
                connection,
                shelf_id,
                user_id,
            )
        )

        if actual_site_id != site_id:
            raise HTTPException(
                status_code=404,
                detail="Shelf does not belong to this site.",
            )

        if role != "ADMIN":
            raise HTTPException(
                status_code=403,
                detail="Only organisation admins can stop a growing cycle.",
            )

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    start_date,
                    status
                FROM growing_cycles
                WHERE id = %s
                  AND shelf_id = %s
                """,
                (cycle_id, shelf_id),
            )

            cycle = cursor.fetchone()

            if cycle is None:
                raise HTTPException(
                    status_code=404,
                    detail="Growing cycle not found.",
                )

            cycle_id_db, start_date, status = cycle

            if status != "ACTIVE":
                raise HTTPException(
                    status_code=400,
                    detail="Only an active growing cycle can be stopped.",
                )

            actual_growth_days = (
                date.today() - start_date
            ).days + 1

            cursor.execute(
                """
                UPDATE growing_cycles
                SET
                    status = 'STOPPED',
                    actual_growth_days = %s,
                    stop_reason = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                RETURNING
                    id,
                    shelf_id,
                    cycle_number,
                    crop_type,
                    start_date,
                    target_harvest_days,
                    target_harvest_date,
                    actual_harvest_date,
                    actual_growth_days,
                    status,
                    stop_reason,
                    created_at,
                    updated_at
                """,
                (
                    actual_growth_days,
                    request.stop_reason,
                    cycle_id_db,
                ),
            )

            row = cursor.fetchone()

            connection.commit()

            return _build_cycle_response(row)

    finally:
        connection.close()