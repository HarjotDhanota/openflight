"""The kiosk's label on a weak or impossible horizontal launch.

Field session Harjot-outdoor-test-1 (24 Oct 2026): shot 14's low-coherence IWR horizontal
read +70.6 deg and showed "H. LAUNCH +70.6 MEAS", while the full ball speed refused the
same input as "not a measured horizontal launch". Every IWR horizontal on that rig was
also azimuth-uncalibrated (its start direction has its own zero): "estimated" until the
azimuth is calibrated, as the full ball speed already refused it.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from openflight import (
    full_speed,
    server as server_module,
    shot_metrics,
    shot_models,
)
from openflight.clubs import ClubType
from openflight.horizontal_frame import MAX_PLAUSIBLE_LAUNCH_DEG, implausible_launch_reason
from openflight.launch_monitor import Shot
from openflight.sim.resolver import resolve_shot
from openflight.sim.types import PlayerState, UnmeasuredShotError
from openflight.source_labels import (
    AZIMUTH_UNCALIBRATED,
    WEAK_HORIZONTAL_SOURCES,
    horizontal_launch_label,
)

SHOT_14 = {
    "ball_speed_mph": 45.1,
    "launch_angle_vertical": 13.57,
    "launch_angle_vertical_source": "radar",
    "inclinometer": {"applied_to": ["camera", "iwr6843"]},
    "launch_angle_horizontal": 70.6261883479819,
    "launch_angle_horizontal_confidence": 0.89,
    "launch_angle_horizontal_source": "radar_low_coherence",
    "launch_angle_horizontal_status": "azimuth_uncalibrated",
    "launch_angle_horizontal_frame": "target_line",
}


# ---------------------------------------------------------------- the bound


@pytest.mark.parametrize("value", [0.0, 12.5, -30.0, 45.0, -45.0])
def test_a_horizontal_inside_a_shank_s_reach_is_plausible(value):
    assert implausible_launch_reason(value) is None


@pytest.mark.parametrize("value", [45.1, 70.63, -60.0, 179.0])
def test_a_horizontal_wider_than_a_shank_is_not_the_ball_s(value):
    reason = implausible_launch_reason(value)

    assert reason is not None
    assert f"{value:+.1f}" in reason
    assert f"{MAX_PLAUSIBLE_LAUNCH_DEG:.0f}" in reason


@pytest.mark.parametrize("value", [None, float("nan"), float("inf")])
def test_a_missing_horizontal_is_not_called_implausible(value):
    assert implausible_launch_reason(value) is None


# ---------------------------------------------------------------- the label


@pytest.mark.parametrize("source", sorted(WEAK_HORIZONTAL_SOURCES))
def test_a_weak_horizontal_source_is_experimental(source):
    # Q3 (25 Oct 2026): it failed its own gate (coherence, consensus)
    assert horizontal_launch_label(source) == "experimental"


@pytest.mark.parametrize(
    ("source", "label"),
    [
        ("radar", "measured"),
        # a camera horizontal on an inferred geometry is never measured (audit 25 Oct)
        ("camera_assisted_experimental", "experimental"),
        ("camera_fallback", "experimental"),
        ("estimated", "estimated"),
    ],
)
def test_other_horizontal_sources_keep_their_label(source, label):
    assert horizontal_launch_label(source) == label


def test_every_weak_horizontal_source_is_one_the_full_ball_speed_refuses():
    """The kiosk label and the full ball speed read the same input the same way."""
    assert not set(WEAK_HORIZONTAL_SOURCES) & full_speed.MEASURED_HORIZONTAL_SOURCES


def test_a_low_coherence_horizontal_record_is_experimental_not_measured():
    record = shot_metrics.from_record({**SHOT_14, "launch_angle_horizontal": 3.2})[
        "launch_horizontal"
    ]

    assert record["value"] == pytest.approx(3.2)
    assert record["status"] == "experimental"
    assert record["source"] == "radar_low_coherence"
    assert "low-coherence" in record["note"]
    assert "azimuth uncalibrated" in record["note"]


def test_a_low_consensus_camera_horizontal_record_is_experimental():
    record = shot_metrics.from_record(
        {"launch_angle_horizontal": -4.0, "launch_angle_horizontal_source": "camera_low_consensus"}
    )["launch_horizontal"]

    assert record["status"] == "experimental"
    assert "low-consensus" in record["note"]


def test_shot_14_s_horizontal_is_not_shown_as_a_number():
    record = shot_metrics.from_record(SHOT_14)["launch_horizontal"]

    assert record["value"] is None
    assert record["status"] == "not_measured"
    assert "+70.6" in record["reason"]


def test_a_withheld_horizontal_record_gives_the_kiosk_s_reason():
    shot = {"launch_angle_horizontal_reason": "radar_low_coherence +70.6° is beyond ±45°"}

    record = shot_metrics.from_record(shot)["launch_horizontal"]

    assert record["status"] == "not_measured"
    assert record["reason"] == "radar_low_coherence +70.6° is beyond ±45°"


def test_a_measured_radar_horizontal_stays_measured():
    record = shot_metrics.from_record(
        {"launch_angle_horizontal": 2.0, "launch_angle_horizontal_source": "radar"}
    )["launch_horizontal"]

    assert record["status"] == "measured"
    assert record["note"] is None


def test_the_full_ball_speed_refuses_an_implausible_measured_horizontal():
    value, reason = full_speed._horizontal_boresight(  # pylint: disable=protected-access
        70.6, "radar", None, "unit_boresight", None
    )

    assert value is None
    assert "+70.6" in reason


# ---------------------------------------------------------------- the kiosk's shot


def _iwr_shot(
    monkeypatch,
    *,
    horizontal_deg,
    horizontal_status="hlcmf_v2_low_coherence",
    phase_reference_rad=None,
):
    measurement = SimpleNamespace(
        accepted=True,
        angle_deg=13.57,
        horizontal_deg=horizontal_deg,
        horizontal_confidence=0.89,
        horizontal_status=horizontal_status,
        n_snapshots=18,
        n_frames=5,
        component_std_deg=1.4,
        to_dict=lambda: {},
    )
    capture = SimpleNamespace(
        trigger_timestamp=100.01,
        path=None,
        raw=b"raw",
        dump_duration_s=4.5,
        error=None,
        valid=True,
        sequence=1,
    )
    runtime = SimpleNamespace(
        horizontal_phase_reference_rad=phase_reference_rad,
        process_shot=lambda **kwargs: SimpleNamespace(
            capture=capture, measurement=measurement, club_path=None
        ),
    )
    monkeypatch.setattr(server_module, "iwr6843_runtime", runtime)
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    shot = Shot(
        ball_speed_mph=45.1,
        club_speed_mph=40.0,
        timestamp=datetime.now(),
        impact_timestamp=100.0,
        club=ClubType.IRON_9,
    )
    server_module._process_iwr6843_angle(shot)  # pylint: disable=protected-access
    return shot


def test_the_kiosk_withholds_an_implausible_iwr_horizontal(monkeypatch):
    """Shot 14: never on the shot, so no flight, collision model or simulator reads it."""
    shot = _iwr_shot(monkeypatch, horizontal_deg=70.6261883479819)

    assert shot.launch_angle_vertical == pytest.approx(13.57)
    assert shot.launch_angle_horizontal is None
    assert shot.launch_angle_horizontal_source is None
    assert "+70.6" in shot.launch_angle_horizontal_reason
    assert "radar_low_coherence" in shot.launch_angle_horizontal_reason
    record = shot.to_dict()["metrics"]["launch_horizontal"]
    assert record["status"] == "not_measured"
    assert record["reason"] == shot.launch_angle_horizontal_reason


def test_the_kiosk_shows_a_plausible_low_coherence_horizontal_as_experimental(monkeypatch):
    shot = _iwr_shot(monkeypatch, horizontal_deg=3.2)

    assert shot.launch_angle_horizontal == pytest.approx(3.2)
    assert shot.launch_angle_horizontal_reason is None
    assert shot.to_dict()["metrics"]["launch_horizontal"]["status"] == "experimental"


def test_a_camera_horizontal_replaces_a_withheld_one(monkeypatch):
    shot = _iwr_shot(monkeypatch, horizontal_deg=70.6)

    server_module._apply_camera_horizontal_decision(  # pylint: disable=protected-access
        shot, 4.0, 0.7, "camera_only_experimental"
    )

    assert shot.launch_angle_horizontal == pytest.approx(4.0)
    assert shot.launch_angle_horizontal_reason is None


def test_an_implausible_camera_horizontal_never_replaces_a_shown_one(monkeypatch):
    shot = _iwr_shot(monkeypatch, horizontal_deg=3.2)

    server_module._apply_camera_horizontal_decision(  # pylint: disable=protected-access
        shot, 80.0, 0.9, "camera_assisted_experimental"
    )

    assert shot.launch_angle_horizontal == pytest.approx(3.2)
    assert shot.launch_angle_horizontal_source == "radar_low_coherence"


# ---------------------------------------------------------------- every reader agrees


@pytest.mark.parametrize("source", sorted(WEAK_HORIZONTAL_SOURCES))
def test_the_collision_model_takes_a_weak_horizontal_estimated_flagged_experimental(source):
    shot = Shot(
        ball_speed_mph=120.0,
        timestamp=datetime(2026, 10, 24),
        launch_angle_horizontal=1.0,
        launch_angle_horizontal_source=source,
        launch_angle_horizontal_confidence=0.9,
    )

    entry = shot_models._horizontal_launch_input(shot, source)  # pylint: disable=protected-access

    # the model has no experimental label: estimated with the reason, flagged
    assert entry.label == "estimated"
    assert entry.experimental is True
    assert source in entry.reason


@pytest.mark.parametrize("source", sorted(WEAK_HORIZONTAL_SOURCES))
def test_a_simulator_never_gets_a_weak_horizontal(source):
    shot = Shot(
        ball_speed_mph=140.0,
        timestamp=datetime(2026, 10, 24),
        club=ClubType.DRIVER,
        launch_angle_vertical=12.0,
        launch_angle_horizontal=1.5,
        launch_angle_horizontal_source=source,
        spin_rpm=2500.0,
        spin_confidence=0.9,
        spin_axis_deg=-3.0,
        spin_axis_source="calculated (D-plane tilt)",
    )

    with pytest.raises(UnmeasuredShotError) as refused:
        resolve_shot(shot, PlayerState())

    assert "horizontal launch (experimental)" in refused.value.missing


# ---------------------------------------------------------------- the uncalibrated azimuth

HORIZONTAL_SOURCES = sorted(
    {"estimated", *WEAK_HORIZONTAL_SOURCES, *full_speed.MEASURED_HORIZONTAL_SOURCES}
)


@pytest.mark.parametrize("source", HORIZONTAL_SOURCES)
def test_an_uncalibrated_azimuth_is_at_best_estimated_whatever_the_source(source):
    expected = "experimental" if source in WEAK_HORIZONTAL_SOURCES else "estimated"
    assert horizontal_launch_label(source, AZIMUTH_UNCALIBRATED) == expected


def test_a_calibrated_radar_horizontal_stays_measured():
    assert horizontal_launch_label("radar", None) == "measured"
    assert horizontal_launch_label("radar") == "measured"


@pytest.mark.parametrize("status", [None, AZIMUTH_UNCALIBRATED])
@pytest.mark.parametrize("source", HORIZONTAL_SOURCES)
def test_the_full_ball_speed_takes_every_horizontal_the_policy_labels(source, status):
    """One rule (fix 5): the full ball speed takes exactly the horizontals the label
    policy labels (measured, estimated, experimental) and inherits the label; a table
    estimate is not a launch angle."""
    value, reason = full_speed._horizontal_boresight(  # pylint: disable=protected-access
        2.0, source, status, "unit_boresight", None
    )

    if source == "estimated":
        assert value is None
        assert reason
    else:
        assert (value, reason) == (2.0, None)


UNCALIBRATED_RADAR = {
    "launch_angle_horizontal": 2.0,
    "launch_angle_horizontal_confidence": 0.9,
    "launch_angle_horizontal_source": "radar",
    "launch_angle_horizontal_status": AZIMUTH_UNCALIBRATED,
    "launch_angle_horizontal_frame": "target_line",
}


def test_an_uncalibrated_radar_horizontal_record_is_estimated():
    record = shot_metrics.from_record(UNCALIBRATED_RADAR)["launch_horizontal"]

    assert record["value"] == pytest.approx(2.0)
    assert record["status"] == "estimated"
    assert record["source"] == "radar"
    assert "azimuth uncalibrated" in record["note"]


def _uncalibrated_shot(**extra):
    return Shot(
        ball_speed_mph=140.0,
        timestamp=datetime(2026, 10, 24),
        club=ClubType.DRIVER,
        launch_angle_vertical=12.0,
        launch_angle_vertical_source="radar",
        inclinometer={"applied_to": ["camera", "iwr6843"]},
        spin_rpm=2500.0,
        spin_confidence=0.9,
        spin_axis_deg=-3.0,
        spin_axis_source="calculated (D-plane tilt)",
        **{**UNCALIBRATED_RADAR, **extra},
    )


def test_the_collision_model_takes_an_uncalibrated_horizontal_as_estimated():
    shot = _uncalibrated_shot()

    entry = shot_models._horizontal_launch_input(  # pylint: disable=protected-access
        shot, "radar (azimuth uncalibrated)"
    )

    assert entry.label == "estimated"
    assert "azimuth uncalibrated" in entry.reason


def test_the_collision_model_takes_a_calibrated_radar_horizontal_as_measured():
    shot = _uncalibrated_shot(launch_angle_horizontal_status=None)

    entry = shot_models._horizontal_launch_input(shot, "radar")  # pylint: disable=protected-access

    assert entry.label == "measured"


def test_a_simulator_is_told_an_uncalibrated_horizontal_is_estimated(monkeypatch):
    from openflight.sim import resolver

    monkeypatch.setattr(resolver, "SIMULATORS_ACCEPT_ESTIMATED", True)
    resolved = resolve_shot(_uncalibrated_shot(), PlayerState())

    assert resolved.hla == pytest.approx(2.0)
    assert resolved.provenance["hla"] == "estimated"


def test_a_simulator_is_told_a_calibrated_radar_horizontal_is_measured():
    resolved = resolve_shot(_uncalibrated_shot(launch_angle_horizontal_status=None), PlayerState())

    assert resolved.provenance["hla"] == "measured"


def test_the_kiosk_shows_an_uncalibrated_iwr_horizontal_as_estimated(monkeypatch):
    shot = _iwr_shot(monkeypatch, horizontal_deg=3.2, horizontal_status="hlcmf_v2_accepted")

    assert shot.launch_angle_horizontal_source == "radar"
    assert shot.launch_angle_horizontal_status == AZIMUTH_UNCALIBRATED
    record = shot.to_dict()["metrics"]["launch_horizontal"]
    assert record["status"] == "estimated"
    assert "azimuth uncalibrated" in record["note"]


def test_the_kiosk_shows_a_calibrated_iwr_horizontal_as_measured(monkeypatch):
    shot = _iwr_shot(
        monkeypatch,
        horizontal_deg=3.2,
        horizontal_status="hlcmf_v2_accepted",
        phase_reference_rad=0.4,
    )

    assert shot.launch_angle_horizontal_source == "radar"
    assert shot.launch_angle_horizontal_status is None
    assert shot.to_dict()["metrics"]["launch_horizontal"]["status"] == "measured"
