# import json
# import logging
# from datetime import datetime, timezone

# import paho.mqtt.client as mqtt

# from database.connection import get_connection
# from realtime.connection_manager import manager


# MQTT_BROKER = "localhost"
# MQTT_PORT = 1883
# MQTT_TOPIC = "farmtab/shelves/+/sensor"

# # Sensor must stay normal for this long before an alert is resolved.
# RECOVERY_PERIOD_SECONDS = 5 * 60


# logger = logging.getLogger(__name__)
# logging.basicConfig(level=logging.INFO)


# # ------------------------------------------------------------
# # Helper: get current UTC time
# # ------------------------------------------------------------

# def utc_now():
#     return datetime.now(timezone.utc)

# def check_and_handle_alert(
#     cursor,
#     shelf_id: int,
#     sensor_type: str,
#     value: float,
#     minimum: float,
#     maximum: float,
# ):
#     # ---------------------------------------------------------
#     # 1. Determine whether the current reading is abnormal
#     # ---------------------------------------------------------
#     if value < minimum:
#         current_alert_type = "LOW"
#         threshold_value = minimum
#     elif value > maximum:
#         current_alert_type = "HIGH"
#         threshold_value = maximum
#     else:
#         current_alert_type = None
#         threshold_value = None

#     # ---------------------------------------------------------
#     # 2. Find the current active/stabilizing alert
#     # ---------------------------------------------------------
#     cursor.execute(
#         """
#         SELECT id, alert_type, status, recovery_started_at
#         FROM alerts
#         WHERE shelf_id = %s
#           AND sensor_type = %s
#           AND status IN ('ACTIVE', 'STABILIZING')
#         LIMIT 1
#         """,
#         (shelf_id, sensor_type),
#     )

#     current_alert = cursor.fetchone()

#     # =========================================================
#     # CASE A:
#     # Current sensor reading is ABNORMAL
#     # =========================================================
#     if current_alert_type is not None:

#         # -----------------------------------------------------
#         # No existing ACTIVE/STABILIZING alert
#         # → Create a new ACTIVE alert
#         # -----------------------------------------------------
#         if not current_alert:
#             cursor.execute(
#                 """
#                 INSERT INTO alerts (
#                     shelf_id,
#                     sensor_type,
#                     alert_type,
#                     value,
#                     threshold_value,
#                     status,
#                     is_read,
#                     recovery_started_at
#                 )
#                 VALUES (
#                     %s, %s, %s, %s, %s,
#                     'ACTIVE',
#                     FALSE,
#                     NULL
#                 )
#                 RETURNING id
#                 """,
#                 (
#                     shelf_id,
#                     sensor_type,
#                     current_alert_type,
#                     value,
#                     threshold_value,
#                 ),
#             )

#             alert_id = cursor.fetchone()[0]

#             logger.warning(
#                 f"Created alert | "
#                 f"id={alert_id} "
#                 f"shelf={shelf_id} "
#                 f"sensor={sensor_type} "
#                 f"type={current_alert_type} "
#                 f"value={value} "
#                 f"threshold={threshold_value}"
#             )

#             return {
#                 "alert_id": alert_id,
#                 "shelf_id": shelf_id,
#                 "sensor_type": sensor_type,
#                 "alert_type": current_alert_type,
#                 "value": value,
#                 "threshold_value": threshold_value,
#             }

#         # -----------------------------------------------------
#         # Existing alert found
#         # -----------------------------------------------------
#         alert_id = current_alert[0]
#         existing_alert_type = current_alert[1]
#         existing_status = current_alert[2]
#         recovery_started_at = current_alert[3]

#         # -----------------------------------------------------
#         # Sensor became abnormal again while STABILIZING
#         # → Cancel recovery
#         # → Return to ACTIVE
#         # -----------------------------------------------------
#         if existing_status == "STABILIZING":
#             cursor.execute(
#                 """
#                 UPDATE alerts
#                 SET status = 'ACTIVE',
#                     alert_type = %s,
#                     value = %s,
#                     threshold_value = %s,
#                     recovery_started_at = NULL,
#                     is_read = FALSE
#                 WHERE id = %s
#                 """,
#                 (
#                     current_alert_type,
#                     value,
#                     threshold_value,
#                     alert_id,
#                 ),
#             )

