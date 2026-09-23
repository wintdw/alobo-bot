import json

from alobo_bot.state import read_json, write_json


def test_write_then_read_round_trips(tmp_path):
    path = tmp_path / "nested" / "state.json"
    assert write_json(path, {"a": [1, 2], "b": "x", "c": None}) is True
    assert read_json(path) == {"a": [1, 2], "b": "x", "c": None}


def test_reading_a_missing_or_unreadable_file_is_empty(tmp_path):
    assert read_json(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert read_json(bad) is None


def test_write_replaces_in_place_and_leaves_no_temp_files(tmp_path):
    path = tmp_path / "state.json"
    write_json(path, {"v": 1})
    write_json(path, {"v": 2})

    assert read_json(path) == {"v": 2}
    assert [entry.name for entry in tmp_path.iterdir()] == ["state.json"]
    assert json.loads(path.read_text()) == {"v": 2}  # what landed is the new value, whole
