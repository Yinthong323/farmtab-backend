"""
Canopy measurement for layer photos.

- measure_image(image_id): measures one photo and saves the result in
  canopy_measurements. Called automatically after each upload.
- Backfill (measure photos that have no result yet), run inside the backend container:
      python -m canopy.canopy
      python -m canopy.canopy --method hsv_v1 --all     (re-measure every photo)
      python -m canopy.canopy --image-id 12             (one photo)
"""

import argparse
import os

import cv2
import numpy as np

from canopy.daily import update_daily_records_for_image
from database.connection import get_connection

# ------------------------------------------------------------
# Methods. Tuning = add a new entry (e.g. "hsv_v2"); never edit an
# existing one, so old results stay reproducible.
# HSV in OpenCV: hue 0-179, saturation 0-255, value 0-255.
# ------------------------------------------------------------
METHODS = {
    # First version, tuned on one pi1 photo
    "hsv_v1": {
        "lower": (25, 80, 90),
        "upper": (90, 255, 255),
        "min_blob_px": 40,
    },
    # Tuned on the 25 Oct 8 PM photos of five cameras. Accepts paler, less
    # saturated leaf colour (pi5/pi6 render leaves paler), leaf edges and
    # stems; the brightness cap (235) drops most LED-bar glare.
    "hsv_v2": {
        "lower": (22, 50, 85),
        "upper": (90, 255, 235),
        "min_blob_px": 60,
    },
}

# The method used for new photos, the cards, the detail page and the daily records
DEFAULT_METHOD = "hsv_v2"


