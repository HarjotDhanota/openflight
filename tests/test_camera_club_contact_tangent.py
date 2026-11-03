"""The club stage's attack angle is the head's direction AT the ball-defined contact (C10b).

The stage read the attack angle as the chord between the frame before the last one the
ball rested in and that frame (``(impact - 1, impact)``). The ball-defined contact lies
between the last rest frame and the first moved one, so that chord is centred one frame
before contact. A descending head's path steepens the earlier it is read; on Oct 27 shot
1 (3.617 ms frames, ~120 mm of head travel a frame) the chord read -13.2 deg. The
attack angle is now the velocity at the contact time of a constant-acceleration fit
(per axis, quadratic in time) through the head positions up to the last rest frame; with
fewer than three such frames the chord stands. Path is unchanged.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from openflight.camera import club_delivery as cd
from openflight.camera.ball_flight import located_tee_range_evidence
from openflight.camera.club_motion import ReferenceBall

FIXTURE = Path(__file__).parent / "fixtures" / "camera_oct27_shot1"


def _arc_positions(contact_t: float, *, attack_deg: float, radius_m: float, speed: float):
    """Head positions on a vertical circle whose tangent at ``contact_t`` is ``attack_deg``,
    one every 3.617 ms from 4 frames before contact to 1.8 ms before it."""
    step = 0.003617
    times = contact_t - 0.0018085 - step * np.arange(3, -1, -1)
    # arc angle phi from the low point; slope = tan(phi) with phi = -s / radius
    low_s = math.radians(-attack_deg) * radius_m  # arc length from contact to the low point
    positions = []
    for t in times:
        s = speed * (t - contact_t) - low_s  # signed arc length from the low point
        phi = s / radius_m
        positions.append(
            cd.HeadPosition(
                t_s=float(t),
                lateral_m=0.0,
                up_m=float(radius_m * (1 - math.cos(phi))),
                forward_m=float(1.8 + radius_m * math.sin(phi)),
            )
        )
    return tuple(positions), times


def test_the_attack_angle_is_the_tangent_at_contact_not_the_last_chord():
    positions, times = _arc_positions(10.0, attack_deg=-4.0, radius_m=1.25, speed=33.0)
    last, before = positions[-1], positions[-2]
    chord = math.degrees(math.atan2(last.up_m - before.up_m, last.forward_m - before.forward_m))
    assert chord < -8.0  # a frame early, the descending arc reads twice as steep

    attack = cd.attack_angle_at_contact(positions, contact_t_s=10.0, last_rest_t_s=times[-1])

    assert attack == pytest.approx(-4.0, abs=0.3)


def test_a_straight_approach_reads_the_same_either_way():
    positions = tuple(
        cd.HeadPosition(t_s=t, lateral_m=0.0, up_m=0.05 - 0.07 * t, forward_m=1.0 + t)
        for t in (0.0, 0.1, 0.2, 0.3)
    )
    attack = cd.attack_angle_at_contact(positions, contact_t_s=0.35, last_rest_t_s=0.3)
    assert attack == pytest.approx(math.degrees(math.atan(-0.07)), abs=1e-6)


def test_fewer_than_three_frames_before_contact_give_no_tangent():
    positions = tuple(
        cd.HeadPosition(t_s=t, lateral_m=0.0, up_m=-0.07 * t, forward_m=t) for t in (0.0, 0.1, 0.2)
    )
    assert cd.attack_angle_at_contact(positions, contact_t_s=0.15, last_rest_t_s=0.1) is None


def _shot1_club():
    """Oct 27 shot 1's club stage inputs: the fixture's crop on the background."""
    from tests.test_camera_shot1_departure import _clip, _effective_geometry  # noqa: PLC0415

    _frames, _archive, ball_inputs = _clip()
    inputs = json.loads((FIXTURE / "club_inputs.json").read_text(encoding="utf-8"))
    data = np.load(FIXTURE / "club_frames.npz")
    (r0, r1), (c0, c1) = inputs["crop"]["rows"], inputs["crop"]["cols"]
    first, last = inputs["crop"]["frames"]
    background = np.zeros(inputs["crop"]["full_shape"][1:], np.uint8)
    background[r0:r1, c0:c1] = data["background"]
    frames = np.repeat(background[None], inputs["crop"]["full_shape"][0], axis=0)
    frames[first : last + 1, r0:r1, c0:c1] = data["frames"]
    geometry = _effective_geometry(ball_inputs).delivery_geometry()
    return {
        "frames": frames,
        "host": data["host_timestamp_ns"],
        "kwargs": {
            "trigger_index": inputs["trigger_index"],
            "range_evidence": located_tee_range_evidence(
                inputs["located_tee_range_m"], inputs["ops_club_speed_mph"]
            ),
            "geometry": geometry,
            "ops_club_speed_mph": inputs["ops_club_speed_mph"],
            "reference_ball": ReferenceBall(**inputs["reference_ball"]),
            "reference_ball_selected": True,
            "sensor_timestamp_ns": data["sensor_timestamp_ns"],
        },
        "inputs": inputs,
    }


def test_oct27_shot1_reads_its_attack_angle_at_the_ball_defined_contact():
    case = _shot1_club()
    result = cd.estimate_chained_delivery(case["frames"], case["host"], **case["kwargs"])

    assert result.impact_frame == 43  # last rest; the ball moved in f44
    by_time = sorted(result.head_positions, key=lambda p: p.t_s)
    pre = [p for p in by_time if p.t_s <= by_time[0].t_s + 3 * 0.0037]
    assert len(pre) == 4  # f40-f43 before contact
    f42, f43 = pre[-2], pre[-1]
    chord = math.degrees(
        math.atan2(
            f43.up_m - f42.up_m,
            math.hypot(f43.forward_m - f42.forward_m, f43.lateral_m - f42.lateral_m),
        )
    )
    assert chord == pytest.approx(case["inputs"]["live_result"]["attack_angle_deg"], abs=0.5)
    # the tangent at contact of the pre-contact positions, shallower than the chord
    assert result.attack_angle_deg == pytest.approx(
        cd.attack_angle_at_contact(by_time, contact_t_s=result.contact_t_s, last_rest_t_s=f43.t_s),
        abs=0.01,
    )
    assert result.attack_angle_deg > chord + 2.0
    # path is not touched (the live value is turned by the session's +2.07 deg target line;
    # this geometry has none)
    live_path = case["inputs"]["live_result"]["club_path_deg"] - 2.0666
    assert result.club_path_deg == pytest.approx(live_path, abs=0.01)


# --- C13b: the measured contact instant is the club stage's contact -----------------------


def _measured(sensor_ns, sigma_ms=0.1, rest=43):
    return {
        "status": "measured",
        "source": "ball_departure_extrapolation",
        "clock": "sensor_timestamp_ns",
        "sensor_ns": sensor_ns,
        "sigma_ms": sigma_ms,
        "last_rest_frame": rest,
        "first_moved_frame": rest + 1,
        "exposure_centre_offset_ns": 149_000.0,
    }


def test_the_measured_contact_instant_replaces_the_frame_midpoint():
    case = _shot1_club()
    stamps = case["kwargs"]["sensor_timestamp_ns"]
    midpoint = (int(stamps[43]) + int(stamps[44])) / 2.0
    # an instant 0.7 ms after the midpoint, on the exposure-centre clock
    record = _measured(midpoint + 0.7e6 + 149_000.0)

    result = cd.estimate_chained_delivery(
        case["frames"], case["host"], **case["kwargs"], measured_contact=record
    )

    assert result.contact_t_s == pytest.approx((midpoint + 0.7e6) / 1e9, abs=1e-9)
    assert result.contact_source == "ball_departure_extrapolation"
    assert result.contact_sigma_ms == pytest.approx(0.1)
    by_time = sorted(result.head_positions, key=lambda p: p.t_s)
    last_rest_t = by_time[3].t_s
    assert result.attack_angle_deg == pytest.approx(
        cd.attack_angle_at_contact(
            by_time, contact_t_s=result.contact_t_s, last_rest_t_s=last_rest_t
        ),
        abs=0.01,
    )


def test_without_a_measured_instant_the_midpoint_stands_labelled():
    case = _shot1_club()
    result = cd.estimate_chained_delivery(case["frames"], case["host"], **case["kwargs"])
    stamps = case["kwargs"]["sensor_timestamp_ns"]
    assert result.contact_t_s == pytest.approx((int(stamps[43]) + int(stamps[44])) / 2e9)
    assert result.contact_source == "frame_midpoint"


def test_a_measured_instant_outside_its_own_bracket_is_not_used():
    case = _shot1_club()
    stamps = case["kwargs"]["sensor_timestamp_ns"]
    record = _measured(float(stamps[47]), rest=43)  # says f43|f44 but lands in f47
    result = cd.estimate_chained_delivery(
        case["frames"], case["host"], **case["kwargs"], measured_contact=record
    )
    assert result.contact_source == "frame_midpoint"
    assert any("measured contact" in note for note in result.notes)


def test_the_fusion_hands_the_fallback_s_contact_to_the_club_stage(monkeypatch):
    from openflight.camera import fusion_processing as fp  # noqa: PLC0415

    record = _measured(1.0e9)
    monkeypatch.setattr(
        fp, "run_ball_fallback", lambda *a, **k: {"status": "accepted", "contact": record}
    )
    seen = {}

    def spy(*_args, **kwargs):
        seen.update(kwargs)
        raise ValueError("stop after the inputs")

    monkeypatch.setattr(fp, "estimate_chained_delivery", spy)
    from tests.camera_fallback_scene import render_scene  # noqa: PLC0415
    from tests.test_fallback_chain import _scene_context  # noqa: PLC0415

    context, archive = _scene_context(render_scene())
    fp.process_camera_fusion(context, archive)

    assert seen["measured_contact"] == record
