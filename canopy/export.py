"""
Dataset export: turns daily_cycle_records into a machine learning CSV.

One CSV row = one cycle on one day, using ONLY information from Day 1 up to that day
(no future data leaks into the features). The labels come from the harvest at the
end of the cycle.

Run inside the backend container and save the output on the server:
    python -m canopy.export > dataset.csv                   (harvested cycles only = training data)
    python -m canopy.export --include-active > preview.csv  (also active cycles, labels left empty)

Train with a split BY CYCLE (for example scikit-learn GroupKFold on cycle_id),
never by row, otherwise days of the same cycle land in both train and test.
"""

import argparse
import csv
import sys

from canopy.canopy import DEFAULT_METHOD
from database.connection import get_connection

READINGS_PER_FULL_DAY = 17280          # one sensor reading every 5 seconds

COLUMNS = [
    # --- identifiers (not features) ---
    "cycle_id", "layer_code", "record_date",
    # --- features: what is known on this day, using days 1..k only ---
    "plant_age_day",
    "canopy_pixels", "canopy_cm2",
    "canopy_rel_first",            # today's pixels / this cycle's first measured pixels
    "canopy_avg_3",                # mean of the last 3 measured days
    "growth_px_per_day",           # pixel change per day since the previous measured day
    "growth_pct_per_day",          # % change per day since the previous measured day
    "growth_pct_per_day_3",        # % change per day over (up to) the last 3 measured days
    "days_with_photo_so_far",
    "avg_ph", "avg_ec", "avg_temperature", "avg_orp",                  # today
    "ph_mean_to_date", "ec_mean_to_date",
    "temperature_mean_to_date", "orp_mean_to_date",                    # Day 1 .. today
    "sensor_coverage",             # 1.0 = a full day of sensor readings
    "plant_count",
    "reduced_light",               # 1 if the cycle's notes mention reduced light (one LED)
    # --- labels: known only after harvest ---
    "days_to_harvest",             # main target
    "harvest_canopy_pixels", "harvest_canopy_cm2", "harvest_canopy_rel",
    "final_weight_g",
]


def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def rounded(value, digits=4):
    return None if value is None else round(float(value), digits)