def green_mask(img, params):
    """Return a 0/255 mask of leaf pixels."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

    mask = cv2.inRange(
        hsv,
        np.array(params["lower"], dtype=np.uint8),
        np.array(params["upper"], dtype=np.uint8),
    )

    # Remove single-pixel noise
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

    # Remove small specks below min_blob_px
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    keep_ids = [
        i for i in range(1, n)
        if stats[i, cv2.CC_STAT_AREA] >= params["min_blob_px"]
    ]

    return (np.isin(labels, keep_ids).astype(np.uint8)) * 255


def _save_result(cursor, image_id, layer_id, method, pixels, cm2,
                 calibration_id, mask_url, overlay_url, status, note):
    cursor.execute(
        """
        INSERT INTO canopy_measurements (
            image_id,
            layer_id,
            method,
            canopy_pixels,
            canopy_cm2,
            calibration_id,
            mask_path,
            overlay_path,
            status,
            error,
            processed_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (image_id, method) DO UPDATE SET
            canopy_pixels  = EXCLUDED.canopy_pixels,
            canopy_cm2     = EXCLUDED.canopy_cm2,
            calibration_id = EXCLUDED.calibration_id,
            mask_path      = EXCLUDED.mask_path,
            overlay_path   = EXCLUDED.overlay_path,
            status         = EXCLUDED.status,
            error          = EXCLUDED.error,
            processed_at   = CURRENT_TIMESTAMP
        """,
        (
            image_id,
            layer_id,
            method,
            pixels,
            cm2,
            calibration_id,
            mask_url,
            overlay_url,
            status,
            note,
        ),
    )


def measure_image(image_id: int, method: str = DEFAULT_METHOD) -> dict:
    """Measure one photo and save the result. Never raises; failures are saved."""
    params = METHODS[method]
    connection = get_connection()
    layer_id = None

    try:
        cursor = connection.cursor()

        # ----------------------------------------------------
        # 1. Find the photo
        # ----------------------------------------------------
        cursor.execute(
            """
            SELECT layer_id, captured_at, file_path
            FROM layer_images
            WHERE id = %s
            """,
            (image_id,),
        )
        row = cursor.fetchone()

        if not row:
            cursor.close()
            return {"image_id": image_id, "status": "skipped", "note": "image not found"}

        layer_id, captured_at, file_path = row

        # Stored as '/uploads/...'; on disk it is 'uploads/...' inside /app
        disk_path = file_path.lstrip("/")
        img = cv2.imread(disk_path)

        if img is None:
            raise RuntimeError(f"cannot read image file {disk_path}")

        height, width = img.shape[:2]

        # ----------------------------------------------------
        # 2. Green-pixel mask and count
        # ----------------------------------------------------
        mask = green_mask(img, params)
        pixels = int(cv2.countNonZero(mask))

        # ----------------------------------------------------
        # 3. Calibration valid at the moment the photo was taken
        # ----------------------------------------------------
        cursor.execute(
            """
            SELECT id, cm2_per_pixel, image_width, image_height
            FROM camera_calibrations
            WHERE layer_id = %s
              AND valid_from <= %s
              AND (valid_to IS NULL OR valid_to > %s)
            ORDER BY valid_from DESC
            LIMIT 1
            """,
            (layer_id, captured_at, captured_at),
        )
        calibration = cursor.fetchone()

        cm2 = None
        calibration_id = None
        note = None

        if calibration is None:
            note = "no calibration valid at capture time"
        elif (calibration[2], calibration[3]) != (width, height):
            calibration_id = calibration[0]
            note = (
                f"image size {width}x{height} does not match "
                f"calibration {calibration[2]}x{calibration[3]}"
            )
        else:
            calibration_id = calibration[0]
            cm2 = round(pixels * float(calibration[1]), 2)

        # ----------------------------------------------------
        # 4. Save mask and overlay next to the original photo
        # ----------------------------------------------------
        base = os.path.splitext(disk_path)[0]
        mask_disk = f"{base}_mask_{method}.png"
        overlay_disk = f"{base}_overlay_{method}.jpg"

        cv2.imwrite(mask_disk, mask)

        overlay = img.copy()
        overlay[mask > 0] = (255, 0, 255)
        blended = cv2.addWeighted(img, 0.45, overlay, 0.55, 0)
        cv2.imwrite(overlay_disk, blended, [cv2.IMWRITE_JPEG_QUALITY, 85])

        # ----------------------------------------------------
        # 5. Save the result
        # ----------------------------------------------------
        _save_result(
            cursor, image_id, layer_id, method, pixels, cm2, calibration_id,
            "/" + mask_disk, "/" + overlay_disk, "ok", note,
        )

        connection.commit()
        cursor.close()

        # Refresh the daily record of this photo's cycle. A problem here must
        # never undo or hide the measurement itself, so it is only reported.
        try:
            update_daily_records_for_image(image_id, method)
        except Exception as daily_error:
            print(f"daily record update failed for image {image_id}: {daily_error}")

        return {
            "image_id": image_id,
            "status": "ok",
            "canopy_pixels": pixels,
            "canopy_cm2": cm2,
            "note": note,
        }

    except Exception as e:
        connection.rollback()

        # Record the failure so it is visible, not silently missing
        if layer_id is not None:
            try:
                cursor = connection.cursor()
                _save_result(
                    cursor, image_id, layer_id, method, None, None, None,
                    None, None, "failed", str(e),
                )
                connection.commit()
                cursor.close()
            except Exception:
                connection.rollback()

        return {"image_id": image_id, "status": "failed", "note": str(e)}

    finally:
        connection.close()


def backfill(method: str = DEFAULT_METHOD, reprocess_all: bool = False):
    """Measure photos with no result for this method (or failed ones)."""
    connection = get_connection()

    try:
        cursor = connection.cursor()

        if reprocess_all:
            cursor.execute("SELECT id FROM layer_images ORDER BY captured_at")
        else:
            cursor.execute(
                """
                SELECT i.id
                FROM layer_images i
                LEFT JOIN canopy_measurements m
                    ON m.image_id = i.id
                   AND m.method = %s
                WHERE m.id IS NULL
                   OR m.status = 'failed'
                ORDER BY i.captured_at
                """,
                (method,),
            )

        image_ids = [r[0] for r in cursor.fetchall()]
        cursor.close()

    finally:
        connection.close()

    print(f"{len(image_ids)} photo(s) to measure with {method}")

    for image_id in image_ids:
        result = measure_image(image_id, method)
        print(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Canopy measurement")
    parser.add_argument("--method", default=DEFAULT_METHOD, choices=list(METHODS))
    parser.add_argument("--all", action="store_true", help="re-measure every photo")
    parser.add_argument("--image-id", type=int, help="measure one photo only")
    args = parser.parse_args()

    if args.image_id:
        print(measure_image(args.image_id, args.method))
    else:
        backfill(args.method, args.all)