#             logger.warning(
#                 f"Alert returned to ACTIVE | "
#                 f"id={alert_id} "
#                 f"shelf={shelf_id} "
#                 f"sensor={sensor_type} "
#                 f"type={current_alert_type} "
#                 f"value={value}"
#             )

#             return

#         # -----------------------------------------------------
#         # Existing ACTIVE alert but direction changed
#         # LOW → HIGH or HIGH → LOW
#         # -----------------------------------------------------
#         if existing_alert_type != current_alert_type:
#             cursor.execute(
#                 """
#                 UPDATE alerts
#                 SET alert_type = %s,
#                     value = %s,
#                     threshold_value = %s,
#                     recovery_started_at = NULL,
#                     is_read = FALSE
#                 WHERE id = %s
#                 """,
#                 (
#                     current_alert_type,
#                     value,
#                     threshold_value,
#                     alert_id,
#                 ),
#             )

#             logger.warning(
#                 f"Alert direction changed | "
#                 f"id={alert_id} "
#                 f"shelf={shelf_id} "
#                 f"sensor={sensor_type} "
#                 f"type={current_alert_type} "
#                 f"value={value}"
#             )

#             return

#         # -----------------------------------------------------
#         # Existing ACTIVE alert with same direction
#         # → Keep ACTIVE
#         # → Update latest value
#         # -----------------------------------------------------
#         cursor.execute(
#             """
#             UPDATE alerts
#             SET value = %s,
#                 threshold_value = %s
#             WHERE id = %s
#             """,
#             (
#                 value,
#                 threshold_value,
#                 alert_id,
#             ),
#         )

#         return

#     # =========================================================
#     # CASE B:
#     # Current sensor reading is NORMAL
#     # =========================================================

#     # No ACTIVE/STABILIZING alert
#     # → Nothing to do
#     if not current_alert:
#         return

#     alert_id = current_alert[0]
#     recovery_started_at = current_alert[3]
#     existing_status = current_alert[2]

#     # ---------------------------------------------------------
#     # Existing ACTIVE alert just became normal
#     # → Start STABILIZING period
#     # ---------------------------------------------------------
#     if existing_status == "ACTIVE" and recovery_started_at is None:
#         cursor.execute(
#             """
#             UPDATE alerts
#             SET status = 'STABILIZING',
#                 recovery_started_at = CURRENT_TIMESTAMP,
#                 value = %s
#             WHERE id = %s
#             """,
#             (
#                 value,
#                 alert_id,
#             ),
#         )

#         logger.info(
#             f"Alert entered STABILIZING | "
#             f"id={alert_id} "
#             f"shelf={shelf_id} "
#             f"sensor={sensor_type} "
#             f"value={value}"
#         )

#         return

#     # ---------------------------------------------------------
#     # Safety check:
#     # STABILIZING should always have a recovery timestamp
#     # ---------------------------------------------------------
#     if recovery_started_at is None:
#         cursor.execute(
#             """
#             UPDATE alerts
#             SET recovery_started_at = CURRENT_TIMESTAMP,
#                 status = 'STABILIZING',
#                 value = %s
#             WHERE id = %s
#             """,
#             (
#                 value,
#                 alert_id,
#             ),
#         )

#         return

#     # ---------------------------------------------------------
#     # Calculate actual recovery time
#     # ---------------------------------------------------------
#     now = utc_now()

#     elapsed_seconds = (
#         now - recovery_started_at
#     ).total_seconds()

#     # ---------------------------------------------------------
#     # Still within the 5-minute stabilizing period
#     # ---------------------------------------------------------
#     if elapsed_seconds < RECOVERY_PERIOD_SECONDS:
#         cursor.execute(
#             """
#             UPDATE alerts
#             SET value = %s
#             WHERE id = %s
#             """,
#             (
#                 value,
#                 alert_id,
#             ),
#         )

#         logger.info(
#             f"Alert stabilizing | "
#             f"id={alert_id} "
#             f"shelf={shelf_id} "
#             f"sensor={sensor_type} "
#             f"elapsed={int(elapsed_seconds)}s"
#         )

