import json
import random
import time

import paho.mqtt.client as mqtt


MQTT_BROKER = "localhost"
MQTT_PORT = 1883

# We will use this shelf for testing.
SHELF_ID = 2

MQTT_TOPIC = f"farmtab/shelves/{SHELF_ID}/sensor"

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

client.connect(MQTT_BROKER, MQTT_PORT, 60)


ph = 6.10
ec = 1200
orp = 30.0
temperature = 50.5


print("Dummy sensor started.")
print(f"Publishing to: {MQTT_TOPIC}")
print("Publishing every 2 seconds...")
print("Press Ctrl+C to stop.\n")


try:
    while True:
        # Make the values change slightly,
        # simulating a real sensor.
        ph += random.uniform(-0.03, 0.03)
        ec += random.uniform(-20, 20)
        orp += random.uniform(-3, 3)
        temperature += random.uniform(-0.1, 0.1)

        data = {
            "ph": round(ph, 2),
            "ec": round(ec, 2),
            "orp": round(orp, 2),
            "temperature": round(temperature, 2),
        }

        payload = json.dumps(data)

        result = client.publish(MQTT_TOPIC, payload)

        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            print(f"Published: {payload}")
        else:
            print(f"Publish failed: {result.rc}")

        time.sleep(2)

except KeyboardInterrupt:
    print("\nDummy sensor stopped.")

finally:
    client.disconnect()
