"""Falsification tests 6, 7 and 11 on the frozen 21-shot table.

6 sweeps published D-plane coefficient envelopes and bootstraps within club.
7 compares the two saved LCMF elevation component models before averaging.
11 applies a necessary normal-impulse/Coulomb feasibility grid without using
the project's derived spin value.  Feasible grid points are not measurements.
"""

from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).parent))

from test_meshfit_depth_ab import EXCLUDE, SESSION  # noqa: E402

VERTICAL_COEFFICIENTS = (0.68, 0.72, 0.81, 0.86)
HORIZONTAL_COEFFICIENTS = (0.61, 0.63, 0.69, 0.76, 0.87)
BALL_MASS_KG = 0.04593


def value(row, key):
    raw = row.get(key)
    return None if raw in (None, "") else float(raw)


def unit_from_angles(horizontal_deg, vertical_deg):
    horizontal = math.radians(horizontal_deg)
    vertical = math.radians(vertical_deg)
    return np.asarray(
        [
            math.cos(vertical) * math.sin(horizontal),
            math.sin(vertical),
            math.cos(vertical) * math.cos(horizontal),
        ]
    )


def bootstrap_gap(seven, nine, seed=20260826, samples=20_000):
    rng = np.random.default_rng(seed)
    gaps = np.empty(samples)
    for index in range(samples):
        gaps[index] = (
            rng.choice(nine, len(nine), replace=True).mean()
            - rng.choice(seven, len(seven), replace=True).mean()
        )
    return [float(np.percentile(gaps, 2.5)), float(np.percentile(gaps, 97.5))]


