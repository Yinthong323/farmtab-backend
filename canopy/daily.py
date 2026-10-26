"""
Daily cycle records: one row per cycle per day in daily_cycle_records.

Each row combines, for one layer on one day of its growing cycle:
  - plant age (transplant day = Day 1)
  - the canopy measurement of that day's photo (empty if there is no photo)
  - the average pH / EC / temperature / ORP over that day's sensor window

It runs automatically after every photo is measured (see canopy.py), and can be
run by hand inside the backend container:
    python -m canopy.daily                    (fill missing days of every cycle)
    python -m canopy.daily --cycle-id 7       (one cycle only)
    python -m canopy.daily --all              (recompute every day, e.g. after a new canopy method)
"""

import argparse
from datetime import datetime, time, timedelta, timezone

from database.connection import get_connection

MYT = timezone(timedelta(hours=8))

SCHEDULED_HOUR = 20                                  # the daily 8 PM capture
CYCLE_PHOTO_TYPES = ["scheduled", "start", "harvest"]  # 'manual' test photos never count

# If a day has several photos, the best one represents the day:
# harvest photo > start photo (taken after the plants went in) > the 8 PM photo
PHOTO_PRIORITY = {"harvest": 3, "start": 2, "scheduled": 1}


def _myt(day, hour=0):
    """A Malaysia-time moment: the given date at the given hour."""
    return datetime.combine(day, time(hour, 0), tzinfo=MYT)


