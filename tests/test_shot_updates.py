"""Folding late shot updates into the shot they update, for every log reader."""

from openflight.shot_updates import apply_shot_updates, read_session_jsonl


def _detected(number, **values):
    return {"ts": f"t{number}", "type": "shot_detected", "shot_number": number, **values}


def _updated(number, **values):
    return {
        "ts": f"u{number}",
        "type": "shot_updated",
        "shot_number": number,
        "update": {"reason": "late_enrichment", "late_by_ms": 900.0},
        **values,
    }


def test_update_replaces_the_shot_in_place_and_counts_it_once():
    events = [
        {"type": "session_start", "session_uuid": "s"},
        _detected(1, ball_speed_mph=150.0, launch_angle_vertical=None),
        {"type": "iwr6843_capture", "shot_number": 1},
        _detected(2, ball_speed_mph=140.0),
        _updated(1, ball_speed_mph=150.0, launch_angle_vertical=14.0),
    ]

    folded = apply_shot_updates(events)

    assert [event["type"] for event in folded] == [
        "session_start",
        "shot_detected",
        "iwr6843_capture",
        "shot_detected",
    ]
    assert folded[1]["launch_angle_vertical"] == 14.0
    assert folded[1]["ts"] == "t1", "the shot keeps its detection time"
    assert "update" not in folded[1], "the folded shot keeps the shot_detected schema"
    assert folded[3] == events[3]
    assert events[1]["launch_angle_vertical"] is None, "the input is not mutated"


def test_the_latest_of_several_updates_wins():
    folded = apply_shot_updates(
        [_detected(1, spin_rpm=None), _updated(1, spin_rpm=5000), _updated(1, spin_rpm=6100)]
    )

    assert [event["spin_rpm"] for event in folded] == [6100]


def test_an_update_without_its_shot_becomes_that_shot_once():
    folded = apply_shot_updates([_updated(3, ball_speed_mph=120.0), _updated(3, spin_rpm=1)])

    assert len(folded) == 1
    assert folded[0]["type"] == "shot_detected"
    assert folded[0]["shot_number"] == 3
    assert folded[0]["spin_rpm"] == 1
    assert "update" not in folded[0]


def test_updates_never_cross_a_session_start():
    events = [
        {"type": "session_start", "session_uuid": "a"},
        _detected(1, launch_angle_vertical=None),
        {"type": "session_start", "session_uuid": "b"},
        _detected(1, launch_angle_vertical=None),
        _updated(1, launch_angle_vertical=9.0),
    ]

    folded = apply_shot_updates(events)

    assert folded[1]["launch_angle_vertical"] is None
    assert folded[3]["launch_angle_vertical"] == 9.0


def test_non_shot_and_malformed_events_pass_through():
    events = ["not-a-dict", {"type": "trigger_event"}, {"type": "shot_updated"}, _detected(1)]

    assert apply_shot_updates(events) == events


def test_read_session_jsonl_skips_bad_lines_and_folds(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"type": "session_start"}',
                "not json",
                "[1, 2]",
                "",
                '{"type": "shot_detected", "shot_number": 1, "spin_rpm": null}',
                '{"type": "shot_updated", "shot_number": 1, "spin_rpm": 5000}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert read_session_jsonl(path) == [
        {"type": "session_start"},
        {"type": "shot_detected", "shot_number": 1, "spin_rpm": 5000},
    ]
