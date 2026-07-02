"""Early-flight marked-ball truth model."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial.transform import Rotation

GRAVITY_MM_S2 = np.array([0.0, 0.0, -9_810.0])


@dataclass(frozen=True)
class Launch:
    speed_mps: float
    vla_deg: float
    hla_deg: float
    p0_mm: tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass(frozen=True)
class Spin:
    rate_rpm: float
    axis_tilt_deg: float


@dataclass(frozen=True)
class BallState:
    t_s: float
    center_world: np.ndarray
    orientation: Rotation
    spin_axis_world: np.ndarray
    rate_rpm: float
    axis_tilt_deg: float
    launch_vector_mm_s: np.ndarray


def launch_vector(speed_mps: float, vla_deg: float, hla_deg: float) -> np.ndarray:
    speed = speed_mps * 1000.0
    vla = np.deg2rad(vla_deg)
    hla = np.deg2rad(hla_deg)
    return speed * np.array(
        [
            np.cos(vla) * np.cos(hla),
            np.cos(vla) * np.sin(hla),
            np.sin(vla),
        ]
    )


def _unit(vector: np.ndarray) -> np.ndarray:
    vector = np.asarray(vector, dtype=float)
    norm = np.linalg.norm(vector)
    if norm <= 0:
        raise ValueError("zero-length vector")
    return vector / norm


def spin_axis_world(launch: Launch, spin: Spin) -> np.ndarray:
    flight = _unit(launch_vector(launch.speed_mps, launch.vla_deg, launch.hla_deg))
    up = np.array([0.0, 0.0, 1.0])
    side = np.cross(up, flight)
    if np.linalg.norm(side) < 1e-9:
        side = np.array([0.0, 1.0, 0.0])
    side = _unit(side)
    backspin_axis = _unit(np.cross(flight, side))
    return Rotation.from_rotvec(np.deg2rad(spin.axis_tilt_deg) * flight).apply(backspin_axis)


def signed_tilt_deg(axis_world: np.ndarray, launch_vector: np.ndarray) -> float:
    flight = _unit(launch_vector)
    up = np.array([0.0, 0.0, 1.0])
    side = np.cross(up, flight)
    if np.linalg.norm(side) < 1e-9:
        side = np.array([0.0, 1.0, 0.0])
    side = _unit(side)
    backspin_axis = _unit(np.cross(flight, side))
    axis = _unit(axis_world - np.dot(axis_world, flight) * flight)
    signed = np.arctan2(np.dot(np.cross(backspin_axis, axis), flight), np.dot(backspin_axis, axis))
    return float(np.rad2deg(signed))


def ball_states(
    launch: Launch,
    spin: Spin,
    frame_times: list[float] | np.ndarray,
    r0: Rotation | None = None,
) -> list[BallState]:
    velocity = launch_vector(launch.speed_mps, launch.vla_deg, launch.hla_deg)
    p0 = np.asarray(launch.p0_mm, dtype=float)
    axis = spin_axis_world(launch, spin)
    initial = Rotation.identity() if r0 is None else r0
    omega_rad_s = spin.rate_rpm * 2.0 * np.pi / 60.0

    states: list[BallState] = []
    for t_s in np.asarray(frame_times, dtype=float):
        center = p0 + velocity * t_s + 0.5 * GRAVITY_MM_S2 * t_s * t_s
        orientation = Rotation.from_rotvec(axis * omega_rad_s * t_s) * initial
        states.append(
            BallState(
                t_s=float(t_s),
                center_world=center,
                orientation=orientation,
                spin_axis_world=axis,
                rate_rpm=spin.rate_rpm,
                axis_tilt_deg=spin.axis_tilt_deg,
                launch_vector_mm_s=velocity,
            )
        )
    return states
