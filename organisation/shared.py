from fastapi import APIRouter

from database.connection import get_connection


router = APIRouter(
    prefix="/organisations",
    tags=["Organisations"],
)


@router.get("/shared")
def get_shared_organisations():

    conn = get_connection()

    try:
        with conn.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    id,
                    name,
                    description,
                    website,
                    phone_number
                FROM organisations
                WHERE organisation_type = 'SHARED'
                ORDER BY LOWER(name)
                """
            )

            organisations = cursor.fetchall()

    finally:
        conn.close()

    return {
        "organisations": [
            {
                "id": organisation[0],
                "name": organisation[1],
                "description": organisation[2],
                "website": organisation[3],
                "phone_number": organisation[4],
            }
            for organisation in organisations
        ]
    }