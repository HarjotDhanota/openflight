"""Delivered club angles in the normalized, right-handed 690CB frame.

The striking-face axes were measured from the largest coherent planar patch on
the face, not from the cavity-rim plane used by mesh normalization. In the
source cache the heel axis was approximately ``(0, +1, +0.06)`` and pointed at
the hosel. Loading mirrors local z, so the right-handed constants below carry
the opposite z sign. ``MESH_HOSEL_AXIS_LOCAL`` records the suspect CAD hosel for
diagnostics only; pose construction uses the image shaft and the virtual
``SHAFT_LOCAL`` tied to the catalogue lie.

Those axes are now RE-DERIVED from the mesh at import, by
`mesh.detect_striking_face`, whenever the local mesh cache is present -- the
patch it finds is the same one they were measured from. The hand-transcribed
constants stay as `REFERENCE_FACE_NORMAL_LOCAL` and `REFERENCE_HEEL_TOE_LOCAL`:
they are the regression reference the derived axes must agree with to within
`FACE_AXES_AGREEMENT_LIMIT_DEG`, and they are the fallback when the cache is
absent, since it is a license-pinned local artefact that is not committed.
`FACE_AXES_SOURCE` records which of the two is live.

Importing this module therefore loads and validates the mesh when the cache is
present, which costs a couple of seconds once per process. The load is itself
cached, so a process that goes on to fit poses pays nothing extra.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.optimize import brentq

from .fit import triad
from .mesh import TriangleMesh, default_mesh_asset_root, detect_striking_face, load_normalized_mesh

# Hand-transcribed from the measured striking-face patch. Kept frozen: they are
# what a re-derivation is checked against, not merely a default.
REFERENCE_FACE_NORMAL_LOCAL = np.array([-0.941, 0.021, 0.337], dtype=float)
REFERENCE_FACE_NORMAL_LOCAL /= np.linalg.norm(REFERENCE_FACE_NORMAL_LOCAL)

# Heel direction from the 80.6 mm striking-face patch; source was (0,+1,+0.06).
REFERENCE_HEEL_TOE_LOCAL = np.array([0.0, 1.0, -0.06], dtype=float)
REFERENCE_HEEL_TOE_LOCAL -= REFERENCE_FACE_NORMAL_LOCAL * float(
    REFERENCE_HEEL_TOE_LOCAL @ REFERENCE_FACE_NORMAL_LOCAL
)
REFERENCE_HEEL_TOE_LOCAL /= np.linalg.norm(REFERENCE_HEEL_TOE_LOCAL)

# Wider than any plausible transcription or detector difference, tight enough
# that a differently oriented mesh cannot pass.
FACE_AXES_AGREEMENT_LIMIT_DEG = 3.0


def _angle_deg(first: np.ndarray, second: np.ndarray) -> float:
    cosine = float(first @ second) / (np.linalg.norm(first) * np.linalg.norm(second))
    return math.degrees(math.acos(float(np.clip(cosine, -1.0, 1.0))))


def face_axes_from_mesh(
    mesh: TriangleMesh, *, verify: bool = True
) -> tuple[np.ndarray, np.ndarray]:
    """Striking-face normal and heel-toe axis, measured off the mesh itself.

    The normal is the largest coherent planar patch's own normal, and the
    heel-toe axis is that patch's in-plane long axis, orthogonalized against it.

    Args:
        mesh: A normalized, right-handed clubhead mesh.
        verify: Check both axes against the frozen reference constants and
            refuse to return axes that disagree by more than
            `FACE_AXES_AGREEMENT_LIMIT_DEG`. A mesh that fails is a DIFFERENT
            club or a differently oriented frame, and silently adopting its
            axes would move every delivered angle without saying so.

    Raises:
        RuntimeError: If ``verify`` and either axis disagrees with its
            reference.
        ValueError: If the mesh has no clubface-sized planar patch.
    """
    face = detect_striking_face(mesh)
    normal = np.asarray(face.normal_local, dtype=float)
    normal = normal / np.linalg.norm(normal)
    heel = np.asarray(face.long_axis_local, dtype=float)
    heel = heel - normal * float(heel @ normal)
    heel /= np.linalg.norm(heel)
    if verify:
        for measured, reference, name in (
            (normal, REFERENCE_FACE_NORMAL_LOCAL, "striking-face normal"),
            (heel, REFERENCE_HEEL_TOE_LOCAL, "heel-toe axis"),
        ):
            offset = _angle_deg(measured, reference)
            if offset > FACE_AXES_AGREEMENT_LIMIT_DEG:
                raise RuntimeError(
                    f"mesh {name} {np.round(measured, 4).tolist()} is {offset:.2f} degrees "
                    f"from the reference {np.round(reference, 4).tolist()}, over the "
                    f"{FACE_AXES_AGREEMENT_LIMIT_DEG:.1f} degree limit"
                )
    return normal, heel


def _face_axes_at_import() -> tuple[np.ndarray, np.ndarray] | None:
    """Re-derive the axes from the local mesh cache, or None when there is none."""
    path = default_mesh_asset_root() / "poc_7iron.npz"
    if not path.exists():
        return None
    mesh, _metadata, _digest = load_normalized_mesh(str(path))
    return face_axes_from_mesh(mesh)


_DERIVED_FACE_AXES = _face_axes_at_import()
FACE_AXES_SOURCE = "mesh" if _DERIVED_FACE_AXES is not None else "reference_constants"
FACE_NORMAL_LOCAL, HEEL_TOE_LOCAL = _DERIVED_FACE_AXES or (
    REFERENCE_FACE_NORMAL_LOCAL,
    REFERENCE_HEEL_TOE_LOCAL,
)

# Mirrored CAD hosel/ferrule direction. It is not the shaft reference used by fits.
MESH_HOSEL_AXIS_LOCAL = np.array([-0.245, 0.295, 0.924], dtype=float)
MESH_HOSEL_AXIS_LOCAL /= np.linalg.norm(MESH_HOSEL_AXIS_LOCAL)

STATIC_LOFT_DEG = 33.10
STATIC_LIE_DEG = 61.19

# Wider than any real delivery: outside these a pose is wrong, not unusual.
ENVELOPE = {
    "dynamic_loft_deg": (15.0, 50.0),
    "face_angle_deg": (-25.0, 25.0),
    "lie_deg": (45.0, 78.0),
    "sole_tilt_deg": (-15.0, 15.0),
}


def _rotation(axis: np.ndarray, angle_rad: float) -> np.ndarray:
    axis = np.asarray(axis, dtype=float)
    axis /= np.linalg.norm(axis)
    cross = np.array(
        [
            [0.0, -axis[2], axis[1]],
            [axis[2], 0.0, -axis[0]],
            [-axis[1], axis[0], 0.0],
        ]
    )
    cosine, sine = math.cos(angle_rad), math.sin(angle_rad)
    return cosine * np.eye(3) + (1.0 - cosine) * np.outer(axis, axis) + sine * cross


def _grounded_basis(dynamic_loft_deg: float, face_angle_deg: float) -> np.ndarray:
    loft = math.radians(float(dynamic_loft_deg))
    face_angle = math.radians(float(face_angle_deg))
    face_world = np.array(
        [
            math.cos(loft) * math.cos(face_angle),
            math.cos(loft) * math.sin(face_angle),
            math.sin(loft),
        ]
    )
    heel_world = np.array([math.sin(face_angle), -math.cos(face_angle), 0.0])
    height_world = np.cross(face_world, heel_world)
    height_world /= np.linalg.norm(height_world)

    height_local = np.cross(FACE_NORMAL_LOCAL, HEEL_TOE_LOCAL)
    height_local /= np.linalg.norm(height_local)
    local_frame = np.column_stack((FACE_NORMAL_LOCAL, HEEL_TOE_LOCAL, height_local))
    world_frame = np.column_stack((face_world, heel_world, height_world))
    return world_frame @ local_frame.T


_STATIC_GROUNDED_BASIS = _grounded_basis(STATIC_LOFT_DEG, 0.0)
_STATIC_SHAFT_WORLD = np.array(
    [0.0, -math.cos(math.radians(STATIC_LIE_DEG)), math.sin(math.radians(STATIC_LIE_DEG))]
)
# A virtual shaft with catalogue geometry. The mesh's long hosel has a measured 76 degree lie.
SHAFT_LOCAL = _STATIC_GROUNDED_BASIS.T @ _STATIC_SHAFT_WORLD
SHAFT_LOCAL /= np.linalg.norm(SHAFT_LOCAL)


def basis_from_angles(yaw_deg: float, pitch_deg: float, roll_deg: float) -> np.ndarray:
    """Local-to-world matrix for a pose, columns being the mesh's own axes."""
    normal, width, height = triad(yaw_deg, pitch_deg, roll_deg)
    return np.column_stack((normal, width, height))


