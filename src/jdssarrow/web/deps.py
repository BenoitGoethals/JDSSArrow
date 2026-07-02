"""Shared dependencies for the web routers."""

from __future__ import annotations

from fastapi import Request

from jdssarrow.gateway.gateway import JdssGateway


def get_gateway(request: Request) -> JdssGateway:
    return request.app.state.gateway
