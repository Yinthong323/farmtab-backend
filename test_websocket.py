import websocket


url = "ws://98.88.222.75:8000/ws/sites/5/shelves/2/sensor"

print("Connecting to WebSocket...")
print(f"URL: {url}")

ws = websocket.create_connection(url)

print("WebSocket connected successfully!")
print("Waiting for sensor data...")
print("Press Ctrl+C to stop.\n")

try:
    while True:
        message = ws.recv()
        print(f"Received: {message}")

except KeyboardInterrupt:
    print("\nStopping WebSocket test...")

finally:
    ws.close()