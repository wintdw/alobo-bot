import datetime as dt
import json
from types import SimpleNamespace

import pytest

from alobo_bot.metrics import Metrics, Snapshot, status_class
from alobo_bot.web import request_client_ip


def snapshot(metrics: Metrics) -> Snapshot:
    return metrics.snapshot()


# --- requests ---------------------------------------------------------------

def test_requests_are_counted_by_path_status_client_and_latency():
    metrics = Metrics()
    for path, status, ip, duration in (
        ("/", 200, "a", 10.0),
        ("/", 500, "a", 30.0),
        ("/status", 200, "b", 5.0),
    ):
        metrics.begin_request()
        metrics.end_request(path=path, status=status, client_ip=ip, duration_ms=duration)

    snap = snapshot(metrics)
    assert snap.requests == 3
    assert snap.in_flight == 0
    assert snap.by_path == {"/": 2, "/status": 1}
    assert snap.by_status == {200: 2, 500: 1}
    assert snap.errors == 1
    assert snap.error_rate == pytest.approx(1 / 3)
    assert snap.latency_ms_avg == pytest.approx(15.0)
    assert snap.latency_ms_max == 30.0


def test_in_flight_counts_requests_that_have_not_answered_yet():
    metrics = Metrics()
    metrics.begin_request()
    metrics.begin_request()
    assert snapshot(metrics).in_flight == 2

    metrics.end_request(path="/", status=200, client_ip="a", duration_ms=1)
    assert snapshot(metrics).in_flight == 1


def test_an_empty_snapshot_has_no_rates_or_latency():
    snap = snapshot(Metrics())
    assert snap.requests == 0
    assert snap.error_rate == 0.0
    assert snap.cache_hit_rate == 0.0
    assert snap.latency_ms_avg is None
    assert snap.latency_ms_max is None


# --- clients ----------------------------------------------------------------

def test_clients_are_remembered_most_recent_first_and_bounded():
    metrics = Metrics(clients_capacity=2)
    for ip in ("a", "b", "c"):
        metrics.end_request(path="/", status=200, client_ip=ip, duration_ms=1)

    snap = snapshot(metrics)
    assert snap.clients_seen == 3                       # every distinct address seen
    assert [client.ip for client in snap.clients] == ["c", "b"]  # newest first, capped


def test_each_client_accumulates_its_own_request_count():
    metrics = Metrics()
    metrics.end_request(path="/", status=200, client_ip="a", duration_ms=1)
    metrics.end_request(path="/results", status=404, client_ip="a", duration_ms=1)
    metrics.end_request(path="/", status=200, client_ip="b", duration_ms=1)

    clients = {client.ip: client for client in snapshot(metrics).clients}
    assert clients["a"].requests == 2
    assert clients["a"].last_path == "/results" and clients["a"].last_status == 404
    assert clients["b"].requests == 1


def test_a_snapshot_is_a_copy_not_a_live_view():
    metrics = Metrics()
    metrics.end_request(path="/", status=200, client_ip="a", duration_ms=1)
    snap = snapshot(metrics)

    metrics.end_request(path="/x", status=200, client_ip="a", duration_ms=1)
    assert snap.requests == 1 and snap.by_path == {"/": 1}


# --- searches ---------------------------------------------------------------

def test_the_search_slot_is_single_flight_and_counted():
    metrics = Metrics()
    assert metrics.begin_search() is True
    assert metrics.begin_search() is False               # a second caller is turned away
    assert snapshot(metrics).search_busy == 1

    moment = dt.datetime(2026, 9, 23, 18, 30)
    metrics.finish_search(now=moment)
    snap = snapshot(metrics)
    assert snap.busy is False
    assert snap.searches == 1
    assert snap.last_run == moment


def test_a_failed_search_is_counted_and_remembered_then_cleared_by_a_success():
    metrics = Metrics()
    metrics.begin_search()
    metrics.fail_search("ApiError: boom")

    snap = snapshot(metrics)
    assert snap.busy is False
    assert snap.search_failures == 1 and snap.searches == 0
    assert snap.last_error == "ApiError: boom"

    metrics.begin_search()
    metrics.finish_search()
    snap = snapshot(metrics)
    assert snap.last_error is None                       # the next run clears it
    assert snap.search_failures == 1                     # but the count stands


