import copy
import pathlib

import pytest
import yaml

from alobo_bot.config import DEFAULTS, load_config, validate


def test_defaults_are_used_when_no_file(tmp_path):
    cfg = load_config(tmp_path / "missing.yaml")
    assert cfg["api"]["base_url"].startswith("https://")
    assert cfg["search"]["sport"] == "pickleball"


def test_file_overrides_and_deep_merges(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"api": {"version": "9.9.9"}, "search": {"radius_km": 3.5}}))
    cfg = load_config(path)
    assert cfg["api"]["version"] == "9.9.9"
    assert cfg["search"]["radius_km"] == 3.5
    # untouched keys keep their defaults
    assert cfg["api"]["app_name"] == DEFAULTS["api"]["app_name"]


def test_paths_resolve_against_config_dir(tmp_path):
    cfg = load_config(tmp_path / "missing.yaml")
    assert pathlib.Path(cfg["report"]["dir"]).is_absolute()


def test_validate_rejects_bad_url():
    cfg = copy.deepcopy(DEFAULTS)
    cfg["api"]["base_url"] = "ftp://nope"
    with pytest.raises(ValueError):
        validate(cfg)


def test_validate_rejects_backwards_delay():
    cfg = copy.deepcopy(DEFAULTS)
    cfg["api"]["delay_min_seconds"] = 5
    cfg["api"]["delay_max_seconds"] = 1
    with pytest.raises(ValueError):
        validate(cfg)
