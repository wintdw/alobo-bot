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
        "base_url": "https://user-app-new-ootprnz4oa-uc.a.run.app",
        "global_url": "https://user-global-ootprnz4oa-uc.a.run.app",
        "app_name": "alobo-user",
        "platform": "web",
        "version": "2.10.3",
        "lang": "vi",
        "app_key": "935b1fccd4bc45a12af095bf0bafa723",
        "timeout_seconds": 30,
        "delay_min_seconds": 0.3,
        "delay_max_seconds": 0.8,
        "max_retries": 3,
    },
    "search": {
        "sport": "pickleball",
        "default_place": "Hà Nội",
        "radius_km": 3.0,
        # Saved places: name -> {"lat": ..., "lng": ...}, searchable wherever a
        # place is (`find --place/--preset`, the web page's Area field).
        "presets": {},
        "max_branches": 25,
        "workers": 6,
        "page_size": 100,
        # Which categories a run reports: all | court (per court) | social ("xé vé", per person).
        "category": "all",
        # Court availability: any (keep partly-free courts, quoted for the open
        # part) | free (only courts free for the whole window).
        "availability": "any",
        # Tariff ("đối tượng áp dụng") to price courts under: a target id or name
        # from get_core_types. Blank = each type's generic customer tariff.
        "target": None,
        "areas": [
            "Hà Nội",
            "Ba Đình, Hà Nội",
            "Hoàn Kiếm, Hà Nội",
            "Tây Hồ, Hà Nội",
            "Long Biên, Hà Nội",
            "Cầu Giấy, Hà Nội",
            "Đống Đa, Hà Nội",
            "Hai Bà Trưng, Hà Nội",
            "Hoàng Mai, Hà Nội",
            "Thanh Xuân, Hà Nội",
            "Nam Từ Liêm, Hà Nội",
            "Bắc Từ Liêm, Hà Nội",
            "Hà Đông, Hà Nội",
            "Hoài Đức, Hà Nội",
            "Gia Lâm, Hà Nội",
            "Thanh Trì, Hà Nội",
            "Đông Anh, Hà Nội",
        ],
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
    presets = cfg["search"].get("presets") or {}
    if not isinstance(presets, dict):
        raise ValueError("config: search.presets must be a mapping of name -> {lat, lng}")
    for name, value in presets.items():
        if not isinstance(value, dict):
            raise ValueError(f"config: preset {name!r} must be a mapping with lat and lng")
        for axis in ("lat", "lng"):
            try:
                float(value[axis])
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"config: preset {name!r} needs a numeric {axis}") from None
