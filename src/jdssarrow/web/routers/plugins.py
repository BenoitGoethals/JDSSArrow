"""Plugin introspection — what implementations are available per extension point.

Lets the web config UI render a dropdown of installed plugins for each AEP-76 seam, proving
the system is pluggable at runtime rather than at build time.
"""

from __future__ import annotations

from fastapi import APIRouter

from jdssarrow.plugins.registry import GROUPS, registry

router = APIRouter(tags=["plugins"])


@router.get("/api/plugins")
def list_plugins() -> dict[str, list[str]]:
    """Available plugin names for every extension-point group."""
    result: dict[str, list[str]] = {}
    for group in GROUPS:
        try:
            result[group] = registry.names(group)
        except Exception:
            result[group] = []
    return result
