# import 'dart:convert';

# import 'package:http/http.dart' as http;
# import 'package:shared_preferences/shared_preferences.dart';

# class LayerService {
#   static const String baseUrl = 'http://cyphyai.usm.my:6000';

#   Future<String> _getAccessToken() async {
#     final prefs = await SharedPreferences.getInstance();
#     final token = prefs.getString('access_token');

#     if (token == null || token.isEmpty) {
#       throw Exception('Access token not found. Please login again.');
#     }

#     return token;
#   }

#   // Reads the backend's error message ("detail") from a failed response.
#   String _errorMessage(http.Response response, String fallback) {
#     try {
#       final body = jsonDecode(response.body);

#       if (body is Map && body['detail'] is String) {
#         return body['detail'] as String;
#       }

#       // Validation errors (422) come back as a list
#       if (body is Map && body['detail'] is List) {
#         return 'Please check the values you entered.';
#       }
#     } catch (_) {
#       // Keep the fallback message.
#     }

#     return fallback;
#   }

#   String _dateString(DateTime date) {
#     return '${date.year.toString().padLeft(4, '0')}-'
#         '${date.month.toString().padLeft(2, '0')}-'
#         '${date.day.toString().padLeft(2, '0')}';
#   }

#   // ------------------------------------------------------------
#   // The six camera layers of a shelf, each with its latest image,
#   // canopy measurement and active growing cycle (if any).
#   // ------------------------------------------------------------
#   Future<List<Map<String, dynamic>>> getShelfLayers({
#     required int siteId,
#     required int shelfId,
#   }) async {
#     final token = await _getAccessToken();

#     final response = await http.get(
#       Uri.parse('$baseUrl/sites/$siteId/shelves/$shelfId/layers'),
#       headers: {'Authorization': 'Bearer $token'},
#     );

#     if (response.statusCode != 200) {
#       throw Exception(
#         _errorMessage(response, 'Unable to load growth monitoring layers.'),
#       );
#     }

#     final data = jsonDecode(response.body) as List<dynamic>;

#     return data
#         .map((item) => Map<String, dynamic>.from(item as Map))
#         .toList();
#   }

#   // ------------------------------------------------------------
#   // Start a growing cycle on one layer.
#   // The result contains "first_capture":
#   //   requested   - a photo is being taken now
#   //   existing    - photos taken since the transplant date were attached
#   //   tonight_8pm - the first photo arrives at the 8 PM capture
#   // ------------------------------------------------------------
#   Future<Map<String, dynamic>> startCycle({
#     required int siteId,
#     required int shelfId,
#     required int layerId,
#     required DateTime transplantDate,
#     required int plantCount,
#     required String variety,
#     String? notes,
#   }) async {
#     final token = await _getAccessToken();

#     final response = await http.post(
#       Uri.parse('$baseUrl/sites/$siteId/shelves/$shelfId/layers/$layerId/cycles'),
#       headers: {
#         'Authorization': 'Bearer $token',
#         'Content-Type': 'application/json',
#       },
#       body: jsonEncode({
#         'transplant_date': _dateString(transplantDate),
#         'plant_count': plantCount,
#         'variety': variety,
#         if (notes != null && notes.isNotEmpty) 'notes': notes,
#       }),
#     );

#     if (response.statusCode != 200) {
#       throw Exception(
#         _errorMessage(response, 'Unable to start growing cycle.'),
#       );
#     }

#     return Map<String, dynamic>.from(jsonDecode(response.body) as Map);
#   }

#   // ------------------------------------------------------------
#   // Abort an active cycle (started by mistake, or a failed batch).
#   // ------------------------------------------------------------
#   Future<void> abortCycle({
#     required int siteId,
#     required int shelfId,
#     required int layerId,
#     required int cycleId,
#     String? reason,
#   }) async {
#     final token = await _getAccessToken();

#     final response = await http.post(
#       Uri.parse(
#         '$baseUrl/sites/$siteId/shelves/$shelfId/layers/$layerId'
#         '/cycles/$cycleId/abort',
#       ),
#       headers: {
#         'Authorization': 'Bearer $token',
#         'Content-Type': 'application/json',
#       },
#       body: jsonEncode({
#         if (reason != null && reason.isNotEmpty) 'reason': reason,
#       }),
#     );

