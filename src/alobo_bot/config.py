"""Config loading: built-in DEFAULTS deep-merged with the project config.yaml."""

from __future__ import annotations

import copy
import os
import pathlib
from typing import Any

import yaml

# Project root = <src/alobo_bot>/.. = parent of the src layout. Override with
# ALOBO_BOT_HOME when running from a non-editable install.
ROOT = pathlib.Path(
    os.environ.get("ALOBO_BOT_HOME") or pathlib.Path(__file__).resolve().parents[2]
)
CONFIG_PATH = ROOT / "config.yaml"

DEFAULTS: dict[str, Any] = {
    "api": {
        "base_url": "https://user-app-new-vk7r7j5t3q-uc.a.run.app",
        "global_url": "https://user-app-vk7r7j5t3q-uc.a.run.app",
        "app_name": "alobo-user",
        "platform": "web",
        "version": "2.10.3",
        "lang": "vi",
        "app_key": "Alobo-User-Key-2026",
        "timeout_seconds": 30,
        "delay_min_seconds": 0.3,
        "delay_max_seconds": 0.8,
        "max_retries": 3,
    },
    "search": {
        "sport": "pickleball",
        "default_place": "Hà Nội",
        "radius_km": 10.0,
        "max_branches": 25,
        "workers": 6,
        "page_size": 100,
    },
    "report": {"dir": "data/reports"},
    "raw": {"dir": "data/raw"},
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into a copy of base."""
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _resolve_paths(cfg: dict, root: pathlib.Path) -> dict:
    """Turn root-relative path values (report.dir, raw.dir) into absolute paths."""
    for section in ("report", "raw"):
        value = cfg.get(section, {}).get("dir")
        if isinstance(value, str):
            cfg[section]["dir"] = str((root / value).resolve())
    return cfg


def load_config(path: str | os.PathLike[str] | None = None) -> dict:
    """Load config.yaml from the project root (or an explicit path) over DEFAULTS."""
    source = pathlib.Path(path) if path else CONFIG_PATH
    cfg = _deep_merge(DEFAULTS, {})
    if source.exists():
        with open(source, encoding="utf-8") as fh:
            cfg = _deep_merge(cfg, yaml.safe_load(fh) or {})
    return _resolve_paths(cfg, source.parent if path else ROOT)


def validate(cfg: dict) -> None:
    """Raise ValueError on obviously unusable configuration."""
    api = cfg["api"]
    for key in ("base_url", "global_url"):
        if not str(api.get(key, "")).startswith("http"):
            raise ValueError(f"config: api.{key} must be an http(s) URL, got {api.get(key)!r}")
    if api["delay_min_seconds"] > api["delay_max_seconds"]:
        raise ValueError("config: api.delay_min_seconds must be <= delay_max_seconds")
    if int(cfg["search"]["max_branches"]) < 1:
        raise ValueError("config: search.max_branches must be >= 1")
    if float(cfg["search"]["radius_km"]) <= 0:
        raise ValueError("config: search.radius_km must be > 0")