def delivered_angles(basis_world: np.ndarray) -> dict[str, float]:
    """Return loft, face, golf lie, and sole tilt for a right-handed head."""
    basis = np.asarray(basis_world, dtype=float)
    face = basis @ FACE_NORMAL_LOCAL
    shaft = basis @ SHAFT_LOCAL
    heel = basis @ HEEL_TOE_LOCAL
    norms = np.asarray([np.linalg.norm(face), np.linalg.norm(shaft), np.linalg.norm(heel)])
    if not np.all(np.isfinite(norms)) or float(np.min(norms)) < 1e-9:
        raise ValueError("degenerate basis: club axes collapse to zero length")
    face, shaft, heel = face / norms[0], shaft / norms[1], heel / norms[2]
    return {
        "dynamic_loft_deg": math.degrees(math.asin(float(np.clip(face[2], -1.0, 1.0)))),
        "face_angle_deg": math.degrees(math.atan2(float(face[1]), float(face[0]))),
        "lie_deg": math.degrees(math.asin(float(np.clip(shaft[2], -1.0, 1.0)))),
        # Positive means the toe (opposite HEEL_TOE_LOCAL) is above the heel.
        "sole_tilt_deg": -math.degrees(math.asin(float(np.clip(heel[2], -1.0, 1.0)))),
    }


