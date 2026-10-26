import json
from types import SimpleNamespace

import pytest

from openflight.iwr6843.calibration import Calibration
from openflight.iwr6843.replay import (
    ReplayInput,
    input_from_dump,
    inputs_from_session,
    replay_capture,
    replay_summary,
    write_records,
)


def test_inputs_from_session_extracts_debug_dump_references(tmp_path):
    dump = tmp_path / "shot.l3dump"
    dump.write_bytes(b"raw")
    session = tmp_path / "session.jsonl"
    session.write_text(
        "\n".join(
            [
                json.dumps({"type": "shot_detected", "shot_number": 1}),
                json.dumps(
                    {
                        "type": "iwr6843_capture",
                        "shot_number": 1,
                        "capture_path": "shot.l3dump",
                        "ball_speed_mph": 101.5,
                    }
                ),
                json.dumps(
                    {
                        "type": "iwr6843_capture",
                        "shot_number": 2,
                        "capture_path": None,
                        "ball_speed_mph": 102.5,
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    inputs = inputs_from_session(session, club="9i")

    assert inputs == [
        ReplayInput(
            source=str(session),
            shot_number=1,
            capture_path=dump,
            ball_speed_mph=101.5,
            club="9i",
        )
    ]


def test_replay_capture_reports_missing_file(tmp_path):
    cal = Calibration.identity()
    cal.tee_range_m = 1.5

    record = replay_capture(
        ReplayInput(
            source="single",
            shot_number=None,
            capture_path=tmp_path / "missing.l3dump",
            ball_speed_mph=100.0,
        ),
        cal,
        net_range_m=4.6,
        tx_order="normal",
    )

    assert record.status == "missing_capture_file"
    assert not record.accepted
    assert "missing.l3dump" in record.error


def test_replay_capture_uses_estimator_result(monkeypatch, tmp_path):
    dump = tmp_path / "shot.l3dump"
    dump.write_bytes(b"raw")
    cal = Calibration.identity()
    cal.tee_range_m = 1.5

    def fake_estimator(raw, calibration, **kwargs):
        assert raw == b"raw"
        assert calibration is cal
        assert kwargs["ball_speed_mph"] == 101.0
        return SimpleNamespace(
            accepted=True,
            status="accepted",
            angle_deg=19.2,
            raw_angle_deg=19.2,
            component_std_deg=0.8,
            n_snapshots=20,
            n_frames=6,
            track_speed_mph=100.0,
            track_rms_bins=0.2,
            track_inliers=44,
        )

    monkeypatch.setattr(
        "openflight.iwr6843.replay.process_dump",
        lambda *_args, **_kwargs: SimpleNamespace(
            track=SimpleNamespace(bin_at=lambda _t: 33.0),
            geometry=SimpleNamespace(range_res_m=0.05),
        ),
    )

    record = replay_capture(
        ReplayInput(
            source="single",
            shot_number=4,
            capture_path=dump,
            ball_speed_mph=101.0,
            club="7i",
        ),
        cal,
        net_range_m=4.6,
        tx_order="normal",
        estimator=fake_estimator,
    )

    assert record.accepted
    assert record.launch_angle_deg == 19.2
    assert record.track_start_range_m == pytest.approx(1.65)


def test_replay_summary_and_csv_output(tmp_path):
    records = [
        SimpleNamespace(
            accepted=True,
            launch_angle_deg=20.0,
            component_std_deg=1.0,
            track_rms_bins=0.2,
            track_start_range_m=1.5,
            to_dict=lambda: {"shot_number": 1, "status": "accepted"},
        ),
        SimpleNamespace(
            accepted=False,
            launch_angle_deg=None,
            component_std_deg=None,
            track_rms_bins=None,
            track_start_range_m=None,
            to_dict=lambda: {"shot_number": 2, "status": "rejected"},
        ),
    ]

    summary = replay_summary(records)

    assert summary["shots"] == 2
    assert summary["accepted_shots"] == 1
    assert summary["coverage"] == 0.5
    assert summary["median_launch_angle_deg"] == 20.0

    out = tmp_path / "replay.jsonl"
    write_records(records, out, "jsonl")
    assert len(out.read_text(encoding="utf-8").splitlines()) == 2


def test_a_legacy_replay_reads_its_single_height_as_the_rx_row():
    """Sessions with one radar height and no rig replay it as the RX-row centre of a +90 deg board."""
    from openflight.iwr6843 import antennas, lcmf  # noqa: PLC0415
    from openflight.iwr6843.replay import build_replay_calibration  # noqa: PLC0415

    cal = build_replay_calibration(
        "config/iwr6843_calibration_reference.json",
        tee_range_m=1.372,
        tilt_deg=5.5,
        radar_height_m=0.229,
        ball_height_m=0.040,
    )
    assert cal.antennas is None
    assert lcmf.antenna_layout(cal) == antennas.legacy_layout(0.229)


def test_a_rig_replay_places_the_antennas_off_its_phase_centre():
    from openflight.iwr6843 import antennas  # noqa: PLC0415
    from openflight.iwr6843.replay import build_replay_calibration  # noqa: PLC0415

    cal = build_replay_calibration(
        "config/iwr6843_calibration_reference.json",
        tee_range_m=1.5,
        tilt_deg=10.0,
        radar_height_m=0.05885,
        ball_height_m=0.021,
        board_rotation_deg=90.0,
    )
    assert cal.antennas.basis == antennas.RIG_BASIS
    assert cal.antennas.rx_row_height_m == pytest.approx(0.051, abs=1e-5)


def test_the_replay_record_names_the_estimator_and_the_antenna_basis(monkeypatch, tmp_path):
    from openflight.iwr6843 import antennas, lcmf  # noqa: PLC0415

    monkeypatch.setattr(
        "openflight.iwr6843.replay.process_dump",
        lambda *_args, **_kwargs: SimpleNamespace(track=None),
    )

    dump = tmp_path / "shot.l3dump"
    dump.write_bytes(b"raw")
    cal = Calibration.identity()
    cal.tee_range_m = 1.5
    cal.meta["radar_height_m"] = 0.229

    def fake_estimator(_raw, _calibration, **_kwargs):
        return SimpleNamespace(
            accepted=False,
            status="rejected_by_ball_tracker",
            angle_deg=None,
            raw_angle_deg=None,
            component_std_deg=None,
            n_snapshots=0,
            n_frames=0,
            track_speed_mph=None,
            track_rms_bins=None,
            track_inliers=None,
            to_dict=lambda: {"estimator": lcmf.NAME},
        )

    record = replay_capture(
        ReplayInput("single", None, dump, 100.0),
        cal,
        net_range_m=4.6,
        tx_order="normal",
        estimator=fake_estimator,
    )

    assert record.to_dict()["estimator"] == lcmf.NAME
    assert record.to_dict()["antenna_basis"] == antennas.LEGACY_BASIS


def test_the_replay_calibration_takes_the_recorded_roll(tmp_path):
    import math  # noqa: PLC0415

    from openflight.iwr6843.replay import build_replay_calibration  # noqa: PLC0415

    def build(**extra):
        return build_replay_calibration(
            "config/iwr6843_calibration_reference.json",
            tee_range_m=1.5,
            tilt_deg=10.0,
            radar_height_m=0.05,
            ball_height_m=0.04,
            board_rotation_deg=90.0,
            **extra,
        )

    assert build().roll_rad == 0.0
    assert math.degrees(build(roll_deg=-2.0).roll_rad) == pytest.approx(-2.0)


def test_replay_loads_a_saved_two_round_capture(monkeypatch, tmp_path):
    from openflight.iwr6843.dump import parse_dump
    from openflight.iwr6843.sparse_capture import SparseDump
    from openflight.iwr6843.two_round import TwoRoundClient, run_two_round
    from openflight.iwr6843.two_round_file import pack_sparse_capture
    from openflight.iwr6843.two_round_sim import SimulatedIWR6843TwoRound, SimulatedSerial
    from tests.iwr6843_partial_scene import pack, scene

    raw = pack(scene(6, seed=2))
    radar = SimulatedIWR6843TwoRound()
    radar.freeze(raw)
    _result, transfer = run_two_round(
        TwoRoundClient(SimulatedSerial(radar), timeout_s=0.05),
        lambda dump: parse_dump(dump)[1][2, 0, 0, 7],
    )
    path = tmp_path / "iwr6843_20261026_101500_000_001.l3sparse"
    path.write_bytes(pack_sparse_capture(transfer))
    seen = []

    def fake_estimator(capture, _calibration, **_kwargs):
        seen.append(capture)
        _meta, cube = parse_dump(capture)
        assert cube[2, 0, 0, 7] == parse_dump(raw)[1][2, 0, 0, 7]
        return SimpleNamespace(
            accepted=False,
            status="rejected_no_track",
            angle_deg=None,
            raw_angle_deg=None,
            component_std_deg=None,
            n_snapshots=0,
            n_frames=0,
            track_speed_mph=None,
            track_rms_bins=None,
            track_inliers=0,
        )

    monkeypatch.setattr(
        "openflight.iwr6843.replay.process_dump",
        lambda *_args, **_kwargs: SimpleNamespace(track=None),
    )
    cal = Calibration.identity()
    cal.tee_range_m = 1.5
    record = replay_capture(
        input_from_dump(path, ball_speed_mph=100.0),
        cal,
        net_range_m=4.6,
        tx_order="normal",
        estimator=fake_estimator,
    )

    assert record.status == "rejected_no_track", record.error
    assert isinstance(seen[0], SparseDump)
