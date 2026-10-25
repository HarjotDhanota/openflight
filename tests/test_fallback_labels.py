"""Every value carries its weakest input's label, whichever sensor stood in.

A camera-fallback launch (or any camera value on an inferred geometry) is
``experimental``; every model output built on it is too, and a range from the
ball's own size makes every range-dependent value experimental. Sensor readings
that do not rest on them keep their own label.
"""

import pytest

from openflight import shot_metrics, source_labels
from openflight.shot_metrics import from_record


def record(status, value=1.0, note=None, source=None):
    return {"status": status, "value": value, "note": note, "source": source, "reason": None}


@pytest.mark.parametrize(
    ("source", "status", "label"),
    [
        ("radar", None, "measured"),
        ("camera", None, "measured"),
        ("estimated", None, "estimated"),
        # Q3: a value that failed its own gate is experimental
        ("radar_low_coherence", None, "experimental"),
        ("camera_low_consensus", None, "experimental"),
        ("radar", "azimuth_uncalibrated", "estimated"),
        ("camera_fallback", None, "experimental"),
        ("camera_assisted_experimental", None, "experimental"),
        ("camera_only_experimental", None, "experimental"),
        ("camera_legacy_fallback", None, "experimental"),
    ],
)
def test_each_launch_source_has_one_label(source, status, label):
    assert source_labels.launch_label(source, status) == label


def test_the_horizontal_label_is_the_launch_label():
    assert source_labels.horizontal_launch_label("camera_fallback") == "experimental"
    assert source_labels.horizontal_launch_label("radar_low_coherence") == "experimental"


def test_a_camera_fallback_vertical_is_experimental_and_says_why():
    metrics = from_record(
        {"launch_angle_vertical": 14.2, "launch_angle_vertical_source": "camera_fallback"}
    )

    vertical = metrics["launch_vertical"]
    assert vertical["status"] == "experimental"
    assert vertical["value"] == 14.2
    assert "not validated against TrackMan" in vertical["note"]


def test_a_camera_horizontal_on_an_inferred_geometry_is_never_measured():
    metrics = from_record(
        {
            "launch_angle_horizontal": 2.0,
            "launch_angle_horizontal_source": "camera_assisted_experimental",
        }
    )

    assert metrics["launch_horizontal"]["status"] == "experimental"


def test_every_model_output_on_a_fallback_launch_is_experimental():
    metrics = {
        "launch_vertical": record("experimental"),
        "launch_horizontal": record("experimental"),
        "club_path": record("measured"),
        "attack_angle": record("measured"),
        "ball_speed": record("measured"),
        "club_speed": record("measured"),
        "ball_speed_full": record("estimated"),
        "face_angle": record("calculated"),
        "dynamic_loft": record("estimated", note="launch weak"),
        "spin_rate": record("calculated"),
        "spin_axis": record("calculated"),
        "carry": record("calculated"),
        "curve": record("calculated"),
        "smash_factor": record("calculated"),
        "swing_plane": record("calculated"),
        "low_point_distance": record("model_geometry"),
    }

    shot_metrics.with_input_labels(metrics, {"spin_source": "calculated"})

    for name in (
        "ball_speed_full",
        "face_angle",
        "dynamic_loft",
        "spin_rate",
        "spin_axis",
        "carry",
        "curve",
    ):
        assert metrics[name]["status"] == "experimental", name
        assert "experimental" in metrics[name]["note"], name
    assert "launch weak" in metrics["dynamic_loft"]["note"]
    # sensor readings and club-only geometry are not built on the ball's launch
    for name, status in (
        ("ball_speed", "measured"),
        ("club_speed", "measured"),
        ("club_path", "measured"),
        ("smash_factor", "calculated"),
        ("swing_plane", "calculated"),
        ("low_point_distance", "model_geometry"),
    ):
        assert metrics[name]["status"] == status, name


def test_an_ops_spin_of_experimental_quality_lowers_nothing_it_does_not_feed():
    metrics = {
        "launch_vertical": record("measured"),
        "launch_horizontal": record("measured"),
        "spin_rate": record("experimental"),
        "spin_axis": record("calculated"),
        "carry": record("calculated"),
    }

    shot_metrics.with_input_labels(metrics, {"spin_source": None, "spin_quality": "experimental"})

    assert metrics["carry"]["status"] == "calculated"
    assert metrics["spin_rate"]["status"] == "experimental"