def angles_from_pose(yaw_deg: float, pitch_deg: float, roll_deg: float) -> dict[str, float]:
    """Convenience wrapper: pose angles straight to delivered angles."""
    return delivered_angles(basis_from_angles(yaw_deg, pitch_deg, roll_deg))


def in_envelope(angles: dict[str, float]) -> bool:
    """Could a real club have been delivered in this orientation?"""
    return all(low <= angles[key] <= high for key, (low, high) in ENVELOPE.items())


def _pose_from_basis(basis: np.ndarray) -> tuple[float, float, float]:
    normal = basis[:, 0]
    yaw_deg = math.degrees(math.atan2(float(normal[1]), float(normal[0])))
    pitch_deg = -math.degrees(math.asin(float(np.clip(normal[2], -1.0, 1.0))))
    _, unrolled_width, _ = triad(yaw_deg, pitch_deg, 0.0)
    width = basis[:, 1]
    roll_deg = math.degrees(
        math.atan2(float(normal @ np.cross(unrolled_width, width)), float(unrolled_width @ width))
    )
    pose = (yaw_deg, pitch_deg, roll_deg)
    if not np.allclose(basis_from_angles(*pose), basis, atol=1e-7):
        raise RuntimeError("grounded basis is not representable by the renderer pose")
    return pose


def square_pose(
    dynamic_loft_deg: float = STATIC_LOFT_DEG,
    face_angle_deg: float = 0.0,
    lie_deg: float = STATIC_LIE_DEG,
    *,
    seed_pose: tuple[float, float, float] | None = None,
) -> tuple[float, float, float]:
    """Build the unique grounded right-handed pose for the requested delivery.

    The default has a horizontal sole and heel toward world -y. Non-static lie
    rotates that grounded head only enough for the virtual image-shaft reference
    to reach the requested elevation. ``seed_pose`` remains API-compatible but
    cannot select the old rolled branch.
    """
    del seed_pose
    grounded = _grounded_basis(float(dynamic_loft_deg), float(face_angle_deg))
    face_world = grounded @ FACE_NORMAL_LOCAL

    def error(delta_deg: float) -> float:
        rotated = _rotation(face_world, math.radians(delta_deg)) @ grounded
        shaft = rotated @ SHAFT_LOCAL
        elevation = math.degrees(math.asin(float(np.clip(shaft[2], -1.0, 1.0))))
        return elevation - float(lie_deg)

    samples = np.linspace(-90.0, 90.0, 73)
    errors = np.asarray([error(float(value)) for value in samples])
    roots: list[float] = []
    for index in range(len(samples) - 1):
        first, second = float(errors[index]), float(errors[index + 1])
        if abs(first) < 1e-9:
            roots.append(float(samples[index]))
        elif first * second < 0.0:
            roots.append(float(brentq(error, float(samples[index]), float(samples[index + 1]))))
    if not roots:
        raise RuntimeError(f"no grounded pose delivers lie {float(lie_deg):.3f} degrees")
    delta_deg = min(roots, key=abs)
    basis = _rotation(face_world, math.radians(delta_deg)) @ grounded
    return _pose_from_basis(basis)