def main() -> None:
    with open(SESSION / "shots.csv", newline="", encoding="utf-8") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if int(row["shot_number"]) not in EXCLUDE
        ]

    vertical = []
    for coefficient in VERTICAL_COEFFICIENTS:
        by_club = {}
        for club_name in ("7-iron", "9-iron"):
            inferred = []
            for row in rows:
                if row["club"] != club_name:
                    continue
                launch = value(row, "launch_angle_vertical")
                attack = value(row, "experimental_fused_attack_angle_deg")
                if launch is not None and attack is not None:
                    inferred.append(attack + (launch - attack) / coefficient)
            by_club[club_name] = np.asarray(inferred)
        gap = float(by_club["9-iron"].mean() - by_club["7-iron"].mean())
        vertical.append(
            {
                "coefficient": coefficient,
                "seven_mean_deg": float(by_club["7-iron"].mean()),
                "nine_mean_deg": float(by_club["9-iron"].mean()),
                "gap_deg": gap,
                "gap_bootstrap_95_deg": bootstrap_gap(
                    by_club["7-iron"], by_club["9-iron"], seed=int(coefficient * 10_000)
                ),
            }
        )

    horizontal = []
    face_by_shot = {}
    for coefficient in HORIZONTAL_COEFFICIENTS:
        inferred = []
        for row in rows:
            launch = value(row, "experimental_camera_horizontal_deg")
            path = value(row, "experimental_fused_club_path_deg")
            if launch is None or path is None:
                continue
            face = path + (launch - path) / coefficient
            inferred.append(face)
            face_by_shot.setdefault(int(row["shot_number"]), []).append(face)
        horizontal.append(
            {
                "coefficient": coefficient,
                "median_face_deg": float(np.median(inferred)),
                "range_deg": [float(np.min(inferred)), float(np.max(inferred))],
            }
        )
    sign_changes = [
        shot for shot, values in face_by_shot.items() if min(values) < 0.0 < max(values)
    ]

    components = []
    for name, column in (
        ("two8", "iwr_measurement_components_deg_channel_two8_deg"),
        ("four4_path_tdm", "iwr_measurement_components_deg_channel_four4_path_tdm_deg"),
    ):
        by_club = {
            club_name: np.asarray(
                [value(row, column) for row in rows if row["club"] == club_name]
            )
            for club_name in ("7-iron", "9-iron")
        }
        components.append(
            {
                "model": name,
                "seven_mean_deg": float(by_club["7-iron"].mean()),
                "nine_mean_deg": float(by_club["9-iron"].mean()),
                "gap_deg": float(by_club["9-iron"].mean() - by_club["7-iron"].mean()),
            }
        )
    first = np.asarray(
        [value(row, "iwr_measurement_components_deg_channel_two8_deg") for row in rows]
    )
    second = np.asarray(
        [
            value(row, "iwr_measurement_components_deg_channel_four4_path_tdm_deg")
            for row in rows
        ]
    )
    component_pair = {
        "mean_two8_minus_four4_deg": float(np.mean(first - second)),
        "sd_two8_minus_four4_deg": float(np.std(first - second, ddof=1)),
        "correlation": float(np.corrcoef(first, second)[0, 1]),
    }

    impulse = []
    masses = np.linspace(0.10, 0.50, 17)
    strike_offsets = np.asarray([0.0, 0.010, 0.020, 0.030])
    inertias = np.asarray([3e-4, 6e-4, 1e-3])
    restitutions = np.linspace(0.55, 0.90, 15)
    frictions = np.linspace(0.10, 0.60, 11)
    for row in rows:
        launch_h = value(row, "experimental_camera_horizontal_deg")
        launch_v = value(row, "launch_angle_vertical")
        path = value(row, "experimental_fused_club_path_deg")
        attack = value(row, "experimental_fused_attack_angle_deg")
        if None in (launch_h, launch_v, path, attack):
            continue
        ball_velocity = (
            float(row["ball_speed_mph"]) / 2.2369362920544
        ) * unit_from_angles(launch_h, launch_v)
        club_velocity = (
            float(row["club_speed_mph"]) / 2.2369362920544
        ) * unit_from_angles(path, attack)
        feasible = 0
        total = 0
        required_e = []
        for vertical_coefficient in VERTICAL_COEFFICIENTS:
            loft = attack + (launch_v - attack) / vertical_coefficient
            for horizontal_coefficient in HORIZONTAL_COEFFICIENTS:
                face = path + (launch_h - path) / horizontal_coefficient
                normal = unit_from_angles(face, loft)
                incoming_normal = float(np.dot(club_velocity, normal))
                outgoing_normal = float(np.dot(ball_velocity, normal))
                outgoing_tangent = float(
                    np.linalg.norm(ball_velocity - outgoing_normal * normal)
                )
                for centered_mass in masses:
                    for strike_offset in strike_offsets:
                        for inertia in inertias:
                            # Directional effective mass for an off-centre
                            # normal impulse: 1/M = 1/M0 + r^2/I.
                            effective_mass = 1.0 / (
                                1.0 / centered_mass + strike_offset**2 / inertia
                            )
                            e_required = (
                                outgoing_normal
                                * (1.0 + BALL_MASS_KG / effective_mass)
                                / incoming_normal
                                - 1.0
                            )
                            required_e.append(e_required)
                            for restitution in restitutions:
                                for friction in frictions:
                                    total += 1
                                    normal_ok = abs(e_required - restitution) <= 0.025
                                    friction_ok = (
                                        outgoing_tangent / outgoing_normal <= friction
                                    )
                                    feasible += int(normal_ok and friction_ok)
        impulse.append(
            {
                "shot": int(row["shot_number"]),
                "club": row["club"],
                "feasible_grid_points": feasible,
                "total_grid_points": total,
                "feasible_fraction": feasible / total,
                "required_restitution_range": [min(required_e), max(required_e)],
            }
        )

    result = {
        "test6_vertical": vertical,
        "test6_horizontal": horizontal,
        "test6_face_sign_changes": sign_changes,
        "test7_components": components,
        "test7_pairwise": component_pair,
        "test7_raw_phase_slope": "not logged in this capture export",
        "test11_impulse": impulse,
    }
    output = Path(__file__).with_name("test6_7_11_dplane_envelopes.json")
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("test 6 vertical coefficient sweep:")
    for record in vertical:
        print(
            f"  a={record['coefficient']:.2f}: gap={record['gap_deg']:.2f} deg, "
            f"bootstrap 95% {np.round(record['gap_bootstrap_95_deg'], 2).tolist()}"
        )
    print(
        f"  horizontal face sign changes: {len(sign_changes)}/{len(face_by_shot)} shots"
    )
    print("test 7 component gaps:")
    for record in components:
        print(f"  {record['model']}: {record['gap_deg']:.2f} deg")
    print(f"  pairwise: {component_pair}")
    rejected = [
        record["shot"] for record in impulse if record["feasible_grid_points"] == 0
    ]
    print(
        f"test 11: no feasible impulse grid point on {len(rejected)}/{len(impulse)} shots: {rejected}"
    )
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