#     if (response.statusCode != 200) {
#       throw Exception(
#         _errorMessage(response, 'Unable to abort growing cycle.'),
#       );
#     }
#   }

#   // ------------------------------------------------------------
#   // One layer's cycle, day by day (Layer Detail page).
#   // Without cycleId: the active cycle, or the latest harvested one.
#   // ------------------------------------------------------------
#   Future<Map<String, dynamic>> getLayerDetail({
#     required int siteId,
#     required int shelfId,
#     required int layerId,
#     int? cycleId,
#   }) async {
#     final token = await _getAccessToken();

#     final query = cycleId == null ? '' : '?cycle_id=$cycleId';

#     final response = await http.get(
#       Uri.parse(
#         '$baseUrl/sites/$siteId/shelves/$shelfId/layers/$layerId/detail$query',
#       ),
#       headers: {'Authorization': 'Bearer $token'},
#     );

#     if (response.statusCode != 200) {
#       throw Exception(
#         _errorMessage(response, 'Unable to load layer details.'),
#       );
#     }

#     return Map<String, dynamic>.from(jsonDecode(response.body) as Map);
#   }

#   // ------------------------------------------------------------
#   // HARVEST
#   // ------------------------------------------------------------

#   String _cyclePath(int siteId, int shelfId, int layerId, int cycleId) {
#     return '$baseUrl/sites/$siteId/shelves/$shelfId/layers/$layerId'
#         '/cycles/$cycleId';
#   }

#   // Which photo will be the final harvest photo?
#   // The result has a "state":
#   //   needs_capture  - no usable photo today; ask the camera for one
#   //   waiting        - the camera has not answered yet
#   //   measuring      - the photo arrived, the canopy is being measured
#   //   ready          - usable ("image" is included)
#   //   measure_failed - the photo could not be measured ("image" is included)
#   //   expired        - the camera did not answer in time
#   Future<Map<String, dynamic>> getHarvestPhoto({
#     required int siteId,
#     required int shelfId,
#     required int layerId,
#     required int cycleId,
#     int? requestId,
#   }) async {
#     final token = await _getAccessToken();

#     final query = requestId == null ? '' : '?request_id=$requestId';

#     final response = await http.get(
#       Uri.parse(
#         '${_cyclePath(siteId, shelfId, layerId, cycleId)}/harvest-photo$query',
#       ),
#       headers: {'Authorization': 'Bearer $token'},
#     );

#     if (response.statusCode != 200) {
#       throw Exception(
#         _errorMessage(response, 'Unable to check the harvest photo.'),
#       );
#     }

#     return Map<String, dynamic>.from(jsonDecode(response.body) as Map);
#   }

#   // Asks the layer's camera to take a photo now. Returns the request id.
#   Future<int> requestHarvestCapture({
#     required int siteId,
#     required int shelfId,
#     required int layerId,
#     required int cycleId,
#   }) async {
#     final token = await _getAccessToken();

#     final response = await http.post(
#       Uri.parse(
#         '${_cyclePath(siteId, shelfId, layerId, cycleId)}/harvest/capture',
#       ),
#       headers: {'Authorization': 'Bearer $token'},
#     );

#     if (response.statusCode != 200) {
#       throw Exception(
#         _errorMessage(response, 'Unable to request a photo.'),
#       );
#     }

#     final body = jsonDecode(response.body) as Map;

#     return body['request_id'] as int;
#   }

#   // Ends the cycle and records its final result.
#   Future<Map<String, dynamic>> harvestCycle({
#     required int siteId,
#     required int shelfId,
#     required int layerId,
#     required int cycleId,
#     int? imageId,
#     double? finalWeightG,
#     String? note,
#   }) async {
#     final token = await _getAccessToken();

#     final response = await http.post(
#       Uri.parse('${_cyclePath(siteId, shelfId, layerId, cycleId)}/harvest'),
#       headers: {
#         'Authorization': 'Bearer $token',
#         'Content-Type': 'application/json',
#       },
#       body: jsonEncode({
#         'image_id': imageId,
#         'final_weight_g': finalWeightG,
#         if (note != null && note.isNotEmpty) 'harvest_note': note,
#       }),
#     );

