# import asyncio
# from typing import Dict, Set

# from fastapi import WebSocket


# class ConnectionManager:
#     def __init__(self):
#         self.active_connections: Dict[int, Set[WebSocket]] = {}

#         # FastAPI's asyncio event loop.
#         self.loop = None

#     def set_loop(self, loop):
#         self.loop = loop

#     async def connect(self, shelf_id: int, websocket: WebSocket):
#         await websocket.accept()

#         if shelf_id not in self.active_connections:
#             self.active_connections[shelf_id] = set()

#         self.active_connections[shelf_id].add(websocket)

#     def disconnect(self, shelf_id: int, websocket: WebSocket):
#         if shelf_id not in self.active_connections:
#             return

#         self.active_connections[shelf_id].discard(websocket)

#         if not self.active_connections[shelf_id]:
#             del self.active_connections[shelf_id]

#     async def broadcast(self, shelf_id: int, message: dict):
#         if shelf_id not in self.active_connections:
#             return

#         disconnected = set()

#         for websocket in self.active_connections[shelf_id]:
#             try:
#                 await websocket.send_json(message)
#             except Exception:
#                 disconnected.add(websocket)

#         for websocket in disconnected:
#             self.disconnect(shelf_id, websocket)

#     def broadcast_from_mqtt(self, shelf_id: int, message: dict):
#         """
#         MQTT callbacks run in a background thread.

#         This safely sends the message to FastAPI's
#         asyncio event loop.
#         """
#         if self.loop is None:
#             return

#         asyncio.run_coroutine_threadsafe(
#             self.broadcast(shelf_id, message),
#             self.loop,
#         )


# manager = ConnectionManager()

import asyncio
from typing import Dict, Set

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        # Shelf-specific sensor WebSocket connections
        self.active_connections: Dict[int, Set[WebSocket]] = {}

        # App-wide alert WebSocket connections
        self.alert_connections: Set[WebSocket] = set()

        # FastAPI's asyncio event loop
        self.loop = None

    def set_loop(self, loop):
        self.loop = loop

    # ============================================================
    # SHELF SENSOR WEBSOCKET
    # ============================================================

    async def connect(self, shelf_id: int, websocket: WebSocket):
        await websocket.accept()

        if shelf_id not in self.active_connections:
            self.active_connections[shelf_id] = set()

        self.active_connections[shelf_id].add(websocket)

    def disconnect(self, shelf_id: int, websocket: WebSocket):
        if shelf_id not in self.active_connections:
            return

        self.active_connections[shelf_id].discard(websocket)

        if not self.active_connections[shelf_id]:
            del self.active_connections[shelf_id]

    async def broadcast(self, shelf_id: int, message: dict):
        if shelf_id not in self.active_connections:
            return

        disconnected = set()

        for websocket in self.active_connections[shelf_id]:
            try:
                await websocket.send_json(message)
            except Exception:
                disconnected.add(websocket)

        for websocket in disconnected:
            self.disconnect(shelf_id, websocket)

    def broadcast_from_mqtt(self, shelf_id: int, message: dict):
        """
        MQTT callbacks run in a background thread.

        This safely sends the message to FastAPI's
        asyncio event loop.
        """
        if self.loop is None:
            return

        asyncio.run_coroutine_threadsafe(
            self.broadcast(shelf_id, message),
            self.loop,
        )

    # ============================================================
    # GLOBAL ALERT WEBSOCKET
    # ============================================================

    async def connect_alert(self, websocket: WebSocket):
        await websocket.accept()
        self.alert_connections.add(websocket)

    def disconnect_alert(self, websocket: WebSocket):
        self.alert_connections.discard(websocket)

    async def broadcast_alert(self, message: dict):
        if not self.alert_connections:
            return

        disconnected = set()

        for websocket in self.alert_connections:
            try:
                await websocket.send_json(message)
            except Exception:
                disconnected.add(websocket)

        for websocket in disconnected:
            self.disconnect_alert(websocket)

    def broadcast_alert_from_mqtt(self, message: dict):
        """
        MQTT callbacks run in a background thread.

        Safely broadcast a new alert to all connected
        Flutter applications.
        """
        if self.loop is None:
            return

        asyncio.run_coroutine_threadsafe(
            self.broadcast_alert(message),
            self.loop,
        )


manager = ConnectionManager()