def rebuild_cycle_records(
    cycle_id: int,
    method: str,
    force_date=None,
    force_all: bool = False,
) -> int:
    """
    Create or update the daily rows of one cycle.

    - Days that have no row yet are created (also days without a photo).
    - force_date: recompute that one day (a new or late photo arrived for it).
    - force_all: recompute every day.
    Returns the number of rows written.
    """
    connection = get_connection()

    try:
        cursor = connection.cursor()

        # ----------------------------------------------------
        # The cycle, and the shelf its sensors belong to
        # ----------------------------------------------------
        cursor.execute(
            """
            SELECT
                c.layer_id,
                l.shelf_id,
                c.transplant_date,
                c.status,
                c.harvest_date
            FROM layer_cycles c
            JOIN layers l
                ON l.id = c.layer_id
            WHERE c.id = %s
            """,
            (cycle_id,),
        )

        cycle = cursor.fetchone()

        if not cycle:
            return 0

        layer_id, shelf_id, transplant_date, status, harvest_date = cycle

        # An aborted cycle is left as it is (and is never used for training)
        if status not in ("ACTIVE", "HARVESTED"):
            return 0

        today = datetime.now(MYT).date()

        if status == "HARVESTED" and harvest_date is not None:
            last_day = harvest_date
        else:
            last_day = today

        # ----------------------------------------------------
        # Photos of this cycle, grouped by Malaysia date
        # ----------------------------------------------------
        cursor.execute(
            """
            SELECT id, captured_at, capture_type
            FROM layer_images
            WHERE cycle_id = %s
              AND capture_type = ANY(%s)
            """,
            (cycle_id, CYCLE_PHOTO_TYPES),
        )

        photo_by_day = {}

        for image_id, captured_at, capture_type in cursor.fetchall():
            day = captured_at.astimezone(MYT).date()
            rank = (PHOTO_PRIORITY.get(capture_type, 0), captured_at)
            current = photo_by_day.get(day)

            if current is None or rank > current["rank"]:
                photo_by_day[day] = {
                    "rank": rank,
                    "id": image_id,
                    "captured_at": captured_at,
                }

        # ----------------------------------------------------
        # Canopy results of those photos
        # ----------------------------------------------------
        canopy_by_image = {}

        image_ids = [p["id"] for p in photo_by_day.values()]

        if image_ids:
            cursor.execute(
                """
                SELECT image_id, canopy_pixels, canopy_cm2
                FROM canopy_measurements
                WHERE image_id = ANY(%s)
                  AND method = %s
                  AND status = 'ok'
                """,
                (image_ids, method),
            )

            for image_id, pixels, cm2 in cursor.fetchall():
                canopy_by_image[image_id] = (pixels, cm2)

        # ----------------------------------------------------
        # Days that already have a row
        # ----------------------------------------------------
        cursor.execute(
            "SELECT plant_age_day FROM daily_cycle_records WHERE cycle_id = %s",
            (cycle_id,),
        )

        existing_days = {row[0] for row in cursor.fetchall()}

        # ----------------------------------------------------
        # One row per day, from the transplant date
        # ----------------------------------------------------
        written = 0
        day = transplant_date

        while day <= last_day:
            age = (day - transplant_date).days + 1
            photo = photo_by_day.get(day)

            wanted = force_all or age not in existing_days or day == force_date

            # Today is unfinished until its photo exists, so do not
            # create a half-day row from partial sensor data
            incomplete_today = (
                status == "ACTIVE" and day == today and photo is None
            )

            if wanted and not incomplete_today:
                # Window: from the previous 8 PM (but not before the transplant
                # date) up to this day's photo, or 8 PM if there is no photo
                window_start = max(
                    _myt(day - timedelta(days=1), SCHEDULED_HOUR),
                    _myt(transplant_date, 0),
                )

                window_end = (
                    photo["captured_at"]
                    if photo
                    else _myt(day, SCHEDULED_HOUR)
                )

                cursor.execute(
                    """
                    SELECT
                        avg(ph)::float8,
                        avg(ec)::float8,
                        avg(temperature)::float8,
                        avg(orp)::float8,
                        count(*)
                    FROM sensor_readings
                    WHERE shelf_id = %s
                      AND recorded_at > %s
                      AND recorded_at <= %s
                    """,
                    (shelf_id, window_start, window_end),
                )

                avg_ph, avg_ec, avg_temp, avg_orp, reading_count = cursor.fetchone()

                pixels = cm2 = None

                if photo and photo["id"] in canopy_by_image:
                    pixels, cm2 = canopy_by_image[photo["id"]]

                cursor.execute(
                    """
                    INSERT INTO daily_cycle_records (
                        cycle_id,
                        layer_id,
                        plant_age_day,
                        record_date,
                        image_id,
                        canopy_pixels,
                        canopy_cm2,
                        canopy_method,
                        avg_ph,
                        avg_ec,
                        avg_temperature,
                        avg_orp,
                        sensor_reading_count,
                        window_start,
                        window_end,
                        is_harvest_day
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (cycle_id, plant_age_day) DO UPDATE SET
                        record_date          = EXCLUDED.record_date,
                        image_id             = EXCLUDED.image_id,
                        canopy_pixels        = EXCLUDED.canopy_pixels,
                        canopy_cm2           = EXCLUDED.canopy_cm2,
                        canopy_method        = EXCLUDED.canopy_method,
                        avg_ph               = EXCLUDED.avg_ph,
                        avg_ec               = EXCLUDED.avg_ec,
                        avg_temperature      = EXCLUDED.avg_temperature,
                        avg_orp              = EXCLUDED.avg_orp,
                        sensor_reading_count = EXCLUDED.sensor_reading_count,
                        window_start         = EXCLUDED.window_start,
                        window_end           = EXCLUDED.window_end,
                        is_harvest_day       = EXCLUDED.is_harvest_day
                    """,
                    (
                        cycle_id,
                        layer_id,
                        age,
                        day,
                        photo["id"] if photo else None,
                        pixels,
                        cm2,
                        method if pixels is not None else None,
                        avg_ph,
                        avg_ec,
                        avg_temp,
                        avg_orp,
                        reading_count,
                        window_start,
                        window_end,
                        status == "HARVESTED" and day == harvest_date,
                    ),
                )

                written += 1

            day += timedelta(days=1)

        connection.commit()
        cursor.close()

        return written

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def update_daily_records_for_image(image_id: int, method: str) -> int:
    """Called after a photo was measured: refresh the day this photo belongs to."""
    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT cycle_id, captured_at, capture_type
            FROM layer_images
            WHERE id = %s
            """,
            (image_id,),
        )

        row = cursor.fetchone()
        cursor.close()

    finally:
        connection.close()

    if not row:
        return 0

    cycle_id, captured_at, capture_type = row

    # Test photos and photos outside a cycle do not create daily rows
    if cycle_id is None or capture_type not in CYCLE_PHOTO_TYPES:
        return 0

    return rebuild_cycle_records(
        cycle_id,
        method,
        force_date=captured_at.astimezone(MYT).date(),
    )


def backfill(method: str, cycle_id: int | None = None, force_all: bool = False):
    connection = get_connection()

    try:
        cursor = connection.cursor()

        if cycle_id is not None:
            cursor.execute("SELECT id FROM layer_cycles WHERE id = %s", (cycle_id,))
        else:
            cursor.execute(
                """
                SELECT id
                FROM layer_cycles
                WHERE status IN ('ACTIVE', 'HARVESTED')
                ORDER BY id
                """
            )

        cycle_ids = [r[0] for r in cursor.fetchall()]
        cursor.close()

    finally:
        connection.close()

    print(f"{len(cycle_ids)} cycle(s) to update")

    for cid in cycle_ids:
        written = rebuild_cycle_records(cid, method, force_all=force_all)
        print(f"cycle {cid}: {written} row(s) written")


if __name__ == "__main__":
    # Same default as the canopy module
    from canopy.canopy import DEFAULT_METHOD

    parser = argparse.ArgumentParser(description="Daily cycle records")
    parser.add_argument("--cycle-id", type=int)
    parser.add_argument("--all", action="store_true", help="recompute every day")
    args = parser.parse_args()

    backfill(DEFAULT_METHOD, args.cycle_id, args.all)