"""Wiring for the track-derived club metrics: what reaches a shot, and what does not.

These tests are about PLUMBING and POLICY, not about accuracy. The reconstruction
itself is covered by `test_clubpose_head_track.py` and the inversion by
`test_club_metrics.py`; here the questions are whether the pipeline hands the
right evidence to those modules, whether a missing sensor produces a named
reason instead of a number, and -- most importantly -- whether anything new
writes over a shipped field. Nothing here may change `launch_angle_vertical`,
the fused estimator, or the IWR club path.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest

import openflight.server as server_module
from openflight.camera.club_motion import ReferenceBall
from openflight.camera.clubpose.head_track import HeadTrack, HeadTrackQuality
from openflight.launch_monitor import Shot
from openflight.server import shot_to_dict

CAPTURE_FPS = 467.6


def _ok_track() -> HeadTrack:
    return HeadTrack(
        status="ok",
        reason="ok",
        swing_plane_deg=58.9,
        swing_direction_deg=4.3,
        low_point_mm=42.0,
        arc_radius_m=1.14,
        club_path_deg=1.6,
        attack_angle_deg=-4.6,
        in_plane_speed_mph=71.8,
        contact_world_mm=(0.0, 12.0, -3.0),
        quality=HeadTrackQuality(n_frames=12, n_pre=9, n_post=3, plane_rms_residual_mm=5.5),
    )


def _archive(frames: int = 96) -> dict:
    return {
        "frames": np.zeros((frames, 200, 320), dtype=np.uint8),
        "host_timestamp_ns": (np.arange(frames, dtype=np.int64) * int(1e9 / CAPTURE_FPS)),
        "trigger_host_timestamp_ns": np.int64(0),
        "pre_trigger_count": np.int32(74),
    }


@pytest.fixture(name="rig")
def _rig(monkeypatch, tmp_path):
    """A camera capture, an IWR runtime and a club range evidence that all exist."""
    monkeypatch.setattr(
        server_module,
        "camera_capture_config",
        {
            "mount_height_m": 0.2032,
            "lateral_offset_m": -0.060325,
            "mirror_horizontal": True,
            "width": 320,
            "height": 200,
        },
    )
    monkeypatch.setattr(
        server_module,
        "iwr6843_runtime",
        SimpleNamespace(
            calibration=SimpleNamespace(
                tee_range_m=1.575, radar_height_m=0.1524, tee_ball_height_m=0.04
            )
        ),
    )
    monkeypatch.setattr(
        server_module,
        "_load_camera_capture_archive",
        lambda _capture: _archive(),
    )
    monkeypatch.setattr(
        "openflight.camera.club_motion.detect_reference_ball",
        lambda _frames: ReferenceBall(x=160.0, y=94.0, diameter_px=12.77, area_px=128),
    )
    evidence = SimpleNamespace(
        track=SimpleNamespace(slope_bins=-1.6),
        geometry=SimpleNamespace(range_res_m=0.046875),
        impact_t_s=0.0,
    )
    return SimpleNamespace(capture=SimpleNamespace(valid=True, path=tmp_path), evidence=evidence)


def _shot(**overrides) -> Shot:
    defaults = {
        "ball_speed_mph": 110.0,
        "timestamp": datetime.now(),
        "launch_angle_vertical": 19.6,
        "launch_angle_horizontal": 0.5,
        "experimental_camera_vertical_launch_deg": 24.2,
    }
    return Shot(**{**defaults, **overrides})


def test_no_camera_capture_yields_a_named_reason_and_no_numbers() -> None:
    shot = _shot()

    server_module._fuse_track_club_metrics(shot, None)

    assert shot.experimental_track_status == "rejected_no_camera_capture"
    assert shot.experimental_swing_plane_deg is None
    assert shot.experimental_track_club_path_deg is None
    assert shot.experimental_dynamic_loft_deg is None


def test_without_iwr_range_evidence_the_track_is_not_attempted(rig, monkeypatch) -> None:
    """The radar owns the depth; with no range walk there is nothing to lift."""
    monkeypatch.setattr(
        "openflight.camera.clubpose.head_track.build_head_track",
        lambda *_args, **_kwargs: pytest.fail("no range evidence: must not segment"),
    )
    shot = _shot(iwr6843_club_range_evidence=None)

    server_module._fuse_track_club_metrics(shot, rig.capture)

    assert shot.experimental_track_status == "rejected_no_iwr_range_evidence"
    assert shot.experimental_swing_plane_deg is None


def test_a_reconstructed_track_populates_its_own_fields(rig, monkeypatch) -> None:
    monkeypatch.setattr(
        "openflight.camera.clubpose.head_track.build_head_track",
        lambda *_args, **_kwargs: _ok_track(),
    )
    shot = _shot(iwr6843_club_range_evidence=rig.evidence)

    server_module._fuse_track_club_metrics(shot, rig.capture)

    assert shot.experimental_track_status == "ok"
    assert shot.experimental_swing_plane_deg == pytest.approx(58.9)
    assert shot.experimental_swing_direction_deg == pytest.approx(4.3)
    assert shot.experimental_track_club_path_deg == pytest.approx(1.6)
    assert shot.experimental_track_attack_angle_deg == pytest.approx(-4.6)
    assert shot.experimental_low_point_mm == pytest.approx(42.0)
    assert shot.experimental_arc_radius_m == pytest.approx(1.14)


def test_the_track_is_given_un_mirrored_frames(rig, monkeypatch) -> None:
    """A mirrored preview would put the swing direction on the wrong side."""
    seen: dict[str, object] = {}
    marked = _archive()
    marked["frames"][0, 0, 0] = 200  # a corner the mirror has to move
    monkeypatch.setattr(server_module, "_load_camera_capture_archive", lambda _c: marked)

    def capture_frames(frames, *_args, **_kwargs):
        seen["frames"] = frames
        return _ok_track()

    monkeypatch.setattr("openflight.camera.clubpose.head_track.build_head_track", capture_frames)
    shot = _shot(iwr6843_club_range_evidence=rig.evidence)

    server_module._fuse_track_club_metrics(shot, rig.capture)

    assert seen["frames"][0, 0, -1] == 200
    assert seen["frames"][0, 0, 0] == 0


def test_the_contact_frame_is_the_trigger_less_the_sound_flight_time(rig, monkeypatch) -> None:
    """f_c = pre_trigger_frames - tee_range / speed of sound * fps.

    The acoustic trigger fires when the impact reaches the unit, not when it
    happens, and the lag belongs to the installation rather than to the
    software: it scales with how far the unit sits from the ball.
    """
    seen: dict[str, float] = {}

    def capture_contact(*_args, **kwargs):
        seen.update(kwargs)
        return _ok_track()

    monkeypatch.setattr("openflight.camera.clubpose.head_track.build_head_track", capture_contact)
    shot = _shot(iwr6843_club_range_evidence=rig.evidence)

    server_module._fuse_track_club_metrics(shot, rig.capture)

    expected = 74 - 1.575 / 343.0 * seen["fps"]
    assert seen["f_contact"] == pytest.approx(expected, abs=0.01)
    assert seen["fps"] == pytest.approx(CAPTURE_FPS, rel=1e-3)


def test_the_radar_range_rate_comes_from_the_club_range_evidence(rig, monkeypatch) -> None:
    seen: dict[str, float] = {}

    def capture_rate(*_args, **kwargs):
        seen.update(kwargs)
        return _ok_track()

    monkeypatch.setattr("openflight.camera.clubpose.head_track.build_head_track", capture_rate)
    shot = _shot(iwr6843_club_range_evidence=rig.evidence)

    server_module._fuse_track_club_metrics(shot, rig.capture)

    assert seen["range_rate_ms"] == pytest.approx(-1.6 * 0.046875)


def test_a_low_quality_track_is_recorded_but_offers_no_angles(rig, monkeypatch) -> None:
    """A jackknife that swings the answer means nothing was measured."""
    monkeypatch.setattr(
        "openflight.camera.clubpose.head_track.build_head_track",
        lambda *_a, **_k: HeadTrack(
            status="low_quality",
            reason="jackknife swing plane spans 55 deg",
            swing_plane_deg=61.0,
            club_path_deg=1.0,
            attack_angle_deg=-3.0,
        ),
    )
    shot = _shot(iwr6843_club_range_evidence=rig.evidence)

    server_module._fuse_track_club_metrics(shot, rig.capture)

    assert shot.experimental_track_status == "low_quality"
    assert shot.experimental_swing_plane_deg is None
    assert shot.experimental_track_club_path_deg is None
    assert shot.experimental_dynamic_loft_deg is None


def test_the_dplane_loft_uses_the_camera_launch_angle(rig, monkeypatch) -> None:
    monkeypatch.setattr(
        "openflight.camera.clubpose.head_track.build_head_track", lambda *_a, **_k: _ok_track()
    )
    shot = _shot(iwr6843_club_range_evidence=rig.evidence)

    server_module._fuse_track_club_metrics(shot, rig.capture)

    assert shot.experimental_dynamic_loft_status == "experimental"
    assert shot.experimental_dynamic_loft_deg == pytest.approx(-4.6 + (24.2 + 4.6) / 0.81, abs=1e-6)
    assert shot.experimental_spin_loft_deg == pytest.approx((24.2 + 4.6) / 0.81, abs=1e-6)


def test_without_the_camera_launch_angle_the_loft_is_withheld(rig, monkeypatch) -> None:
    monkeypatch.setattr(
        "openflight.camera.clubpose.head_track.build_head_track", lambda *_a, **_k: _ok_track()
    )
    shot = _shot(
        iwr6843_club_range_evidence=rig.evidence,
        experimental_camera_vertical_launch_deg=None,
    )

    server_module._fuse_track_club_metrics(shot, rig.capture)

    assert shot.experimental_dynamic_loft_status == "withheld_lcmf_launch_bias"
    assert shot.experimental_dynamic_loft_deg is None
    assert shot.experimental_spin_loft_deg is None


def test_the_face_angle_is_stored_with_a_status_that_forbids_display(rig, monkeypatch) -> None:
    monkeypatch.setattr(
        "openflight.camera.clubpose.head_track.build_head_track", lambda *_a, **_k: _ok_track()
    )
    shot = _shot(iwr6843_club_range_evidence=rig.evidence)

    server_module._fuse_track_club_metrics(shot, rig.capture)

    assert shot.experimental_face_angle_status == "withheld_launch_direction_offset"
    assert shot.experimental_face_angle_deg == pytest.approx(1.6 + (0.5 - 1.6) / 0.69, abs=1e-6)


def test_the_track_fusion_never_writes_a_shipped_field(rig, monkeypatch) -> None:
    """The whole promotion is additive; nothing may replace a displayed value."""
    monkeypatch.setattr(
        "openflight.camera.clubpose.head_track.build_head_track", lambda *_a, **_k: _ok_track()
    )
    shot = _shot(
        iwr6843_club_range_evidence=rig.evidence,
        experimental_fused_club_path_deg=5.9,
        experimental_fused_attack_angle_deg=-4.6,
        club_path_deg=3.0,
    )

    server_module._fuse_track_club_metrics(shot, rig.capture)

    assert shot.launch_angle_vertical == pytest.approx(19.6)
    assert shot.launch_angle_horizontal == pytest.approx(0.5)
    assert shot.experimental_fused_club_path_deg == pytest.approx(5.9)
    assert shot.experimental_fused_attack_angle_deg == pytest.approx(-4.6)
    assert shot.club_path_deg == pytest.approx(3.0)


def test_a_reconstruction_error_is_reported_not_raised(rig, monkeypatch) -> None:
    monkeypatch.setattr(
        "openflight.camera.clubpose.head_track.build_head_track",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    shot = _shot(iwr6843_club_range_evidence=rig.evidence)

    server_module._fuse_track_club_metrics(shot, rig.capture)

    assert shot.experimental_track_status == "error"
    assert shot.experimental_swing_plane_deg is None


def test_shot_to_dict_carries_the_new_fields() -> None:
    shot = _shot(
        experimental_track_status="ok",
        experimental_swing_plane_deg=58.9,
        experimental_swing_direction_deg=4.3,
        experimental_track_club_path_deg=1.6,
        experimental_track_attack_angle_deg=-4.6,
        experimental_low_point_mm=42.0,
        experimental_arc_radius_m=1.14,
        experimental_dynamic_loft_deg=30.6,
        experimental_dynamic_loft_status="experimental",
        experimental_spin_loft_deg=35.2,
        experimental_spin_loft_status="experimental",
        experimental_face_angle_deg=-0.09,
        experimental_face_angle_status="withheld_launch_direction_offset",
        experimental_camera_vertical_launch_status="accepted",
    )

    payload = shot_to_dict(shot)

    assert payload["experimental_swing_plane_deg"] == pytest.approx(58.9)
    assert payload["experimental_swing_direction_deg"] == pytest.approx(4.3)
    assert payload["experimental_track_club_path_deg"] == pytest.approx(1.6)
    assert payload["experimental_track_attack_angle_deg"] == pytest.approx(-4.6)
    assert payload["experimental_low_point_mm"] == pytest.approx(42.0)
    assert payload["experimental_arc_radius_m"] == pytest.approx(1.14)
    assert payload["experimental_dynamic_loft_deg"] == pytest.approx(30.6)
    assert payload["experimental_spin_loft_deg"] == pytest.approx(35.2)
    assert payload["experimental_face_angle_deg"] == pytest.approx(-0.09)
    assert payload["experimental_face_angle_status"] == "withheld_launch_direction_offset"
    assert payload["experimental_camera_vertical_launch_deg"] == pytest.approx(24.2)
    assert payload["experimental_camera_vertical_launch_status"] == "accepted"
    assert payload["experimental_track_status"] == "ok"


def test_the_session_logger_records_the_track_metrics_block(tmp_path) -> None:
    """The block travels as one field; empty entries are dropped as elsewhere."""
    import json  # noqa: PLC0415

    from openflight.session_logger import SessionLogger  # noqa: PLC0415

    logger = SessionLogger(log_dir=tmp_path, enabled=True)
    logger.start_session(mode="rolling-buffer", trigger_type="sound")
    logger.log_shot(
        ball_speed_mph=110.0,
        club_speed_mph=80.0,
        smash_factor=1.375,
        estimated_carry_yards=160.0,
        club="7-iron",
        peak_magnitude=10.0,
        readings_count=1,
        experimental_track_metrics={
            "experimental_track_status": "ok",
            "experimental_swing_plane_deg": 58.9,
            "experimental_face_angle_deg": -0.09,
            "experimental_face_angle_status": "withheld_launch_direction_offset",
            "experimental_dynamic_loft_deg": None,
        },
    )

    entry = json.loads(logger.session_path.read_text().strip().splitlines()[-1])
    assert entry["experimental_track_status"] == "ok"
    assert entry["experimental_swing_plane_deg"] == pytest.approx(58.9)
    assert entry["experimental_face_angle_status"] == "withheld_launch_direction_offset"
    assert "experimental_dynamic_loft_deg" not in entry


def test_camera_fusion_runs_the_track_metrics_on_the_shared_archive(monkeypatch, tmp_path) -> None:
    """One NPZ decode still serves every per-shot estimator."""
    np.savez(
        tmp_path / "frames.npz",
        frames=np.zeros((8, 4, 4), dtype=np.uint8),
        host_timestamp_ns=np.arange(8, dtype=np.int64),
        trigger_host_timestamp_ns=np.int64(3),
        pre_trigger_count=np.int32(4),
    )
    seen = []
    for name in (
        "_fuse_camera_ball_flight",
        "_fuse_camera_club_delivery",
        "_fuse_track_club_metrics",
    ):
        monkeypatch.setattr(
            server_module, name, lambda _s, _c, archive, _n=name: seen.append((_n, archive))
        )
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now())

    server_module._fuse_camera_measurements(shot, SimpleNamespace(valid=True, path=tmp_path))

    assert [name for name, _ in seen] == [
        "_fuse_camera_ball_flight",
        "_fuse_camera_club_delivery",
        "_fuse_track_club_metrics",
    ]
    assert len({id(archive) for _, archive in seen}) == 1