#         return

#     # ---------------------------------------------------------
#     # Stable for the full recovery period
#     # → RESOLVED
#     # ---------------------------------------------------------
#     cursor.execute(
#         """
#         UPDATE alerts
#         SET status = 'RESOLVED',
#             resolved_at = CURRENT_TIMESTAMP,
#             recovery_started_at = NULL,
#             value = %s
#         WHERE id = %s
#           AND status = 'STABILIZING'
#         """,
#         (
#             value,
#             alert_id,
#         ),
#     )

#     logger.info(
#         f"Alert resolved | "
#         f"id={alert_id} "
#         f"shelf={shelf_id} "
#         f"sensor={sensor_type} "
#         f"after={int(elapsed_seconds)}s"
#     )


# # ------------------------------------------------------------
# # MQTT connection
# # ------------------------------------------------------------

# def on_connect(client, userdata, flags, reason_code, properties):

#     if reason_code == 0:

#         logger.info(
#             "Connected to MQTT broker."
#         )

#         client.subscribe(
#             MQTT_TOPIC
#         )

#         logger.info(
#             f"Subscribed to: {MQTT_TOPIC}"
#         )

#     else:

#         logger.error(
#             f"MQTT connection failed: {reason_code}"
#         )


# # ------------------------------------------------------------
# # MQTT message
# # ------------------------------------------------------------

# def on_message(client, userdata, msg):

#     try:

#         # ----------------------------------------------------
#         # Parse topic
#         # ----------------------------------------------------

#         topic_parts = msg.topic.split("/")

#         if len(topic_parts) != 4:

#             logger.warning(
#                 f"Ignoring unexpected topic: {msg.topic}"
#             )

#             return

#         shelf_id = int(
#             topic_parts[2]
#         )

#         # ----------------------------------------------------
#         # Parse sensor data
#         # ----------------------------------------------------

#         data = json.loads(
#             msg.payload.decode("utf-8")
#         )

#         ph = float(
#             data["ph"]
#         )

#         ec = float(
#             data["ec"]
#         )

#         orp = float(
#             data["orp"]
#         )

#         temperature = float(
#             data["temperature"]
#         )

#         # ----------------------------------------------------
#         # Connect to PostgreSQL
#         # ----------------------------------------------------

#         connection = get_connection()

#         try:

#             cursor = connection.cursor()

#             # ------------------------------------------------
#             # Get CURRENT shelf thresholds
#             # ------------------------------------------------

#             cursor.execute(
#                 """
#                 SELECT
#                     ph_min,
#                     ph_max,
#                     ec_min,
#                     ec_max,
#                     temperature_min,
#                     temperature_max,
#                     orp_min,
#                     orp_max
#                 FROM shelves
#                 WHERE id = %s
#                 """,
#                 (
#                     shelf_id,
#                 ),
#             )

#             thresholds = cursor.fetchone()

#             if not thresholds:

#                 logger.warning(
#                     f"Shelf not found | shelf={shelf_id}"
#                 )

#                 connection.rollback()

#                 return

#             (
#                 ph_min,
#                 ph_max,
#                 ec_min,
#                 ec_max,
#                 temperature_min,
#                 temperature_max,
#                 orp_min,
#                 orp_max,
#             ) = thresholds

#             # ------------------------------------------------
#             # Check alerts
#             # ------------------------------------------------

#             check_and_handle_alert(
#                 cursor=cursor,
#                 shelf_id=shelf_id,
#                 sensor_type="PH",
#                 value=ph,
#                 minimum=float(ph_min),
#                 maximum=float(ph_max),
#             )

#             check_and_handle_alert(
#                 cursor=cursor,
#                 shelf_id=shelf_id,
#                 sensor_type="EC",
#                 value=ec,
#                 minimum=float(ec_min),
#                 maximum=float(ec_max),
#             )

#             check_and_handle_alert(
#                 cursor=cursor,
#                 shelf_id=shelf_id,
#                 sensor_type="TEMPERATURE",
#                 value=temperature,
#                 minimum=float(temperature_min),
#                 maximum=float(temperature_max),
#             )

