"""FastAPI application: web config + live monitoring for a running gateway.

The app owns one :class:`JdssGateway` for its lifetime (started/stopped via the lifespan). It
exposes:

* REST config endpoints (view/update the plugin selection and network params).
* Introspection of the AEP-76 volumes and the available plugins per extension point.
* A monitoring snapshot + Prometheus ``/metrics``.
* A WebSocket that streams live message events to the React dashboard.
* An Arrow IPC dump of recent telemetry for downstream analytics.

The gateway is created from a config file named by the ``JDSS_CONFIG`` env var (falling back
to defaults), so ``uvicorn jdssarrow.web.app:app`` just works.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from jdssarrow.config.loader import FileConfigStore, load_config
from jdssarrow.web.routers import register_routers
from jdssarrow.web.runtime import build_and_start


def create_app(config_path: str | None = None) -> FastAPI:
    from jdssarrow.audit import setup_logging

    setup_logging()
    path = config_path or os.environ.get("JDSS_CONFIG")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config = load_config(path)
        # A config store lets PUT /api/config persist runtime edits back to the file.
        app.state.config_store = FileConfigStore(path) if path else None
        gateway, node = await build_and_start(config)
        app.state.gateway = gateway
        app.state.node = node
        from jdssarrow.simulator.manager import SimulationManager
        from jdssarrow.web.eud_server import EudServerManager
        from jdssarrow.web.servers import ServerConnectionManager

        app.state.sim_manager = SimulationManager()
        # live CoT/TAK server connections (CRUD-managed from the Configuration tab)
        server_manager = ServerConnectionManager()
        server_manager.attach(node, gateway)
        await server_manager.reconcile(config.servers)
        app.state.server_manager = server_manager
        # built-in TAK server so ATAK EUDs connect directly to this node
        eud_server = EudServerManager()
        eud_server.attach(node, gateway)
        await eud_server.reconfigure(config.eud_server)
        app.state.eud_server = eud_server
        try:
            yield
        finally:
            await app.state.eud_server.stop()
            await app.state.server_manager.stop()
            await app.state.sim_manager.stop()
            await app.state.node.stop()

    app = FastAPI(title="JDSSArrow — JDSS Web Config & Monitor", version="0.1.0", lifespan=lifespan)

    from fastapi.responses import JSONResponse

    from jdssarrow.auth import UserStore
    from jdssarrow.capabilities import CapabilityError
    from jdssarrow.web.routers.auth import COOKIE_NAME, auth_disabled

    # File-backed login for the dashboard (default user warrior/warrior1401, seeded on first run).
    app.state.user_store = UserStore()

    @app.exception_handler(CapabilityError)
    async def _capability_denied(_request, exc: CapabilityError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.middleware("http")
    async def _require_login(request, call_next):
        # Gate every /api/* route behind a valid session cookie, except the auth endpoints
        # themselves. Static assets (the SPA + docs) and /metrics stay open so the login page
        # can load and scrapers keep working; the WebSocket is gated in its own handler.
        path = request.url.path
        if path.startswith("/api/") and not path.startswith("/api/auth/") and not auth_disabled():
            token = request.cookies.get(COOKIE_NAME)
            if not request.app.state.user_store.verify_token(token):
                return JSONResponse(status_code=401, content={"detail": "authentication required"})
        return await call_next(request)

    register_routers(app)
    _mount_web_ui(app)
    return app


def _mount_web_ui(app: FastAPI) -> None:
    """Serve the built React dashboard (``web-ui/dist``) at ``/`` when present.

    Only mounts when the directory exists, so dev/test imports of the app (which run without a
    built frontend) are unaffected. The API routers are registered *before* this call, so
    ``/api``, ``/metrics`` and ``/ws`` keep precedence over this SPA catch-all mount.

    The directory is taken from ``JDSS_WEB_UI_DIR`` (set in the Docker image) and otherwise
    falls back to the repo-relative ``web-ui/dist`` for local runs after ``npm run build``.
    """
    from pathlib import Path

    from fastapi.staticfiles import StaticFiles

    configured = os.environ.get("JDSS_WEB_UI_DIR")
    default = Path(__file__).resolve().parents[3] / "web-ui" / "dist"
    dist = Path(configured) if configured else default
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="web-ui")


#: module-level ASGI app for `uvicorn jdssarrow.web.app:app`.
app = create_app()