def test_a_camera_club_path_lowers_the_d_plane_outputs_but_not_the_flight_s_speed():
    metrics = {
        "launch_vertical": record("measured"),
        "launch_horizontal": record("measured"),
        "club_path": record("experimental"),
        "attack_angle": record("experimental"),
        "face_angle": record("calculated"),
        "spin_axis": record("calculated"),
        "carry": record("calculated"),
        "ball_speed_full": record("calculated"),
    }

    shot_metrics.with_input_labels(metrics, {"spin_source": None})

    assert metrics["face_angle"]["status"] == "experimental"
    assert metrics["spin_axis"]["status"] == "experimental"
    # the flight flies the axis: it is as weak as the axis
    assert metrics["carry"]["status"] == "experimental"
    assert metrics["ball_speed_full"]["status"] == "calculated"


def test_a_range_from_the_ball_s_size_makes_every_range_dependent_value_experimental():
    metrics = {
        "ball_range": record("experimental"),
        "ball_speed": record("measured"),
        "club_speed": record("measured"),
        "smash_factor": record("calculated"),
        "launch_vertical": record("measured"),
        "club_path": record("measured"),
        "spin_rate": record("calculated"),
        "carry": record("calculated"),
    }

    shot_metrics.with_input_labels(
        metrics, {"ball_range_source": "camera_ball_size", "spin_source": "calculated"}
    )

    for name in ("launch_vertical", "club_path", "spin_rate", "carry"):
        assert metrics[name]["status"] == "experimental", name
    for name in ("ball_speed", "club_speed", "smash_factor"):
        assert metrics[name]["status"] in ("measured", "calculated"), name


def test_a_model_spin_on_the_camera_prior_is_estimated_not_calculated():
    # audit: spin_rate was range-independent even when the collision model built it
    metrics = {"spin_rate": record("calculated"), "ball_speed": record("measured")}

    shot_metrics.with_input_labels(
        metrics, {"ball_range_source": "camera_prior", "spin_source": "calculated"}
    )

    assert metrics["spin_rate"]["status"] == "estimated"
    assert metrics["ball_speed"]["status"] == "measured"


def test_an_ops_measured_spin_stays_measured_under_the_camera_prior():
    metrics = {"spin_rate": record("measured")}

    shot_metrics.with_input_labels(
        metrics, {"ball_range_source": "camera_prior", "spin_source": "measured"}
    )

    assert metrics["spin_rate"]["status"] == "measured"


def test_the_camera_prior_never_relabels_an_experimental_value():
    metrics = {"impact_offset": record("experimental")}

    shot_metrics.with_input_labels(metrics, {"ball_range_source": "camera_prior"})

    assert metrics["impact_offset"]["status"] == "experimental"


def test_the_ball_s_own_size_range_is_experimental_with_its_source():
    metrics = from_record(
        {
            "ball_range_m": 2.05,
            "ball_range_source": "camera_ball_size",
            "ball_range_uncertainty_m": 0.17,
        }
    )

    entry = metrics["ball_range"]
    assert entry["status"] == "experimental"
    assert entry["range"] == pytest.approx([1.88, 2.22])
    assert "size" in entry["source"]
    assert "not validated" in entry["note"]


def test_a_camera_club_delivery_is_never_measured():
    shot = {
        "club_path_deg": None,
        "collision_model": {
            "inputs": {
                "club_path": {"value": 3.0, "label": "measured", "source": "camera_fused_chained"},
                "attack_angle": {"value": -4.0, "label": "measured", "source": "camera_fused_ops"},
            }
        },
    }

    metrics = from_record(shot)

    assert metrics["club_path"]["status"] == "experimental"
    assert metrics["attack_angle"]["status"] == "experimental"


def test_a_fusion_shot_on_the_fallback_launch_resolves_every_model_metric_as_experimental():
    from tests.every_metric import fusion_shot  # noqa: PLC0415

    shot = fusion_shot(
        launch_angle_vertical_source="camera_fallback",
        launch_angle_horizontal_source="camera_fallback",
    )
    metrics = shot.to_dict()["metrics"]

    assert metrics["launch_vertical"]["status"] == "experimental"
    assert metrics["launch_horizontal"]["status"] == "experimental"
    for name in (
        "face_angle",
        "dynamic_loft",
        "spin_loft",
        "face_to_path",
        "d_plane_tilt",
        "spin_axis",
        "carry",
        "apex",
        "curve",
    ):
        assert metrics[name]["value"] is not None, name
        assert metrics[name]["status"] == "experimental", name
    # the collision model took the launch, labelled, with the reason
    inputs = shot.collision_model["inputs"]
    assert inputs["launch_vertical"]["label"] == "estimated"
    assert "camera fallback" in inputs["launch_vertical"]["reason"]
    # the sensors' own readings keep theirs
    assert metrics["ball_speed"]["status"] == "measured"
    assert metrics["club_path"]["status"] == "measured"
    assert metrics["spin_rate"]["status"] == "measured"