#             check_and_handle_alert(
#                 cursor=cursor,
#                 shelf_id=shelf_id,
#                 sensor_type="ORP",
#                 value=orp,
#                 minimum=float(orp_min),
#                 maximum=float(orp_max),
#             )

#             # ------------------------------------------------
#             # Save sensor reading
#             # ------------------------------------------------

#             cursor.execute(
#                 """
#                 INSERT INTO sensor_readings
#                 (
#                     shelf_id,
#                     ph,
#                     ec,
#                     orp,
#                     temperature
#                 )
#                 VALUES
#                 (
#                     %s,
#                     %s,
#                     %s,
#                     %s,
#                     %s
#                 )
#                 RETURNING recorded_at
#                 """,
#                 (
#                     shelf_id,
#                     ph,
#                     ec,
#                     orp,
#                     temperature,
#                 ),
#             )

#             recorded_at = cursor.fetchone()[0]

#             # ------------------------------------------------
#             # Commit alert + sensor reading together
#             # ------------------------------------------------

#             connection.commit()

#             logger.info(
#                 f"Saved sensor reading | "
#                 f"shelf={shelf_id} "
#                 f"pH={ph} "
#                 f"EC={ec} "
#                 f"ORP={orp} "
#                 f"temperature={temperature}"
#             )

#             # ------------------------------------------------
#             # Broadcast to Flutter WebSocket
#             # ------------------------------------------------

#             manager.broadcast_from_mqtt(
#                 shelf_id,
#                 {
#                     "shelf_id": shelf_id,
#                     "recorded_at": recorded_at.isoformat(),
#                     "ph": ph,
#                     "ec": ec,
#                     "orp": orp,
#                     "temperature": temperature,
#                 },
#             )

#         finally:

#             cursor.close()
#             connection.close()

#     except Exception as error:

#         logger.error(
#             f"Error processing MQTT message: {error}"
#         )


# # ------------------------------------------------------------
# # Start MQTT client
# # ------------------------------------------------------------

# def start_mqtt_client():

#     client = mqtt.Client(
#         mqtt.CallbackAPIVersion.VERSION2
#     )

#     client.on_connect = on_connect
#     client.on_message = on_message

#     client.connect(
#         MQTT_BROKER,
#         MQTT_PORT,
#         60,
#     )

#     client.loop_start()

#     return client


import json
import logging
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from database.connection import get_connection
from realtime.connection_manager import manager


MQTT_BROKER = "localhost"
MQTT_PORT = 1883
MQTT_TOPIC = "farmtab/shelves/+/sensor"


# Sensor must stay normal for this long before an alert is resolved.
RECOVERY_PERIOD_SECONDS = 5 * 60


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# ------------------------------------------------------------
# Helper: get current UTC time
# ------------------------------------------------------------

def utc_now():
    return datetime.now(timezone.utc)


# ------------------------------------------------------------
# Alert handling
# ------------------------------------------------------------