#     if (response.statusCode != 200) {
#       throw Exception(
#         _errorMessage(response, 'Unable to harvest the growing cycle.'),
#       );
#     }

#     return Map<String, dynamic>.from(jsonDecode(response.body) as Map);
#   }

#   // Turns '/uploads/layers/...' into a full URL the app can load.
#   static String imageUrl(String path) => '$baseUrl$path';
# }

import hashlib
import os
from datetime import datetime, timedelta, timezone
from io import BytesIO

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
)

from canopy.canopy import DEFAULT_METHOD, measure_image
from database.connection import get_connection
from shelves.shelves import (
    check_site_admin_or_staff,
    get_current_user_id,
)

router = APIRouter(
    tags=["Layers"],
)

MYT = timezone(timedelta(hours=8))          # Malaysia time, used for folder and file names
MAX_IMAGE_BYTES = 15 * 1024 * 1024          # 15 MB per photo
ALLOWED_CAPTURE_TYPES = {"scheduled", "start", "harvest", "manual"}
UPLOAD_ROOT = "uploads/layers"              # inside the container: /app/uploads/layers


def read_image_size(data: bytes):
    """Return (width, height) if Pillow is installed, otherwise (None, None)."""
    try:
        from PIL import Image

        with Image.open(BytesIO(data)) as img:
            return img.width, img.height
    except Exception:
        return None, None


# ============================================================
# UPLOAD IMAGE FROM A RASPBERRY PI CAMERA
# ============================================================
# Called by the Pi, not by the app. The Pi proves who it is with its
# own device token (header X-Device-Token), not with a user login.

