import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI

from auth.signup import router as signup_router
from auth.verification import router as verification_router
from auth.resend import router as resend_router
from organisation.create import router as organisation_router
from auth.login import router as login_router
from auth.forgot_password import router as forgot_password_router
from organisation.shared import router as shared_organisation_router
from organisation.join import router as organisation_join_router
from sites.sites import router as site_router
from fastapi.staticfiles import StaticFiles
from shelves.shelves import router as shelves_router
from sensor_readings.sensor_readings import router as sensor_readings_router
from mqtt.mqtt_service import start_mqtt_client
from fastapi import WebSocket, WebSocketDisconnect
from realtime.connection_manager import manager
from growing_cycles.growing_cycles import router as growing_cycles_router
from devices import devices
from notifications.notifications import router as notifications_router
from users.users import router as users_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()

    manager.set_loop(loop)

    mqtt_client = start_mqtt_client()

    yield

    mqtt_client.loop_stop()
    mqtt_client.disconnect()


app = FastAPI(lifespan=lifespan)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


@app.get("/")
def home():
    return {
        "message": "Farm application backend is running!"
    }

@app.websocket("/ws/sites/{site_id}/shelves/{shelf_id}/sensor")
async def sensor_websocket(
    websocket: WebSocket,
    site_id: int,
    shelf_id: int,
):

    print(f"WebSocket connection attempt: site={site_id}, shelf={shelf_id}")
    await manager.connect(shelf_id, websocket)

    try:
        while True:
            await websocket.receive_text()

    except WebSocketDisconnect:
        manager.disconnect(shelf_id, websocket)


@app.websocket("/ws/alerts")
async def alerts_websocket(
    websocket: WebSocket,
):
    print("Global alert WebSocket connection attempt")

    await manager.connect_alert(websocket)

    try:
        while True:
            await websocket.receive_text()

    except WebSocketDisconnect:
        manager.disconnect_alert(websocket)

app.include_router(signup_router)
app.include_router(verification_router)
app.include_router(resend_router)
app.include_router(login_router)
app.include_router(forgot_password_router)

app.include_router(organisation_router)
app.include_router(shared_organisation_router)
app.include_router(organisation_join_router)
app.include_router(site_router)
app.include_router(shelves_router)
app.include_router(sensor_readings_router)
app.include_router(growing_cycles_router)
app.include_router(devices.router)
app.include_router(notifications_router)
app.include_router(users_router)