"""Config file loading + the file-backed ConfigStore.

Supports YAML and TOML config files. ``load_config`` merges a file (if given) under the
env-var / default layers already handled by :class:`GatewayConfig`. ``FileConfigStore``
implements the ``ConfigStore`` protocol so the web UI can persist runtime edits back to disk.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import yaml

from jdssarrow.config.models import GatewayConfig


def _read_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix in {".toml"}:
        return tomllib.loads(text)
    return yaml.safe_load(text) or {}


def load_config(path: str | Path | None = None) -> GatewayConfig:
    """Build a :class:`GatewayConfig`, overlaying a file when provided."""
    if path is None:
        return GatewayConfig()
    data = _read_file(Path(path))
    return GatewayConfig(**data)


class FileConfigStore:
    name = "file"

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def load(self) -> dict:
        if not self._path.exists():
            return {}
        return _read_file(self._path)

    def save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.suffix == ".toml":  # pragma: no cover - yaml is the default
            import tomli_w  # type: ignore

            self._path.write_text(tomli_w.dumps(data), encoding="utf-8")
        else:
            self._path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
