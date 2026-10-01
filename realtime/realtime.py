from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from realtime.connection_manager import manager


router = APIRouter(tags=["Realtime"])


@router.websocket("/ws/sites/{site_id}/shelves/{shelf_id}/sensor")
async def sensor_websocket(
    websocket: WebSocket,
    site_id: int,
    shelf_id: int,
):
    await manager.connect(shelf_id, websocket)

    try:
        while True:
            # Keep the WebSocket connection alive.
            # We are not receiving data from Flutter yet.
            await websocket.receive_text()

    except WebSocketDisconnect:
        manager.disconnect(shelf_id, websocket)
