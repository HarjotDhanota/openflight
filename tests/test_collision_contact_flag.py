"""A flagged contact labels the collision spin experimental, with the mishit band's sigma
(C15a; research/rig-geometry-lock-2026-11-02/spin-fullspeed-check/REPORT.md).

The collision-model spin is good on clean strikes (RMSE 9.8 % on 53 clean irons, held out)
and unusable on mishits (smash-index band 0.85-0.92: mean error +59 %, RMSE 86 %). When the
shot carries a contact flag (smash index below 92 %, attack -2 deg or shallower, a strike
over 8 mm high, no low point) the spin and its axis are experimental, reason "contact
flagged: collision spin unvalidated on mishits", sigma 86 % of the value; on a clean strike
the label stays and the sigma is 10 %. The flight built on it takes the label.
"""

from __future__ import annotations

import pytest

from openflight import collision_model as cm, shot_metrics
from openflight.clubs import ClubType


def _inputs(**changes):
    values = {
        "club": ClubType.IRON_7,
        "club_path": cm.Input(2.0, "measured", "camera", frame="target_line"),
        "attack_angle": cm.Input(-4.5, "measured", "camera"),
        "launch_horizontal": cm.Input(1.5, "measured", "radar", frame="target_line"),
        "launch_vertical": cm.Input(17.0, "measured", "radar"),
        "club_speed_mph": cm.Input(84.0, "calculated", "full"),
        "ball_speed_mph": cm.Input(114.0, "calculated", "full"),
        "low_point_distance_mm": cm.Input(40.0, "calculated", "arc"),
    }
    values.update(changes)
    return values


def test_a_clean_strike_keeps_its_label_with_a_ten_percent_sigma():
    spin = cm.evaluate(**_inputs()).metrics["spin_rate"]
    assert spin.present and not spin.contact_flagged
    assert spin.status != "experimental"
    assert spin.sigma == pytest.approx(cm.CLEAN_SPIN_RMSE_FRACTION * spin.value)


@pytest.mark.parametrize(
    "changes,why",
    [
        ({"attack_angle": cm.Input(-1.0, "measured", "camera")}, "attack angle"),
        ({"ball_speed_mph": cm.Input(98.0, "calculated", "full")}, "smash index"),
    ],
)
def test_a_flagged_contact_widens_the_spin_to_the_mishit_band(changes, why):
    result = cm.evaluate(**_inputs(**changes))
    spin, axis = result.metrics["spin_rate"], result.metrics["spin_axis"]

    assert spin.contact_flagged and axis.contact_flagged
    assert cm.CONTACT_FLAG_REASON in spin.note and why in spin.note
    assert spin.sigma == pytest.approx(cm.MISHIT_SPIN_RMSE_FRACTION * spin.value)


def test_a_missing_low_point_is_a_note_not_a_mishit():
    """Only the strike's own evidence (attack, height, smash index) labels the spin; the
    head track rarely fixes the arc's low point (C11f), so its absence stays a note."""
    result = cm.evaluate(**_inputs(low_point_distance_mm=cm.Input(None, reason="arc not fixed")))
    spin = result.metrics["spin_rate"]
    assert "no low point" in spin.note and cm.CONTACT_FLAG_REASON not in spin.note
    assert not spin.contact_flagged
    assert spin.sigma == pytest.approx(cm.CLEAN_SPIN_RMSE_FRACTION * spin.value)


def test_oct27_shot1_takes_the_flag_path():
    """Shot 1's own collision inputs (9a7c18eb + C13): smash index 88.8 % flags it."""
    result = cm.evaluate(
        club=ClubType.IRON_7,
        club_path=cm.Input(10.77, "measured", "camera_fused_chained", frame="target_line"),
        attack_angle=cm.Input(-9.76, "measured", "camera_fused_chained"),
        launch_horizontal=cm.Input(8.08, "estimated", "camera_fallback", frame="target_line"),
        launch_vertical=cm.Input(16.91, "estimated", "camera_fallback"),
        club_speed_mph=cm.Input(74.35, "estimated", "full club speed"),
        ball_speed_mph=cm.Input(84.65, "estimated", "full ball speed"),
        low_point_distance_mm=cm.Input(None, reason="the arc's points do not fix it"),
    )
    spin = result.metrics["spin_rate"]
    assert result.metrics["smash_index"].value < cm.SMASH_INDEX_FLAG_PERCENT
    assert spin.contact_flagged
    assert spin.sigma == pytest.approx(0.86 * spin.value)


def test_the_kiosk_labels_a_flagged_spin_and_its_flight_experimental():
    flagged = cm.evaluate(**_inputs(attack_angle=cm.Input(-1.0, "measured", "camera")))
    spin = flagged.metrics["spin_rate"]
    shot = {
        "ball_speed_mph": 114.0,
        "spin_rpm": spin.value,
        "spin_source": spin.status,
        "spin_rpm_collision": spin.value,
        "collision_model": {"metrics": {name: m.to_dict() for name, m in flagged.metrics.items()}},
        "carry_spin_adjusted": 150.0,
        "carry_status": "estimated",
    }
    records = shot_metrics.from_record(shot)

    assert records["spin_rate"]["status"] == "experimental"
    assert cm.CONTACT_FLAG_REASON in records["spin_rate"]["note"]
    low, high = records["spin_rate"]["range"]
    assert high - low == pytest.approx(2 * 0.86 * spin.value, rel=1e-6)
    assert records["carry"]["status"] == "experimental"