def build_cycle_rows(cycle, daily, method):
    (cycle_id, layer_code, status, transplant_date, plant_count, harvest_date,
     harvest_px, harvest_cm2, weight_g, notes) = cycle

    def usable(row):
        # one consistent canopy method only
        return row["px"] is not None and row["method"] == method

    measured_all = [(r["age"], r["px"]) for r in daily if usable(r)]
    first_px_cycle = measured_all[0][1] if measured_all else None

    harvest_age = None
    if status == "HARVESTED" and harvest_date is not None:
        harvest_age = (harvest_date - transplant_date).days + 1

    reduced_light = 1 if "reduced light" in (notes or "").lower() else 0

    out = []

    for i, row in enumerate(daily):
        age = row["age"]

        # Never export days after the harvest day
        if harvest_age is not None and age > harvest_age:
            continue

        history = daily[: i + 1]
        measured = [(r["age"], r["px"]) for r in history if usable(r)]
        this_px = row["px"] if usable(row) else None

        # ----- canopy features (only on days that have a measured photo) -----
        first_px = measured[0][1] if measured else None
        rel = this_px / first_px if (this_px and first_px) else None

        growth_px = growth_pct = growth_pct_3 = avg3 = None

        if this_px is not None:
            avg3 = mean([px for _, px in measured[-3:]])

            if len(measured) >= 2:
                prev_age, prev_px = measured[-2]
                gap = age - prev_age
                growth_px = (this_px - prev_px) / gap
                growth_pct = ((this_px / prev_px) - 1) * 100 / gap if prev_px else None

                ref_age, ref_px = measured[-4] if len(measured) >= 4 else measured[0]
                gap3 = age - ref_age
                growth_pct_3 = ((this_px / ref_px) - 1) * 100 / gap3 if (ref_px and gap3) else None

        # ----- sensor features -----
        coverage = None
        if row["readings"] is not None:
            coverage = min(1.0, row["readings"] / READINGS_PER_FULL_DAY)

        record = {
            "cycle_id": cycle_id,
            "layer_code": layer_code,
            "record_date": row["date"],
            "plant_age_day": age,
            "canopy_pixels": this_px,
            "canopy_cm2": row["cm2"] if this_px is not None else None,
            "canopy_rel_first": rounded(rel),
            "canopy_avg_3": rounded(avg3, 1),
            "growth_px_per_day": rounded(growth_px, 1),
            "growth_pct_per_day": rounded(growth_pct, 3),
            "growth_pct_per_day_3": rounded(growth_pct_3, 3),
            "days_with_photo_so_far": len(measured),
            "avg_ph": rounded(row["ph"]),
            "avg_ec": rounded(row["ec"], 2),
            "avg_temperature": rounded(row["temp"], 2),
            "avg_orp": rounded(row["orp"], 2),
            "ph_mean_to_date": rounded(mean([r["ph"] for r in history])),
            "ec_mean_to_date": rounded(mean([r["ec"] for r in history]), 2),
            "temperature_mean_to_date": rounded(mean([r["temp"] for r in history]), 2),
            "orp_mean_to_date": rounded(mean([r["orp"] for r in history]), 2),
            "sensor_coverage": rounded(coverage, 3),
            "plant_count": plant_count,
            "reduced_light": reduced_light,
        }

        # ----- labels (harvested cycles only) -----
        if harvest_age is not None:
            record["days_to_harvest"] = harvest_age - age
            record["harvest_canopy_pixels"] = harvest_px
            record["harvest_canopy_cm2"] = harvest_cm2
            record["harvest_canopy_rel"] = rounded(
                harvest_px / first_px_cycle if (harvest_px and first_px_cycle) else None
            )
            record["final_weight_g"] = weight_g

        out.append(record)

    return out


def main():
    parser = argparse.ArgumentParser(description="Export the ML dataset")
    parser.add_argument("--include-active", action="store_true",
                        help="also export active cycles (labels stay empty)")
    parser.add_argument("--method", default=DEFAULT_METHOD,
                        help="canopy method to use (default: the current one)")
    args = parser.parse_args()

    statuses = ["HARVESTED", "ACTIVE"] if args.include_active else ["HARVESTED"]

    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                c.id, l.code, c.status, c.transplant_date, c.plant_count,
                c.harvest_date, c.harvest_canopy_pixels, c.harvest_canopy_cm2,
                c.final_weight_g, c.notes
            FROM layer_cycles c
            JOIN layers l
                ON l.id = c.layer_id
            WHERE c.status = ANY(%s)
            ORDER BY c.id
            """,
            (statuses,),
        )
        cycles = cursor.fetchall()

        writer = csv.DictWriter(sys.stdout, fieldnames=COLUMNS)
        writer.writeheader()

        total_rows = 0

        for cycle in cycles:
            cursor.execute(
                """
                SELECT
                    plant_age_day, record_date, canopy_pixels, canopy_cm2,
                    canopy_method, avg_ph, avg_ec, avg_temperature, avg_orp,
                    sensor_reading_count
                FROM daily_cycle_records
                WHERE cycle_id = %s
                ORDER BY plant_age_day
                """,
                (cycle[0],),
            )

            daily = [
                {
                    "age": r[0], "date": r[1], "px": r[2], "cm2": r[3],
                    "method": r[4], "ph": r[5], "ec": r[6], "temp": r[7],
                    "orp": r[8], "readings": r[9],
                }
                for r in cursor.fetchall()
            ]

            rows = build_cycle_rows(cycle, daily, args.method)
            writer.writerows(rows)
            total_rows += len(rows)

        cursor.close()

        print(
            f"exported {total_rows} rows from {len(cycles)} cycle(s) "
            f"(statuses: {', '.join(statuses)}; canopy method: {args.method})",
            file=sys.stderr,
        )

    finally:
        connection.close()


if __name__ == "__main__":
    main()