@router.post("/layers/upload")
def upload_layer_image(
    background_tasks: BackgroundTasks,
    layer_code: str = Form(...),
    captured_at: str = Form(...),
    capture_type: str = Form("scheduled"),
    request_id: int | None = Form(None),
    image: UploadFile = File(...),
    x_device_token: str | None = Header(None),
):
    # ----------------------------------------------------
    # Validate the request before touching the database
    # ----------------------------------------------------

    if not x_device_token:
        raise HTTPException(
            status_code=401,
            detail="Missing device token.",
        )

    try:
        captured = datetime.fromisoformat(captured_at.strip())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="captured_at must look like 2026-10-25T20:00:00+08:00.",
        )

    if captured.tzinfo is None:
        raise HTTPException(
            status_code=400,
            detail="captured_at must include a time zone offset, e.g. +08:00.",
        )

    if captured > datetime.now(timezone.utc) + timedelta(minutes=10):
        raise HTTPException(
            status_code=400,
            detail="captured_at is in the future. Check the Pi's clock.",
        )

    capture_type = capture_type.strip().lower()

    if capture_type not in ALLOWED_CAPTURE_TYPES:
        raise HTTPException(
            status_code=400,
            detail="capture_type must be scheduled, start, harvest or manual.",
        )

    if image.content_type != "image/jpeg":
        raise HTTPException(
            status_code=400,
            detail="Only JPG images are allowed.",
        )

    data = image.file.read()

    if not data:
        raise HTTPException(
            status_code=400,
            detail="Image file is empty.",
        )

    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Image is larger than 15 MB.",
        )

    token_hash = hashlib.sha256(x_device_token.encode()).hexdigest()

    connection = get_connection()
    file_on_disk = None
    file_written = False

    try:
        cursor = connection.cursor()

        # ----------------------------------------------------
        # Identify the layer from its device token
        # ----------------------------------------------------

        cursor.execute(
            """
            SELECT
                id,
                shelf_id,
                code
            FROM layers
            WHERE upload_token_hash = %s
            """,
            (token_hash,),
        )

        layer = cursor.fetchone()

        if not layer:
            cursor.close()

            raise HTTPException(
                status_code=401,
                detail="Invalid device token.",
            )

        layer_id, shelf_id, code = layer

        if code != layer_code.strip():
            cursor.close()

            raise HTTPException(
                status_code=403,
                detail="This token does not belong to that layer.",
            )

        # ----------------------------------------------------
        # Attach to the active cycle, if one is running
        # ----------------------------------------------------

        local_time = captured.astimezone(MYT)

        cursor.execute(
            """
            SELECT id
            FROM layer_cycles
            WHERE layer_id = %s
              AND status = 'ACTIVE'
              AND transplant_date <= %s
            """,
            (
                layer_id,
                local_time.date(),
            ),
        )

        cycle = cursor.fetchone()
        # Only real captures join a cycle. Test ('manual') photos never do.
        cycle_id = (
            cycle[0]
            if cycle and capture_type in ("scheduled", "start", "harvest")
            else None
        )

        # ----------------------------------------------------
        # Decide where the file goes
        # uploads/layers/<shelf>/<code>/<YYYY-MM>/<code>_<date>_<time>_<type>.jpg
        # ----------------------------------------------------

        relative_directory = os.path.join(
            UPLOAD_ROOT,
            str(shelf_id),
            code,
            local_time.strftime("%Y-%m"),
        )

        filename = (
            f"{code}_{local_time.strftime('%Y%m%d_%H%M%S')}_{capture_type}.jpg"
        )

        file_on_disk = os.path.join(relative_directory, filename)
        file_url = "/" + file_on_disk.replace(os.sep, "/")

        width, height = read_image_size(data)

        # ----------------------------------------------------
        # Insert the record. A retried upload of the same photo
        # (same layer + same captured_at) is ignored, not duplicated.
        # ----------------------------------------------------

        cursor.execute(
            """
            INSERT INTO layer_images (
                layer_id,
                cycle_id,
                captured_at,
                capture_type,
                file_path,
                width,
                height,
                file_size_bytes,
                sha256
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (layer_id, captured_at) DO NOTHING
            RETURNING id
            """,
            (
                layer_id,
                cycle_id,
                captured,
                capture_type,
                file_url,
                width,
                height,
                len(data),
                hashlib.sha256(data).hexdigest(),
            ),
        )

        row = cursor.fetchone()

        if not row:
            connection.rollback()
            cursor.close()

            return {
                "message": "Image was already uploaded.",
                "duplicate": True,
            }

        image_id = row[0]

        # If this photo answers a "capture now" job, mark the job done
        if request_id is not None:
            cursor.execute(
                """
                UPDATE capture_requests
                SET status = 'DONE',
                    image_id = %s,
                    completed_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND layer_id = %s
                  AND status IN ('PENDING', 'CLAIMED')
                """,
                (image_id, request_id, layer_id),
            )

        # ----------------------------------------------------
        # Save the file, then commit
        # ----------------------------------------------------

        os.makedirs(relative_directory, exist_ok=True)

        with open(file_on_disk, "wb") as file:
            file.write(data)

        file_written = True

        connection.commit()
        cursor.close()

        # Measure the canopy after the reply is sent, so the Pi is not kept waiting
        background_tasks.add_task(measure_image, image_id)

        return {
            "message": "Image uploaded successfully.",
            "duplicate": False,
            "image": {
                "id": image_id,
                "layer_code": code,
                "cycle_id": cycle_id,
                "captured_at": local_time.isoformat(),
                "capture_type": capture_type,
                "image_url": file_url,
                "width": width,
                "height": height,
            },
        }

    except HTTPException:
        connection.rollback()
        raise

    except Exception as e:
        connection.rollback()

        # Do not leave a file behind if the database record failed
        if file_written and file_on_disk and os.path.exists(file_on_disk):
            os.remove(file_on_disk)

        raise HTTPException(
            status_code=500,
            detail=f"Unable to upload image: {str(e)}",
        )

    finally:
        connection.close()


# ============================================================
# GET THE SIX LAYERS OF A SHELF WITH THEIR LATEST IMAGE
# ============================================================
# Called by the app (Growth Monitoring page). Uses the normal user login.