def test_cache_hit_rate_counts_lookups():
    metrics = Metrics()
    metrics.record_cache_hit()
    metrics.record_cache_hit()
    metrics.record_cache_miss()

    snap = snapshot(metrics)
    assert (snap.cache_hits, snap.cache_misses) == (2, 1)
    assert snap.cache_hit_rate == pytest.approx(2 / 3)


# --- snapshot view ----------------------------------------------------------

def test_status_class_groups_codes():
    assert status_class(200) == "2xx"
    assert status_class(404) == "4xx"
    assert status_class(503) == "5xx"
    assert status_class(99) == "---"
    assert status_class(700) == "---"


def test_snapshot_to_dict_is_json_ready():
    metrics = Metrics()
    metrics.end_request(path="/", status=200, client_ip="a", duration_ms=1)
    data = metrics.snapshot().to_dict()

    assert data["requests"] == 1
    assert data["byStatus"] == {"200": 1}
    assert data["clients"][0]["ip"] == "a"
    json.dumps(data)  # must not raise


# --- persistence ------------------------------------------------------------

def test_counters_survive_a_restart(tmp_path):
    path = tmp_path / "metrics.json"
    first = Metrics(path=path, save_interval_seconds=0)
    first.begin_request()
    first.end_request(path="/", status=200, client_ip="1.2.3.4", duration_ms=12.0)
    first.begin_request()
    first.end_request(path="/status", status=500, client_ip="1.2.3.4", duration_ms=8.0)
    first.begin_search()
    first.finish_search(now=dt.datetime(2026, 9, 23, 18, 30))
    first.record_cache_hit()

    assert path.exists()
    snap = Metrics(path=path).snapshot()

    assert snap.requests == 2
    assert snap.errors == 1
    assert snap.by_path == {"/": 1, "/status": 1}
    assert snap.by_status == {200: 1, 500: 1}
    assert snap.latency_ms_max == 12.0
    assert snap.clients_seen == 1
    assert snap.clients[0].ip == "1.2.3.4"
    assert snap.clients[0].requests == 2
    assert snap.searches == 1
    assert snap.last_run == dt.datetime(2026, 9, 23, 18, 30)
    assert snap.cache_hits == 1
    assert snap.busy is False                            # a restart is never mid-search


def test_process_uptime_is_not_restored_but_the_totals_are(tmp_path):
    path = tmp_path / "metrics.json"
    Metrics(path=path, save_interval_seconds=0).end_request(
        path="/", status=200, client_ip="a", duration_ms=1
    )

    restarted = Metrics(path=path)
    snap = restarted.snapshot()
    assert snap.requests == 1                            # the total carries over
    assert snap.uptime_seconds < 60                      # but the clock starts again


def test_a_corrupt_or_foreign_state_file_starts_from_zero(tmp_path):
    path = tmp_path / "metrics.json"
    path.write_text("{not json")
    assert Metrics(path=path).snapshot().requests == 0

    path.write_text(json.dumps(["not", "a", "dict"]))
    assert Metrics(path=path).snapshot().requests == 0


def test_saving_without_a_path_is_a_no_op():
    metrics = Metrics()
    metrics.end_request(path="/", status=200, client_ip="a", duration_ms=1)
    assert metrics.save() is True


# --- client address ---------------------------------------------------------

class FakeRequest:
    def __init__(self, headers=None, host=None):
        self.headers = headers or {}
        self.client = SimpleNamespace(host=host) if host else None


def test_client_ip_prefers_the_first_forwarded_hop():
    request = FakeRequest(headers={"x-forwarded-for": "203.0.113.9, 10.0.0.1"}, host="10.0.0.1")
    assert request_client_ip(request) == "203.0.113.9"


def test_client_ip_falls_back_to_the_socket_peer():
    assert request_client_ip(FakeRequest(host="127.0.0.1")) == "127.0.0.1"
    assert request_client_ip(FakeRequest(headers={"x-forwarded-for": " , "}, host="1.2.3.4")) == "1.2.3.4"


def test_client_ip_unknown_without_either():
    assert request_client_ip(FakeRequest()) == "unknown"
