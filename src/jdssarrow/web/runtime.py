"""Shared web runtime helpers: build/start a node and hot-reconfigure it.

Both the app lifespan and the ``PUT /api/config`` endpoint need to construct a gateway,
attach the collector handler, start it and announce identity. Centralising that here keeps
the two paths identical and avoids an import cycle between ``app`` and the routers.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from fastapi import FastAPI

from jdssarrow.config.models import GatewayConfig
from jdssarrow.datamodel.messages import JdssMessage
from jdssarrow.gateway.gateway import JdssGateway
from jdssarrow.gateway.node import SoldierNode


class CollectHandler:
    """A no-op subscriber so the exchange always has at least one handler.

    Reception metrics are recorded by the engine; this is a hook point and keeps dispatch
    exercised even when no application handler is attached.
    """

    subscribes_to: Iterable[str] = ("*",)

    async def handle(self, message: JdssMessage) -> None:
        return None


async def build_and_start(config: GatewayConfig) -> tuple[JdssGateway, SoldierNode]:
    """Create a gateway+node from ``config``, start it and announce identity."""
    gateway = JdssGateway(config)
    node = SoldierNode(gateway)
    node.add_handler(CollectHandler())
    await node.start()
    await node.identify()
    return gateway, node


def persist_config_section(app: FastAPI, section: str, data: dict[str, Any]) -> bool:
    """Merge one section into the on-disk config file, if the app has a config store.

    Used to persist live edits (e.g. the capability matrix) so they survive a restart.
    Returns False when the app was started without a config file (nothing to persist to).
    """
    store = getattr(app.state, "config_store", None)
    if store is None:
        return False
    current = store.load()
    current[section] = data
    store.save(current)
    return True


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``patch`` into a copy of ``base`` (patch wins)."""
    out = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


async def reconfigure(app: FastAPI, patch: dict[str, Any]) -> GatewayConfig:
    """Apply a partial config change: stop the old node, start a new one, persist.

    Returns the new effective :class:`GatewayConfig`. Raises ``ValidationError`` if the
    merged config is invalid (the caller maps that to HTTP 422) — the running node is only
    torn down *after* the new config validates, so a bad patch never leaves the node down.
    """
    current = app.state.gateway.config.model_dump(mode="json")
    merged = deep_merge(current, patch)
    new_config = GatewayConfig(**merged)  # validates before we touch the running node

    await app.state.node.stop()
    gateway, node = await build_and_start(new_config)
    app.state.gateway = gateway
    app.state.node = node

    store = getattr(app.state, "config_store", None)
    if store is not None:
        store.save(new_config.model_dump(mode="json"))
    return new_config
