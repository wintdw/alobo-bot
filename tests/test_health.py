"""The /health checks: storage, durable state, disk and the API origins.

Network-free: the upstream probe is exercised against a fake client, and the
storage checks run on tmp directories, so these test the verdict logic only.
"""

import datetime as dt
import os

import pytest

from alobo_bot.api import ApiError
from alobo_bot.config import DEFAULTS
from alobo_bot.health import (
    DEFAULT_LATITUDE,
    DEFAULT_LONGITUDE,
    DEGRADED,
    DOWN,
    OK,
    UpstreamProbe,
    build_report,
    check_disk,
    check_state,
    check_storage,
    check_upstream,
    health_api_config,
    probe_coordinates,
    writable_dir,
)


class FakeClient:
    """The two calls the probe makes, with a call count and optional failures."""

    def __init__(self, *, sports=None, branches=None, sport_error=None, branch_error=None):
        self._sports = [object()] if sports is None else sports
        self._branches = [object()] * 3 if branches is None else branches
        self.sport_error = sport_error
        self.branch_error = branch_error
        self.calls = {"sport_types": 0, "branches_near": 0}

    def sport_types(self):
        self.calls["sport_types"] += 1
        if self.sport_error is not None:
            raise self.sport_error
        return list(self._sports)

    def branches_near(self, latitude, longitude):
        self.calls["branches_near"] += 1
        if self.branch_error is not None:
            raise self.branch_error
        return list(self._branches)


# --- storage ----------------------------------------------------------------

def test_a_writable_dir_passes_and_a_missing_one_is_judged_by_its_parent(tmp_path):
    assert writable_dir(tmp_path) == (True, None)
    assert writable_dir(tmp_path / "not-created-yet") == (True, None)


def test_a_read_only_dir_fails_with_the_reason(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root bypasses file permissions")
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)

    writable, error = writable_dir(locked)
    assert writable is False
    assert "not writable" in error


def test_storage_is_down_when_any_dir_is_unwritable(tmp_path):
    good = tmp_path / "reports"
    assert check_storage({"reports": good})["status"] == OK

    a_file = tmp_path / "not-a-dir"
    a_file.write_text("x", encoding="utf-8")
    report = check_storage({"reports": good, "raw": a_file})
    assert report["status"] == DOWN
    assert [check["ok"] for check in report["dirs"]] == [True, False]


def test_disk_is_degraded_only_below_the_threshold(tmp_path):
    assert check_disk(tmp_path, min_free_bytes=1)["status"] == OK

    low = check_disk(tmp_path, min_free_bytes=10**18)
    assert low["status"] == DEGRADED
    assert low["freeBytes"] >= 0


# --- durable state ----------------------------------------------------------

def test_missing_state_files_are_normal_but_corrupt_ones_are_not(tmp_path):
    assert check_state(tmp_path)["status"] == OK

    (tmp_path / "metrics.json").write_text("{not json", encoding="utf-8")
    report = check_state(tmp_path)
    assert report["status"] == DEGRADED
    states = {check["name"]: check["state"] for check in report["files"]}
    assert states == {"cache.json": "absent", "metrics.json": "corrupt"}


def test_a_readable_state_file_passes(tmp_path):
    (tmp_path / "cache.json").write_text('{"entries": []}', encoding="utf-8")
    assert check_state(tmp_path)["status"] == OK


# --- upstream ---------------------------------------------------------------

def test_upstream_is_ok_when_both_origins_answer():
    report = check_upstream(FakeClient(), 21.0, 105.0)
    assert report["status"] == OK
    assert {check["name"]: check["status"] for check in report["checks"]} == {
        "sport-types": OK,
        "branches-near": OK,
    }


def test_an_unreachable_branch_endpoint_is_down_because_searches_need_it():
    report = check_upstream(FakeClient(branch_error=ApiError("boom")), 21.0, 105.0)
    assert report["status"] == DOWN
    branches = next(c for c in report["checks"] if c["name"] == "branches-near")
    assert "boom" in branches["error"]


def test_a_broken_sport_list_is_only_degraded():
    # The dropdown falls back to the configured sport; searches still work.
    report = check_upstream(FakeClient(sport_error=ApiError("nope")), 21.0, 105.0)
    assert report["status"] == DEGRADED


def test_an_empty_answer_is_degraded_rather_than_ok():
    report = check_upstream(FakeClient(branches=[]), 21.0, 105.0)
    assert report["status"] == DEGRADED


def test_probe_results_are_reused_inside_the_ttl():
    client = FakeClient()
    probe = UpstreamProbe(client, latitude=1.0, longitude=2.0, ttl_seconds=10)

    first = probe.check(now=100.0)
    assert first["cached"] is False
    assert client.calls == {"sport_types": 1, "branches_near": 1}

    reused = probe.check(now=109.0)
    assert reused["cached"] is True
    assert client.calls == {"sport_types": 1, "branches_near": 1}

    probe.check(now=111.0)
    assert client.calls == {"sport_types": 2, "branches_near": 2}


# --- report -----------------------------------------------------------------

def _checks(status=OK):
    return {
        "upstream": {"status": status},
        "storage": {"status": OK},
        "disk": {"status": OK},
        "state": {"status": OK},
    }


def test_the_worst_check_sets_the_verdict_and_status_code():
    now = dt.datetime(2026, 11, 25, 12, 0, 0)
    healthy = build_report(**_checks(), service={}, now=now)
    assert healthy["status"] == OK
    assert healthy["ok"] is True
    assert healthy["httpStatus"] == 200
    assert healthy["checkedAt"] == "2026-11-25T12:00:00"

    degraded = build_report(**_checks(DEGRADED), service={}, now=now)
    assert degraded["status"] == DEGRADED
    assert degraded["httpStatus"] == 200

    down = build_report(**_checks(DOWN), service={}, now=now)
    assert down["status"] == DOWN
    assert down["ok"] is False
    assert down["httpStatus"] == 503


# --- configuration ----------------------------------------------------------

def test_probe_coordinates_prefer_the_first_saved_place():
    cfg = {"search": {"presets": {"Somewhere": {"lat": "1.5", "lng": "2.5"}}}}
    assert probe_coordinates(cfg) == (1.5, 2.5)


def test_probe_coordinates_fall_back_to_hanoi():
    assert probe_coordinates({"search": {"presets": {}}}) == (
        DEFAULT_LATITUDE,
        DEFAULT_LONGITUDE,
    )


def test_health_client_is_one_fast_attempt_and_leaves_the_config_alone():
    cfg = {"api": dict(DEFAULTS["api"])}
    tuned = health_api_config(cfg, 4)

    assert tuned["api"]["timeout_seconds"] == 4
    assert tuned["api"]["max_retries"] == 1
    assert tuned["api"]["delay_max_seconds"] == 0
    assert cfg["api"]["max_retries"] == DEFAULTS["api"]["max_retries"]