def check_and_handle_alert(
    cursor,
    shelf_id: int,
    sensor_type: str,
    value: float,
    minimum: float,
    maximum: float,
):
    # ---------------------------------------------------------
    # 1. Determine whether the current reading is abnormal
    # ---------------------------------------------------------

    if value < minimum:
        current_alert_type = "LOW"
        threshold_value = minimum

    elif value > maximum:
        current_alert_type = "HIGH"
        threshold_value = maximum

    else:
        current_alert_type = None
        threshold_value = None

    # ---------------------------------------------------------
    # 2. Find the current active/stabilizing alert
    # ---------------------------------------------------------

    cursor.execute(
        """
        SELECT id, alert_type, status, recovery_started_at
        FROM alerts
        WHERE shelf_id = %s
          AND sensor_type = %s
          AND status IN ('ACTIVE', 'STABILIZING')
        LIMIT 1
        """,
        (shelf_id, sensor_type),
    )

    current_alert = cursor.fetchone()

    # =========================================================
    # CASE A:
    # Current sensor reading is ABNORMAL
    # =========================================================

    if current_alert_type is not None:

        # -----------------------------------------------------
        # No existing ACTIVE/STABILIZING alert
        # → Create a new ACTIVE alert
        # -----------------------------------------------------

        if not current_alert:
            cursor.execute(
                """
                INSERT INTO alerts (
                    shelf_id,
                    sensor_type,
                    alert_type,
                    value,
                    threshold_value,
                    status,
                    is_read,
                    recovery_started_at
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    'ACTIVE',
                    FALSE,
                    NULL
                )
                RETURNING id
                """,
                (
                    shelf_id,
                    sensor_type,
                    current_alert_type,
                    value,
                    threshold_value,
                ),
            )

            alert_id = cursor.fetchone()[0]

            logger.warning(
                f"Created alert | "
                f"id={alert_id} "
                f"shelf={shelf_id} "
                f"sensor={sensor_type} "
                f"type={current_alert_type} "
                f"value={value} "
                f"threshold={threshold_value}"
            )

            # Return information about the NEW alert.
            # The caller will broadcast it only after commit.
            return {
                "alert_id": alert_id,
                "shelf_id": shelf_id,
                "sensor_type": sensor_type,
                "alert_type": current_alert_type,
                "value": value,
                "threshold_value": threshold_value,
            }

        # -----------------------------------------------------
        # Existing alert found
        # -----------------------------------------------------

        alert_id = current_alert[0]
        existing_alert_type = current_alert[1]
        existing_status = current_alert[2]
        recovery_started_at = current_alert[3]

        # -----------------------------------------------------
        # Sensor became abnormal again while STABILIZING
        # → Cancel recovery
        # → Return to ACTIVE
        # -----------------------------------------------------

        if existing_status == "STABILIZING":
            cursor.execute(
                """
                UPDATE alerts
                SET status = 'ACTIVE',
                    alert_type = %s,
                    value = %s,
                    threshold_value = %s,
                    recovery_started_at = NULL,
                    is_read = FALSE
                WHERE id = %s
                """,
                (
                    current_alert_type,
                    value,
                    threshold_value,
                    alert_id,
                ),
            )

            logger.warning(
                f"Alert returned to ACTIVE | "
                f"id={alert_id} "
                f"shelf={shelf_id} "
                f"sensor={sensor_type} "
                f"type={current_alert_type} "
                f"value={value}"
            )

            return

        # -----------------------------------------------------
        # Existing ACTIVE alert but direction changed
        # LOW → HIGH or HIGH → LOW
        # -----------------------------------------------------

        if existing_alert_type != current_alert_type:
            cursor.execute(
                """
                UPDATE alerts
                SET alert_type = %s,
                    value = %s,
                    threshold_value = %s,
                    recovery_started_at = NULL,
                    is_read = FALSE
                WHERE id = %s
                """,
                (
                    current_alert_type,
                    value,
                    threshold_value,
                    alert_id,
                ),
            )

            logger.warning(
                f"Alert direction changed | "
                f"id={alert_id} "
                f"shelf={shelf_id} "
                f"sensor={sensor_type} "
                f"type={current_alert_type} "
                f"value={value}"
            )

            return

        # -----------------------------------------------------
        # Existing ACTIVE alert with same direction
        # → Keep ACTIVE
        # → Update latest value
        # -----------------------------------------------------

        cursor.execute(
            """
            UPDATE alerts
            SET value = %s,
                threshold_value = %s
            WHERE id = %s
            """,
            (
                value,
                threshold_value,
                alert_id,
            ),
        )

        return

    # =========================================================
    # CASE B:
    # Current sensor reading is NORMAL
    # =========================================================

    # No ACTIVE/STABILIZING alert
    # → Nothing to do

    if not current_alert:
        return

    alert_id = current_alert[0]
    recovery_started_at = current_alert[3]
    existing_status = current_alert[2]

    # ---------------------------------------------------------
    # Existing ACTIVE alert just became normal
    # → Start STABILIZING period
    # ---------------------------------------------------------

    if existing_status == "ACTIVE" and recovery_started_at is None:
        cursor.execute(
            """
            UPDATE alerts
            SET status = 'STABILIZING',
                recovery_started_at = CURRENT_TIMESTAMP,
                value = %s
            WHERE id = %s
            """,
            (
                value,
                alert_id,
            ),
        )

        logger.info(
            f"Alert entered STABILIZING | "
            f"id={alert_id} "
            f"shelf={shelf_id} "
            f"sensor={sensor_type} "
            f"value={value}"
        )

        return

    # ---------------------------------------------------------
    # Safety check:
    # STABILIZING should always have a recovery timestamp
    # ---------------------------------------------------------

    if recovery_started_at is None:
        cursor.execute(
            """
            UPDATE alerts
            SET recovery_started_at = CURRENT_TIMESTAMP,
                status = 'STABILIZING',
                value = %s
            WHERE id = %s
            """,
            (
                value,
                alert_id,
            ),
        )

        return

    # ---------------------------------------------------------
    # Calculate actual recovery time
    # ---------------------------------------------------------

    now = utc_now()

    elapsed_seconds = (
        now - recovery_started_at
    ).total_seconds()

    # ---------------------------------------------------------
    # Still within the 5-minute stabilizing period
    # ---------------------------------------------------------

    if elapsed_seconds < RECOVERY_PERIOD_SECONDS:
        cursor.execute(
            """
            UPDATE alerts
            SET value = %s
            WHERE id = %s
            """,
            (
                value,
                alert_id,
            ),
        )

        logger.info(
            f"Alert stabilizing | "
            f"id={alert_id} "
            f"shelf={shelf_id} "
            f"sensor={sensor_type} "
            f"elapsed={int(elapsed_seconds)}s"
        )

        return

    # ---------------------------------------------------------
    # Stable for the full recovery period
    # → RESOLVED
    # ---------------------------------------------------------

    cursor.execute(
        """
        UPDATE alerts
        SET status = 'RESOLVED',
            resolved_at = CURRENT_TIMESTAMP,
            recovery_started_at = NULL,
            value = %s
        WHERE id = %s
          AND status = 'STABILIZING'
        """,
        (
            value,
            alert_id,
        ),
    )

    logger.info(
        f"Alert resolved | "
        f"id={alert_id} "
        f"shelf={shelf_id} "
        f"sensor={sensor_type} "
        f"after={int(elapsed_seconds)}s"
    )


