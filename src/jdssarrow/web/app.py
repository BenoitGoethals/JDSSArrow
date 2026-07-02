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

        app.state.sim_manager = SimulationManager()
        try:
            yield
        finally:
            await app.state.sim_manager.stop()
            await app.state.node.stop()

    app = FastAPI(title="JDSSArrow — JDSS Web Config & Monitor", version="0.1.0", lifespan=lifespan)

    from fastapi.responses import JSONResponse

    from jdssarrow.capabilities import CapabilityError

    @app.exception_handler(CapabilityError)
    async def _capability_denied(_request, exc: CapabilityError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    register_routers(app)
    return app


#: module-level ASGI app for `uvicorn jdssarrow.web.app:app`.
app = create_app()
