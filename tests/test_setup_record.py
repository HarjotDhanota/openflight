"""The setup record: schema, atomic save, and staleness against the start context."""

from __future__ import annotations

import json

import pytest

from openflight.setup_record import (
    SCHEMA,
    SECTIONS,
    StartContext,
    evaluate,
    load,
    save,
    section_inputs,
)


def _ctx(**over) -> StartContext:
    base = dict(lens_id="stock-2.8", rig_sha256="rig-a", tilt_offset_deg=2.0, tilt_calibrated=True)
    base.update(over)
    return StartContext(**base)


def _confirmed_record() -> dict:
    return {
        "schema": SCHEMA,
        "revision": 1,
        "box": {
            "state": "confirmed",
            "id": "b1",
            "inputs": section_inputs("box", lens_id="stock-2.8", rig_sha256="rig-a", tilt_deg=2.0),
        },
        "aim": {
            "state": "confirmed",
            "floor_line": 0.62,
            "inputs": section_inputs("aim", box_id="b1"),
        },
        "ball_range": {
            "state": "confirmed",
            "range_m": 1.9,
            "inputs": section_inputs("ball_range", box_id="b1"),
        },
        "net_range": {
            "state": "confirmed",
            "inputs": section_inputs(
                "net_range", floor_line=0.62, lens_id="stock-2.8", tilt_deg=2.0
            ),
        },
        "scene": {
            "state": "confirmed",
            "inputs": section_inputs("scene", ball_range_m=1.9),
        },
    }


def test_missing_file_gives_all_sections_not_set(tmp_path):
    record = load(tmp_path / "nope.json")
    for name in SECTIONS:
        assert record[name]["state"] == "not_set"
        assert "no setup file" in record[name]["reason"]


def test_corrupt_and_future_schema_are_not_applied(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    future = tmp_path / "future.json"
    future.write_text(
        json.dumps({**_confirmed_record(), "schema": "openflight.setup.v9"}), encoding="utf-8"
    )

    for path, reason in ((bad, "unreadable"), (future, "unknown schema")):
        record = load(path)
        for name in SECTIONS:
            assert record[name]["state"] == "not_set"
            assert reason in record[name]["reason"]


def test_malformed_section_rejects_the_whole_file(tmp_path):
    path = tmp_path / "setup.json"
    path.write_text(json.dumps({**_confirmed_record(), "aim": "oops"}), encoding="utf-8")
    record = load(path)
    assert all(record[name]["state"] == "not_set" for name in SECTIONS)


def test_save_is_atomic_and_bumps_revision(tmp_path):
    path = tmp_path / "setup.json"
    record = load(path)
    save(path, record)
    save(path, record)
    assert load(path)["revision"] == 2
    assert not list(tmp_path.glob("*.tmp"))


def test_save_round_trips_sections(tmp_path):
    path = tmp_path / "setup.json"
    save(path, _confirmed_record())
    assert load(path)["box"]["id"] == "b1"


def test_confirmed_record_is_unchanged_when_inputs_match():
    record = _confirmed_record()
    out = evaluate(record, _ctx(), tilt_margin_deg=1.0)
    assert [out[n]["state"] for n in SECTIONS] == ["confirmed"] * 5


def test_lens_change_stales_box_and_its_dependents():
    record = _confirmed_record()
    out = evaluate(record, _ctx(lens_id="6mm"), tilt_margin_deg=1.0)
    for name in SECTIONS:
        assert out[name]["state"] == "stale"
        assert "lens" in out[name]["reason"]
    assert record["box"]["state"] == "confirmed"  # evaluate returns a copy


def test_rig_change_stales_box():
    out = evaluate(_confirmed_record(), _ctx(rig_sha256="rig-b"), tilt_margin_deg=1.0)
    assert out["box"]["state"] == "stale"
    assert "rig" in out["box"]["reason"]


def test_tilt_within_margin_keeps_box():
    out = evaluate(_confirmed_record(), _ctx(tilt_offset_deg=2.8), tilt_margin_deg=1.0)
    assert out["box"]["state"] == "confirmed"
    assert out["net_range"]["state"] == "confirmed"


def test_tilt_beyond_margin_stales_box():
    out = evaluate(_confirmed_record(), _ctx(tilt_offset_deg=3.2), tilt_margin_deg=1.0)
    assert out["box"]["state"] == "stale"
    assert "tilt" in out["box"]["reason"]


def test_unknown_tilt_at_start_stales_a_box_confirmed_with_one():
    out = evaluate(_confirmed_record(), _ctx(tilt_offset_deg=None), tilt_margin_deg=1.0)
    assert out["box"]["state"] == "stale"


def test_aim_follows_box_id():
    record = _confirmed_record()
    record["box"]["id"] = "b2"
    out = evaluate(record, _ctx(), tilt_margin_deg=1.0)
    assert out["box"]["state"] == "confirmed"
    assert out["aim"]["state"] == "stale"
    assert out["ball_range"]["state"] == "stale"


def test_net_range_follows_the_aim_floor_line():
    record = _confirmed_record()
    record["aim"]["floor_line"] = 0.70
    out = evaluate(record, _ctx(), tilt_margin_deg=1.0)
    assert out["aim"]["state"] == "confirmed"
    assert out["net_range"]["state"] == "stale"
    assert "floor line" in out["net_range"]["reason"]


def test_scene_follows_ball_range():
    record = _confirmed_record()
    record["ball_range"]["range_m"] = 2.4
    out = evaluate(record, _ctx(), tilt_margin_deg=1.0)
    assert out["ball_range"]["state"] == "confirmed"
    assert out["scene"]["state"] == "stale"


def test_not_set_sections_stay_not_set(tmp_path):
    out = evaluate(load(tmp_path / "nope.json"), _ctx(), tilt_margin_deg=1.0)
    assert all(out[n]["state"] == "not_set" for n in SECTIONS)


def test_section_inputs_rejects_unknown_names():
    with pytest.raises(ValueError):
        section_inputs("box", colour="red")
    with pytest.raises(ValueError):
        section_inputs("garage")