# ------------------------------------------------------------
# MQTT connection
# ------------------------------------------------------------

def on_connect(client, userdata, flags, reason_code, properties):

    if reason_code == 0:

        logger.info(
            "Connected to MQTT broker."
        )

        client.subscribe(
            MQTT_TOPIC
        )

        logger.info(
            f"Subscribed to: {MQTT_TOPIC}"
        )

    else:

        logger.error(
            f"MQTT connection failed: {reason_code}"
        )


# ------------------------------------------------------------
# MQTT message
# ------------------------------------------------------------

def on_message(client, userdata, msg):

    try:

        # ----------------------------------------------------
        # Parse topic
        # ----------------------------------------------------

        topic_parts = msg.topic.split("/")

        if len(topic_parts) != 4:

            logger.warning(
                f"Ignoring unexpected topic: {msg.topic}"
            )

            return

        shelf_id = int(
            topic_parts[2]
        )

        # ----------------------------------------------------
        # Parse sensor data
        # ----------------------------------------------------

        data = json.loads(
            msg.payload.decode("utf-8")
        )

        ph = float(
            data["ph"]
        )

        ec = float(
            data["ec"]
        )

        orp = float(
            data["orp"]
        )

        temperature = float(
            data["temperature"]
        )

        # ----------------------------------------------------
        # Connect to PostgreSQL
        # ----------------------------------------------------

        connection = get_connection()

        try:

            cursor = connection.cursor()

            # ------------------------------------------------
            # Get CURRENT shelf thresholds
            # ------------------------------------------------

            cursor.execute(
                """
                SELECT
                    ph_min,
                    ph_max,
                    ec_min,
                    ec_max,
                    temperature_min,
                    temperature_max,
                    orp_min,
                    orp_max
                FROM shelves
                WHERE id = %s
                """,
                (
                    shelf_id,
                ),
            )

            thresholds = cursor.fetchone()

            if not thresholds:

                logger.warning(
                    f"Shelf not found | shelf={shelf_id}"
                )

                connection.rollback()

                return

            (
                ph_min,
                ph_max,
                ec_min,
                ec_max,
                temperature_min,
                temperature_max,
                orp_min,
                orp_max,
            ) = thresholds

            # ------------------------------------------------
            # Check alerts
            # ------------------------------------------------

            # Store information about a newly created alert.
            # It will be broadcast only after the transaction
            # has been committed.
            new_alert = None

            result = check_and_handle_alert(
                cursor=cursor,
                shelf_id=shelf_id,
                sensor_type="PH",
                value=ph,
                minimum=float(ph_min),
                maximum=float(ph_max),
            )

            if result:
                new_alert = result

            result = check_and_handle_alert(
                cursor=cursor,
                shelf_id=shelf_id,
                sensor_type="EC",
                value=ec,
                minimum=float(ec_min),
                maximum=float(ec_max),
            )

            if result:
                new_alert = result

            result = check_and_handle_alert(
                cursor=cursor,
                shelf_id=shelf_id,
                sensor_type="TEMPERATURE",
                value=temperature,
                minimum=float(temperature_min),
                maximum=float(temperature_max),
            )

            if result:
                new_alert = result

            result = check_and_handle_alert(
                cursor=cursor,
                shelf_id=shelf_id,
                sensor_type="ORP",
                value=orp,
                minimum=float(orp_min),
                maximum=float(orp_max),
            )

            if result:
                new_alert = result

            # ------------------------------------------------
            # Save sensor reading
            # ------------------------------------------------

            cursor.execute(
                """
                INSERT INTO sensor_readings
                (
                    shelf_id,
                    ph,
                    ec,
                    orp,
                    temperature
                )
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                RETURNING recorded_at
                """,
                (
                    shelf_id,
                    ph,
                    ec,
                    orp,
                    temperature,
                ),
            )

            recorded_at = cursor.fetchone()[0]

            # ------------------------------------------------
            # Commit alert + sensor reading together
            # ------------------------------------------------

            connection.commit()

            # ------------------------------------------------
            # Broadcast NEW ALERT after successful commit
            # ------------------------------------------------

            if new_alert:

                cursor.execute(
                    """
                    SELECT
                        s.name,
                        s.site_id,
                        si.name
                    FROM shelves s
                    JOIN sites si ON si.id = s.site_id
                    WHERE s.id = %s
                    """,
                    (shelf_id,),
                )

                shelf_info = cursor.fetchone()

                if shelf_info:

                    shelf_name, site_id, site_name = shelf_info

                    manager.broadcast_alert_from_mqtt(
                        {
                            "type": "NEW_ALERT",
                            "alert_id": new_alert["alert_id"],
                            "site_id": site_id,
                            "shelf_id": shelf_id,
                            "site_name": site_name,
                            "shelf_name": shelf_name,
                            "sensor_type": new_alert["sensor_type"],
                            "alert_type": new_alert["alert_type"],
                            "value": new_alert["value"],
                            "threshold_value": new_alert["threshold_value"],
                        }
                    )

                    logger.info(
                        f"Broadcast NEW_ALERT | "
                        f"id={new_alert['alert_id']} "
                        f"shelf={shelf_id} "
                        f"sensor={new_alert['sensor_type']}"
                    )

            # ------------------------------------------------
            # Log sensor reading
            # ------------------------------------------------

            logger.info(
                f"Saved sensor reading | "
                f"shelf={shelf_id} "
                f"pH={ph} "
                f"EC={ec} "
                f"ORP={orp} "
                f"temperature={temperature}"
            )

            # ------------------------------------------------
            # Broadcast sensor reading to Shelf WebSocket
            # ------------------------------------------------

            manager.broadcast_from_mqtt(
                shelf_id,
                {
                    "shelf_id": shelf_id,
                    "recorded_at": recorded_at.isoformat(),
                    "ph": ph,
                    "ec": ec,
                    "orp": orp,
                    "temperature": temperature,
                },
            )

        finally:

            cursor.close()
            connection.close()

    except Exception as error:

        logger.error(
            f"Error processing MQTT message: {error}"
        )


# ------------------------------------------------------------
# Start MQTT client
# ------------------------------------------------------------

def start_mqtt_client():

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2
    )

    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(
        MQTT_BROKER,
        MQTT_PORT,
        60,
    )

    client.loop_start()

    return client