@router.get("/sites/{site_id}/shelves/{shelf_id}/layers")
def get_shelf_layers(
    site_id: int,
    shelf_id: int,
    user_id: int = Depends(get_current_user_id),
):
    connection = get_connection()

    try:
        check_site_admin_or_staff(
            connection,
            site_id,
            user_id,
        )

        cursor = connection.cursor()

        # Which photo does a card show?
        #   - layer WITH an active cycle: the latest photo that belongs to that cycle
        #     (test photos never show, so a new cycle starts with a clean card)
        #   - layer WITHOUT a cycle: the latest photo of any kind (camera preview)
        cursor.execute(
            """
            SELECT
                l.id,
                l.code,
                l.display_name,
                l.sort_order,
                img.id,
                img.file_path,
                img.captured_at,
                img.capture_type,
                img.canopy_pixels,
                img.canopy_cm2,
                img.overlay_path,
                img.canopy_status,
                c.id,
                c.transplant_date,
                c.plant_count
            FROM layers l
            JOIN shelves s
                ON s.id = l.shelf_id
            LEFT JOIN layer_cycles c
                ON c.layer_id = l.id
               AND c.status = 'ACTIVE'
            LEFT JOIN LATERAL (
                SELECT
                    i.id,
                    i.file_path,
                    i.captured_at,
                    i.capture_type,
                    m.canopy_pixels,
                    m.canopy_cm2,
                    m.overlay_path,
                    m.status AS canopy_status
                FROM layer_images i
                LEFT JOIN canopy_measurements m
                    ON m.image_id = i.id
                   AND m.method = %s
                WHERE i.layer_id = l.id
                  AND (c.id IS NULL OR i.cycle_id = c.id)
                ORDER BY i.captured_at DESC
                LIMIT 1
            ) img ON TRUE
            WHERE l.shelf_id = %s
              AND s.site_id = %s
            ORDER BY l.sort_order
            """,
            (
                DEFAULT_METHOD,
                shelf_id,
                site_id,
            ),
        )

        rows = cursor.fetchall()

        cursor.close()

        layers = []

        for row in rows:
            latest_image = None

            if row[4] is not None:
                latest_image = {
                    "id": row[4],
                    "image_url": row[5],
                    "captured_at": row[6].astimezone(MYT).isoformat(),
                    "capture_type": row[7],
                    "canopy": {
                        "method": DEFAULT_METHOD,
                        "pixels": row[8],
                        "cm2": row[9],
                        "overlay_url": row[10],
                        "status": row[11],      # 'ok', 'failed' or None (not measured yet)
                    },
                }

            active_cycle = None

            if row[12] is not None:
                active_cycle = {
                    "id": row[12],
                    "transplant_date": row[13],
                    "plant_count": row[14],
                }

            layers.append(
                {
                    "layer_id": row[0],
                    "code": row[1],
                    "display_name": row[2],
                    "sort_order": row[3],
                    "latest_image": latest_image,
                    "active_cycle": active_cycle,
                }
            )

        return layers

    finally:
        connection.close()


# ============================================================
# LAYER DETAIL: ONE CYCLE, DAY BY DAY (for the Layer Detail page)
# ============================================================
# Shows the active cycle; if there is none, the most recent harvested one.
# ?cycle_id=... shows a specific cycle of this layer.

