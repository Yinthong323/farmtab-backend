"""
Device connection status.

One endpoint:   GET /device-status?shelf_ids=4,5,6

A device is ONLINE when its shelf has received a sensor reading in the last
few seconds. Your sensors send a reading every few seconds, so "no reading for
30 seconds" means the device is not sending any more. This is the same data the
live sensor page shows, so the status can never disagree with the readings.

For each shelf it returns:
    state                 "online"   a reading arrived recently
                          "offline"  readings exist, but the last one is old
                          "no_data"  this shelf has never sent a reading
    last_reading_at       when the last reading arrived (Malaysia time), or null
    seconds_since_last    how old the last reading is, or null
    latest                the last pH / EC / temperature / ORP values, or null
    readings_last_hour    how many readings arrived in the last hour

The time limit can be changed with DEVICE_ONLINE_SECONDS in backend/.env.
"""

import logging
import os
from datetime import timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from database.connection import get_connection
from shelves.shelves import (
    check_site_admin_or_staff,
    get_current_user_id,
)

logger = logging.getLogger("farmtab.devices")

router = APIRouter(
    tags=["Devices"],
)

MYT = timezone(timedelta(hours=8))

# A reading older than this means the device is no longer sending
ONLINE_WITHIN_SECONDS = int(os.getenv("DEVICE_ONLINE_SECONDS", "30"))

MAX_SHELVES = 50


def parse_shelf_ids(raw: str):
    ids = []

    for part in raw.split(","):
        part = part.strip()

        if not part:
            continue

        try:
            value = int(part)
        except ValueError:
            raise HTTPException(status_code=400, detail="shelf_ids must be numbers separated by commas.")

        if value not in ids:
            ids.append(value)

    if not ids:
        raise HTTPException(status_code=400, detail="Please give at least one shelf id.")

    if len(ids) > MAX_SHELVES:
        raise HTTPException(status_code=400, detail=f"At most {MAX_SHELVES} shelves at a time.")

    return ids


@router.get("/device-status")
def get_device_status(
    shelf_ids: str = Query(..., description="Shelf ids separated by commas, e.g. 4,5,6"),
    user_id: int = Depends(get_current_user_id),
):
    wanted = parse_shelf_ids(shelf_ids)

    connection = get_connection()

    try:
        cursor = connection.cursor()

        # Which of the requested shelves exist, and which site each belongs to
        cursor.execute(
            "SELECT id, site_id FROM shelves WHERE id = ANY(%s)",
            (wanted,),
        )
        shelves = {row[0]: row[1] for row in cursor.fetchall()}

        # The user must have access to every site involved
        for site_id in sorted(set(shelves.values())):
            check_site_admin_or_staff(connection, site_id, user_id)

        known = list(shelves.keys())

        latest = {}
        counts = {}

        if known:
            # The newest reading of each shelf. The age is measured by the
            # database clock, so it does not depend on this server's clock.
            cursor.execute(
                """
                SELECT DISTINCT ON (shelf_id)
                    shelf_id,
                    recorded_at,
                    ph,
                    ec,
                    orp,
                    temperature,
                    EXTRACT(EPOCH FROM (now() - recorded_at))::float8 AS age_seconds
                FROM sensor_readings
                WHERE shelf_id = ANY(%s)
                ORDER BY shelf_id, recorded_at DESC
                """,
                (known,),
            )

            for row in cursor.fetchall():
                latest[row[0]] = row

            cursor.execute(
                """
                SELECT shelf_id, COUNT(*)
                FROM sensor_readings
                WHERE shelf_id = ANY(%s)
                  AND recorded_at > now() - interval '1 hour'
                GROUP BY shelf_id
                """,
                (known,),
            )

            counts = {row[0]: row[1] for row in cursor.fetchall()}

        cursor.close()

    finally:
        connection.close()

    result = []

    for shelf_id in wanted:
        if shelf_id not in shelves:
            continue  # unknown shelf: leave it out

        row = latest.get(shelf_id)

        if row is None:
            result.append(
                {
                    "shelf_id": shelf_id,
                    "site_id": shelves[shelf_id],
                    "state": "no_data",
                    "is_online": False,
                    "last_reading_at": None,
                    "seconds_since_last": None,
                    "latest": None,
                    "readings_last_hour": 0,
                }
            )
            continue

        recorded_at, ph, ec, orp, temperature, age = row[1:]

        # A reading time slightly in the future (clock difference) counts as "now"
        age = max(0.0, age)

        online = age <= ONLINE_WITHIN_SECONDS

        result.append(
            {
                "shelf_id": shelf_id,
                "site_id": shelves[shelf_id],
                "state": "online" if online else "offline",
                "is_online": online,
                "last_reading_at": recorded_at.astimezone(MYT).isoformat(),
                "seconds_since_last": round(age, 1),
                "latest": {
                    "ph": ph,
                    "ec": ec,
                    "temperature": temperature,
                    "orp": orp,
                },
                "readings_last_hour": counts.get(shelf_id, 0),
            }
        )

    return result