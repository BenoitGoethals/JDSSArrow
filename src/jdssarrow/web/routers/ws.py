"""WebSocket live feed — streams message events to the React dashboard.

On connect, the client is subscribed to the gateway's metrics event queue; every sent/received
message is pushed as a JSON event. Backpressure is handled at the sink (bounded queue,
drop-on-full), so a slow browser never stalls the exchange.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from jdssarrow.web.routers.auth import COOKIE_NAME, auth_disabled

router = APIRouter()


@router.websocket("/ws/events")
async def events(ws: WebSocket) -> None:
    # Same session gate as the REST API — the browser sends the cookie on the WS handshake.
    if not auth_disabled():
        if not ws.app.state.user_store.verify_token(ws.cookies.get(COOKIE_NAME)):
            await ws.close(code=4401)  # 4401 = application "unauthorized"
            return
    await ws.accept()
    gateway = ws.app.state.gateway
    queue = gateway.metrics.subscribe()
    try:
        # Prime the client with the current snapshot.
        await ws.send_json({"direction": "snapshot", "snapshot": gateway.metrics.snapshot()})
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
                await ws.send_json(event)
            except TimeoutError:
                await ws.send_json({"direction": "heartbeat"})
    except WebSocketDisconnect:
        pass
    finally:
        gateway.metrics.unsubscribe(queue)
