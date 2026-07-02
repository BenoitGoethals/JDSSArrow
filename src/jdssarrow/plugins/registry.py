"""Runtime discovery and lookup of pluggable implementations.

Each extension point is an entry-point *group* (``jdssarrow.codecs``,
``jdssarrow.transports`` …). Defaults ship with this package; third parties add their own
by declaring entry points in the same group. The registry is the seam the gateway uses to
turn a config string like ``codec: "xml"`` into a concrete object — no ``import`` of the
implementation appears in the core.

Entry points are the discovery mechanism, but callers may also register objects directly
(handy for tests and for in-process custom impls).
"""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any

GROUPS = {
    "codecs": "jdssarrow.codecs",
    "transports": "jdssarrow.transports",
    "security": "jdssarrow.security",
    "bearers": "jdssarrow.bearers",
    "allocators": "jdssarrow.allocators",
    "profiles": "jdssarrow.profiles",
    "policies": "jdssarrow.policies",
}


class PluginError(LookupError):
    """Raised when a requested plugin cannot be found in a group."""


class Registry:
    """Lazily discovers and caches extension-point implementations by group + name."""

    def __init__(self) -> None:
        # group -> name -> loaded class/factory
        self._cache: dict[str, dict[str, Any]] = {}
        # group -> name -> object, for manual overrides (win over entry points)
        self._overrides: dict[str, dict[str, Any]] = {}

    def register(self, group: str, name: str, impl: Any) -> None:
        """Manually register an implementation (used by tests and in-process plugins)."""
        self._overrides.setdefault(group, {})[name] = impl

    def _discover(self, group: str) -> dict[str, Any]:
        if group not in self._cache:
            if group not in GROUPS:
                raise PluginError(f"unknown plugin group: {group!r}")
            loaded: dict[str, Any] = {}
            for ep in entry_points(group=GROUPS[group]):
                loaded[ep.name] = ep.load()
            self._cache[group] = loaded
        return self._cache[group]

    def names(self, group: str) -> list[str]:
        """All plugin names available in a group (overrides + entry points)."""
        found = set(self._discover(group)) | set(self._overrides.get(group, {}))
        return sorted(found)

    def get(self, group: str, name: str) -> Any:
        """Return the class/factory registered under ``name`` in ``group``."""
        override = self._overrides.get(group, {}).get(name)
        if override is not None:
            return override
        try:
            return self._discover(group)[name]
        except KeyError:
            available = ", ".join(self.names(group)) or "<none>"
            raise PluginError(
                f"no {group} plugin named {name!r}; available: {available}"
            ) from None

    def create(self, group: str, name: str, /, *args: Any, **kwargs: Any) -> Any:
        """Instantiate the plugin under ``name`` with the given arguments."""
        return self.get(group, name)(*args, **kwargs)


#: process-wide default registry; the gateway uses this unless given another.
registry = Registry()
