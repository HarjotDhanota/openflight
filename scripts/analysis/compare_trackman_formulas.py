"""Offline reference checks and controlled current-spin/TrackMan-input comparison.

See docs/research/trackman-metrics/README.md. No TrackMan DLL is loaded and no
production defaults are changed. TrackMan reference spin is a measurement,
NOT a recovered ball-speed/launch-angle spin-estimation equation.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))

from validate_ballistics import (  # noqa: E402
    TM_FLAT_AIR_DENSITY,
    _has_required_inputs,
    _stats,
    load_trackman,
)

from openflight.ballistics import LaunchConditions, simulate  # noqa: E402
from openflight.spin_estimate import calculated_spin_rpm  # noqa: E402

_FIXTURES = _ROOT / "docs/research/trackman-metrics/formula-fixtures.json"


def _unit(vector):
    length = math.sqrt(sum(x * x for x in vector))
    if length == 0:
        raise ValueError("Cannot normalize a zero vector")
    return tuple(x / length for x in vector)


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def smash_factor(ball_speed, club_speed):
    """Speeds must use the same units; valid positive club speed only."""
    if club_speed <= 0:
        raise ValueError("Club speed must be positive")
    return ball_speed / club_speed


def spin_loft_deg(club_vector_tm, dynamic_loft_deg, face_angle_deg):
    """Recovered legacy face construction; TM X forward, Y up, Z lateral.

    API here accepts/returns degrees; recovered LaunchData IL uses radians.
    Clamping roundoff before acos is our defensive addition, not recovered IL.
    """
    loft = math.tan(math.radians(dynamic_loft_deg))
    face = math.tan(math.radians(face_angle_deg))
    normal = _unit((1, loft * (1 + face * face), face))
    return math.degrees(math.acos(max(-1, min(1, _dot(_unit(club_vector_tm), normal)))))


def curve(radial_carry, carry_side, launch_direction_deg):
    """Distance units preserved; Carry is horizontal radial distance, not X."""
    if radial_carry <= 0 or abs(carry_side) > radial_carry:
        raise ValueError("Positive radial carry >= abs(carry_side) required")
    return radial_carry * math.sin(
        math.asin(carry_side / radial_carry) - math.radians(launch_direction_deg)
    )


def polynomial(coefficients_ascending, time_s):
    """Horner evaluation; time base must match the source fit's origin."""
    result = 0.0
    for coefficient in reversed(coefficients_ascending):
        result = result * time_s + coefficient
    return result


def spin_vector_metrics(vector_tm, launch_deg, direction_deg):
    """Probe-matching reconstruction: (rate, axis degrees, gyro degrees).

    Preserve the vector's angular-rate units. Native wrapper rates are strongly
    supported as RPM at the normalization boundary; see saved evidence.
    """
    a, d = math.radians(launch_deg), math.radians(direction_deg)
    forward = (math.cos(a) * math.cos(d), math.sin(a), math.cos(a) * math.sin(d))
    up = (-math.sin(a) * math.cos(d), math.cos(a), -math.sin(a) * math.sin(d))
    lateral = (-math.sin(d), 0, math.cos(d))
    rate = math.sqrt(_dot(vector_tm, vector_tm))
    if rate == 0:
        return (0.0, 0.0, 0.0)
    axis = math.degrees(-math.atan2(_dot(vector_tm, up), _dot(vector_tm, lateral)))
    gyro = math.degrees(math.asin(max(-1, min(1, _dot(vector_tm, forward) / rate))))
    return (rate, axis, gyro)


def check_fixtures(path=_FIXTURES):
    functions = {
        "smash_factor": smash_factor,
        "spin_loft_deg": spin_loft_deg,
        "curve": curve,
        "polynomial": polynomial,
        "spin_vector_metrics": spin_vector_metrics,
    }
    fixtures = json.loads(path.read_text(encoding="utf-8"))["fixtures"]
    for index, fixture in enumerate(fixtures, 1):
        actual = functions[fixture["formula"]](*fixture["args"])
        expected = fixture["expected"]
        if not isinstance(expected, list):
            actual, expected = [actual], [expected]
        if len(actual) != len(expected) or not all(
            math.isclose(a, e, rel_tol=1e-10, abs_tol=1e-9) for a, e in zip(actual, expected)
        ):
            raise ValueError(f"Fixture {index} ({fixture['formula']}): {actual} != {expected}")
    return len(fixtures)


def compare(path, air_density):
    rows = []
    for shot in load_trackman(path):
        if not _has_required_inputs(
            shot.ball_speed_mph, shot.launch_v_deg, shot.spin_rpm, shot.carry_yards
        ):
            continue
        calculated = calculated_spin_rpm(shot.ball_speed_mph, shot.launch_v_deg)
        if calculated is None:
            continue
        carries = {}
        for name, rpm in (("tm_reference_spin", shot.spin_rpm), ("of_calculated_spin", calculated)):
            launch = LaunchConditions(
                shot.ball_speed_mph,
                shot.launch_v_deg,
                shot.launch_h_deg or 0,
                rpm,
                shot.spin_axis_deg or 0,
                "measured",
            )
            # Force the scalar for this experiment; bypass confidence/fallback.
            # 'measured' is required by the current API; report provenance below.
            carries[name] = simulate(launch, air_density=air_density).carry_yards
        rows.append(
            {
                "shot_index": shot.shot_index,
                "club": shot.club_raw,
                "tm_spin_rpm": shot.spin_rpm,
                "of_calculated_spin_rpm": calculated,
                "spin_error_rpm": calculated - shot.spin_rpm,
                "tm_carry_yards": shot.carry_yards,
                **carries,
            }
        )
    if not rows:
        raise ValueError("No matched shots with valid calculated-spin inputs")
    return {
        "input": str(path.resolve()),
        "air_density_kg_m3": air_density,
        "provenance": "Both variants use TM speed, launch and axis, and current OF aero. OF spin variant is optional calculated-spin, not default production mode. No recovered TM spin estimator or TM aero equations.",
        "matched_shots": len(rows),
        "of_calculated_spin_error_rpm": _stats([r["spin_error_rpm"] for r in rows]),
        "carry_error_yards": {
            name: _stats([r[name] - r["tm_carry_yards"] for r in rows])
            for name in ("tm_reference_spin", "of_calculated_spin")
        },
        "shots": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument(
        "--trackman", type=Path, default=_ROOT / "session_logs/OpenFlight-Test.Normalized.csv"
    )
    parser.add_argument("--air-density", type=float, default=TM_FLAT_AIR_DENSITY)
    parser.add_argument("--output", type=Path, help="Optional JSON report path")
    args = parser.parse_args()
    count = check_fixtures()
    print(f"Verified {count} reference fixtures")
    if args.check_only:
        return 0
    if not math.isfinite(args.air_density) or args.air_density <= 0:
        parser.error("--air-density must be finite and positive")
    result = compare(args.trackman, args.air_density)
    result["verified_reference_fixtures"] = count
    print(json.dumps({k: v for k, v in result.items() if k != "shots"}, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