@router.get("/sites/{site_id}/shelves/{shelf_id}/layers/{layer_id}/detail")
def get_layer_detail(
    site_id: int,
    shelf_id: int,
    layer_id: int,
    cycle_id: int | None = Query(None),
    user_id: int = Depends(get_current_user_id),
):
    connection = get_connection()

    try:
        check_site_admin_or_staff(
            connection,
            site_id,
            user_id,
        )

        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT l.id, l.code, l.display_name
            FROM layers l
            JOIN shelves s
                ON s.id = l.shelf_id
            WHERE l.id = %s
              AND l.shelf_id = %s
              AND s.site_id = %s
            """,
            (layer_id, shelf_id, site_id),
        )

        layer = cursor.fetchone()

        if not layer:
            raise HTTPException(
                status_code=404,
                detail="Layer not found.",
            )

        result = {
            "layer": {
                "id": layer[0],
                "code": layer[1],
                "display_name": layer[2],
            },
            "cycle": None,
            "summary": None,
            "days": [],
        }

        # ----------------------------------------------------
        # Which cycle to show
        # ----------------------------------------------------
        cycle_columns = """
            id,
            status,
            transplant_date,
            plant_count,
            variety,
            harvest_date,
            final_weight_g,
            harvest_canopy_cm2,
            harvest_canopy_pixels
        """

        if cycle_id is not None:
            cursor.execute(
                f"""
                SELECT {cycle_columns}
                FROM layer_cycles
                WHERE id = %s
                  AND layer_id = %s
                """,
                (cycle_id, layer_id),
            )
        else:
            cursor.execute(
                f"""
                SELECT {cycle_columns}
                FROM layer_cycles
                WHERE layer_id = %s
                  AND status IN ('ACTIVE', 'HARVESTED')
                ORDER BY (status = 'ACTIVE') DESC, transplant_date DESC, id DESC
                LIMIT 1
                """,
                (layer_id,),
            )

        cycle = cursor.fetchone()

        if not cycle:
            cursor.close()
            return result

        (
            cid,
            status,
            transplant_date,
            plant_count,
            variety,
            harvest_date,
            final_weight_g,
            harvest_cm2,
            harvest_pixels,
        ) = cycle

        today = datetime.now(MYT).date()

        if status == "HARVESTED" and harvest_date is not None:
            end_day = harvest_date
        else:
            end_day = today

        result["cycle"] = {
            "id": cid,
            "status": status,
            "transplant_date": transplant_date,
            "plant_count": plant_count,
            "variety": variety,
            "current_day": (end_day - transplant_date).days + 1,
            "harvest_date": harvest_date,
            "final_weight_g": final_weight_g,
            "harvest_canopy_cm2": harvest_cm2,
            "harvest_canopy_pixels": harvest_pixels,
        }

        # ----------------------------------------------------
        # The daily records, with each day's photo
        # ----------------------------------------------------
        cursor.execute(
            """
            SELECT
                d.plant_age_day,
                d.record_date,
                d.canopy_pixels,
                d.canopy_cm2,
                d.is_harvest_day,
                i.id,
                i.file_path,
                i.captured_at,
                i.capture_type,
                m.overlay_path
            FROM daily_cycle_records d
            LEFT JOIN layer_images i
                ON i.id = d.image_id
            LEFT JOIN canopy_measurements m
                ON m.image_id = d.image_id
               AND m.method = %s
            WHERE d.cycle_id = %s
            ORDER BY d.plant_age_day
            """,
            (DEFAULT_METHOD, cid),
        )

        rows = cursor.fetchall()
        cursor.close()

        # ----------------------------------------------------
        # Growth rate: change since the previous day that has a
        # measurement, divided by the number of days in between
        # ----------------------------------------------------
        days = []
        previous = None          # (day, pixels, cm2) of the last measured day
        growth_values = []

        for row in rows:
            day, record_date, pixels, cm2, is_harvest_day = row[:5]

            growth_cm2 = None
            growth_pixels = None
            growth_pct = None

            if pixels is not None and previous is not None:
                gap = day - previous[0]
                growth_pixels = round((pixels - previous[1]) / gap, 1)

                if cm2 is not None and previous[2] is not None:
                    growth_cm2 = round((cm2 - previous[2]) / gap, 2)
                    growth_values.append(growth_cm2)

                if previous[1]:
                    growth_pct = round((pixels - previous[1]) / previous[1] * 100, 1)

            if pixels is not None:
                previous = (day, pixels, cm2)

            image = None

            if row[5] is not None:
                image = {
                    "id": row[5],
                    "image_url": row[6],
                    "captured_at": row[7].astimezone(MYT).isoformat(),
                    "capture_type": row[8],
                    "overlay_url": row[9],
                }

            days.append(
                {
                    "day": day,
                    "date": record_date,
                    "canopy_pixels": pixels,
                    "canopy_cm2": cm2,
                    "growth_cm2_per_day": growth_cm2,
                    "growth_pixels_per_day": growth_pixels,
                    "growth_pct": growth_pct,
                    "is_harvest_day": is_harvest_day,
                    "image": image,
                }
            )

        measured = [d for d in days if d["canopy_pixels"] is not None]
        latest = measured[-1] if measured else None
        last_three = growth_values[-3:]

        result["days"] = days
        result["summary"] = {
            "latest_day": latest["day"] if latest else None,
            "latest_cm2": latest["canopy_cm2"] if latest else None,
            "latest_pixels": latest["canopy_pixels"] if latest else None,
            "growth_cm2_per_day": latest["growth_cm2_per_day"] if latest else None,
            "growth_pct": latest["growth_pct"] if latest else None,
            "growth_3day_avg_cm2": (
                round(sum(last_three) / len(last_three), 2) if last_three else None
            ),
            "photo_days": len(measured),
            "total_days": len(days),
        }

        return result

    except HTTPException:
        connection.rollback()
        raise

    finally:
        connection.close()