"""WebSocket live feed — streams message events to the React dashboard.

On connect, the client is subscribed to the gateway's metrics event queue; every sent/received
message is pushed as a JSON event. Backpressure is handled at the sink (bounded queue,
drop-on-full), so a slow browser never stalls the exchange.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws/events")
async def events(ws: WebSocket) -> None:
    # Open like the rest of the REST API (the login is a dashboard-UI gate, not an API gate).
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
