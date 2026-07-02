"""D-plane forward model and inversion routes for the Stage 0D budget.

Public angles are degrees. Trig helpers convert at the call boundary.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ClubDPlaneParams:
    club: str
    c_min: float
    c_max: float
    k_face_deg_per_mm: float
    k_loft_deg_per_mm: float
    g_axis_deg_per_mm: float
    static_loft_deg: float
    spin_rate_k: float
    strike_u_sigma_mm: float = 8.0
    strike_w_sigma_mm: float = 6.0

    @property
    def c_mid(self) -> float:
        return 0.5 * (self.c_min + self.c_max)


@dataclass(frozen=True)
class DPlaneTruth:
    club: str
    face_deg: float
    path_deg: float
    attack_deg: float
    dynamic_loft_deg: float
    speed_mps: float
    impact_u_mm: float
    impact_w_mm: float
    c_h: float
    c_v: float
    launch_h_deg: float
    launch_v_deg: float
    spin_loft_deg: float
    spin_rpm: float
    spin_axis_deg: float


@dataclass(frozen=True)
class DPlaneMeasurement:
    launch_h_deg: float
    launch_v_deg: float
    path_deg: float
    attack_deg: float
    spin_axis_deg: float
    impact_u_mm: float | None = None
    impact_w_mm: float | None = None


@dataclass(frozen=True)
class LaunchRouteEstimate:
    face_deg: float
    dynamic_loft_deg: float


@dataclass(frozen=True)
class AxisRouteEstimate:
    face_deg: float


def club_params(club: str) -> ClubDPlaneParams:
    if club == "driver":
        return ClubDPlaneParams(
            club="driver",
            c_min=0.76,
            c_max=0.87,
            k_face_deg_per_mm=0.2,
            k_loft_deg_per_mm=0.15,
            g_axis_deg_per_mm=1.57,
            static_loft_deg=10.5,
            spin_rate_k=8_500.0,
        )
    if club in {"iron", "7iron", "7-iron"}:
        return ClubDPlaneParams(
            club="iron",
            c_min=0.61,
            c_max=0.76,
            k_face_deg_per_mm=0.05,
            k_loft_deg_per_mm=0.0,
            g_axis_deg_per_mm=0.3,
            static_loft_deg=34.0,
            spin_rate_k=14_000.0,
        )
    raise ValueError(f"unknown club {club!r}")


def _cosd(angle_deg) -> np.ndarray:
    return np.cos(np.radians(angle_deg))


def _sind(angle_deg) -> np.ndarray:
    return np.sin(np.radians(angle_deg))


def _tand(angle_deg) -> np.ndarray:
    return np.tan(np.radians(angle_deg))


def _atan2d(y, x) -> np.ndarray:
    return np.degrees(np.arctan2(y, x))


def spin_loft_deg(face_deg, path_deg, attack_deg, dynamic_loft_deg):
    ftp_component = (np.asarray(face_deg) - np.asarray(path_deg)) * _cosd(dynamic_loft_deg)
    vertical = np.asarray(dynamic_loft_deg) - np.asarray(attack_deg)
    return np.sqrt(vertical * vertical + ftp_component * ftp_component)


def forward_model(
    club: str,
    face_deg,
    path_deg,
    attack_deg,
    dynamic_loft_deg,
    speed_mps,
    impact_u_mm,
    impact_w_mm,
    c_h,
    c_v,
) -> DPlaneTruth:
    params = club_params(club)
    face_eff = np.asarray(face_deg) + params.k_face_deg_per_mm * np.asarray(impact_u_mm)
    loft_eff = np.asarray(dynamic_loft_deg) + params.k_loft_deg_per_mm * np.asarray(impact_w_mm)
    launch_h = np.asarray(c_h) * face_eff + (1.0 - np.asarray(c_h)) * np.asarray(path_deg)
    launch_v = np.asarray(c_v) * loft_eff + (1.0 - np.asarray(c_v)) * np.asarray(attack_deg)
    sl = spin_loft_deg(face_deg, path_deg, attack_deg, dynamic_loft_deg)
    spin_rpm = params.spin_rate_k * np.asarray(speed_mps) * _sind(sl)
    theta_gear = -params.g_axis_deg_per_mm * np.asarray(impact_u_mm)
    spin_axis = _atan2d(
        (np.asarray(face_deg) - np.asarray(path_deg)) * _cosd(dynamic_loft_deg),
        np.asarray(dynamic_loft_deg) - np.asarray(attack_deg),
    ) + theta_gear
    return DPlaneTruth(
        club=params.club,
        face_deg=float(np.asarray(face_deg)),
        path_deg=float(np.asarray(path_deg)),
        attack_deg=float(np.asarray(attack_deg)),
        dynamic_loft_deg=float(np.asarray(dynamic_loft_deg)),
        speed_mps=float(np.asarray(speed_mps)),
        impact_u_mm=float(np.asarray(impact_u_mm)),
        impact_w_mm=float(np.asarray(impact_w_mm)),
        c_h=float(np.asarray(c_h)),
        c_v=float(np.asarray(c_v)),
        launch_h_deg=float(np.asarray(launch_h)),
        launch_v_deg=float(np.asarray(launch_v)),
        spin_loft_deg=float(np.asarray(sl)),
        spin_rpm=float(np.asarray(spin_rpm)),
        spin_axis_deg=float(np.asarray(spin_axis)),
    )


def _impact_pair_for_mode(truth: DPlaneTruth, sigma_impact: float, gear_mode: str, rng):
    if gear_mode == "none":
        return None, None
    if gear_mode == "perfect":
        return truth.impact_u_mm, truth.impact_w_mm
    if gear_mode == "camera":
        if sigma_impact > 0:
            return (
                truth.impact_u_mm + float(rng.normal(0.0, sigma_impact)),
                truth.impact_w_mm + float(rng.normal(0.0, sigma_impact)),
            )
        return truth.impact_u_mm, truth.impact_w_mm
    raise ValueError(f"unknown gear mode {gear_mode!r}")


def measure_shot(
    truth: DPlaneTruth,
    sigma_launch: float,
    sigma_path: float,
    sigma_axis: float,
    sigma_impact: float,
    b_frame: float,
    gear_mode: str,
    rng,
) -> DPlaneMeasurement:
    u_meas, w_meas = _impact_pair_for_mode(truth, sigma_impact, gear_mode, rng)
    return DPlaneMeasurement(
        launch_h_deg=truth.launch_h_deg + b_frame + float(rng.normal(0.0, sigma_launch)),
        launch_v_deg=truth.launch_v_deg + float(rng.normal(0.0, sigma_launch)),
        path_deg=truth.path_deg + b_frame + float(rng.normal(0.0, sigma_path)),
        attack_deg=truth.attack_deg + float(rng.normal(0.0, sigma_path)),
        spin_axis_deg=truth.spin_axis_deg + float(rng.normal(0.0, sigma_axis)),
        impact_u_mm=u_meas,
        impact_w_mm=w_meas,
    )


def _gear_u(measurement: DPlaneMeasurement, gear_mode: str) -> float:
    return 0.0 if gear_mode == "none" else float(measurement.impact_u_mm or 0.0)


def _gear_w(measurement: DPlaneMeasurement, gear_mode: str) -> float:
    return 0.0 if gear_mode == "none" else float(measurement.impact_w_mm or 0.0)


def invert_launch_route(
    club: str,
    measurement: DPlaneMeasurement,
    c_h_hat: float | None = None,
    c_v_hat: float | None = None,
    gear_mode: str = "none",
) -> LaunchRouteEstimate:
    params = club_params(club)
    c_h = params.c_mid if c_h_hat is None else float(c_h_hat)
    c_v = params.c_mid if c_v_hat is None else float(c_v_hat)
    face = (measurement.launch_h_deg - (1.0 - c_h) * measurement.path_deg) / c_h
    loft = (measurement.launch_v_deg - (1.0 - c_v) * measurement.attack_deg) / c_v
    face -= params.k_face_deg_per_mm * _gear_u(measurement, gear_mode)
    loft -= params.k_loft_deg_per_mm * _gear_w(measurement, gear_mode)
    return LaunchRouteEstimate(face_deg=float(face), dynamic_loft_deg=float(loft))


def invert_axis_route(
    club: str,
    measurement: DPlaneMeasurement,
    dynamic_loft_deg: float,
    gear_mode: str = "none",
) -> AxisRouteEstimate:
    params = club_params(club)
    theta_corr = measurement.spin_axis_deg + params.g_axis_deg_per_mm * _gear_u(measurement, gear_mode)
    spin_loft_vertical = dynamic_loft_deg - measurement.attack_deg
    ftp = _tand(theta_corr) * spin_loft_vertical / max(float(_cosd(dynamic_loft_deg)), 1e-9)
    return AxisRouteEstimate(face_deg=float(measurement.path_deg + ftp))


def fused_face_deg(
    launch_face_deg: float,
    axis_face_deg: float,
    club: str,
    sigma_launch: float,
    sigma_path: float,
    sigma_axis: float,
    sigma_gear_resid: float,
    spin_loft_ref_deg: float,
) -> float:
    params = club_params(club)
    c = params.c_mid
    var_launch = (sigma_launch * sigma_launch + (1.0 - c) ** 2 * sigma_path * sigma_path) / (c * c)
    var_axis = (
        sigma_path * sigma_path
        + (_sind(spin_loft_ref_deg) * sigma_axis) ** 2
        + (params.g_axis_deg_per_mm * sigma_gear_resid * _sind(spin_loft_ref_deg)) ** 2
    )
    var_launch = max(float(var_launch), 1e-12)
    var_axis = max(float(var_axis), 1e-12)
    w_launch = 1.0 / var_launch
    w_axis = 1.0 / var_axis
    return float((w_launch * launch_face_deg + w_axis * axis_face_deg) / (w_launch + w_axis))
