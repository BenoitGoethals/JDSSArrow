"""Web API routers, grouped by concern."""

from __future__ import annotations

from fastapi import FastAPI

from jdssarrow.web.routers import (
    auth,
    config,
    connect,
    connections,
    eud,
    logs,
    monitor,
    plugins,
    servers,
    simcontrol,
    ws,
)


def register_routers(app: FastAPI) -> None:
    app.include_router(auth.router)
    app.include_router(config.router)
    app.include_router(plugins.router)
    app.include_router(monitor.router)
    app.include_router(connections.router)
    app.include_router(connect.router)
    app.include_router(simcontrol.router)
    app.include_router(servers.router)
    app.include_router(eud.router)
    app.include_router(logs.router)
    app.include_router(ws.router)
