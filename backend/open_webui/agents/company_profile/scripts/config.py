"""Local secrets loader for the temporary offline batch runner."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_DEFAULT_PATH = Path(__file__).resolve().parent / "config" / "secrets.yaml"


def load_secrets(path: str | Path = _DEFAULT_PATH) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        return {"qwen": {}}
    with config_path.open(encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def get_qwen_config(secrets: dict[str, Any] | None = None) -> dict[str, str]:
    if secrets is None:
        secrets = load_secrets()
    qwen = secrets.get("qwen", {}) or {}
    return {
        "api_key": qwen.get("api_key", ""),
        "base_url": qwen.get("base_url", ""),
        "model": qwen.get("model", ""),
    }
