"""Delivered club angles in the normalized, right-handed 690CB frame.

The striking-face axes were measured from the largest coherent planar patch on
the face, not from the cavity-rim plane used by mesh normalization. In the
source cache the heel axis was approximately ``(0, +1, +0.06)`` and pointed at
the hosel. Loading mirrors local z, so the right-handed constants below carry
the opposite z sign. ``MESH_HOSEL_AXIS_LOCAL`` records the suspect CAD hosel for
diagnostics only; pose construction uses the image shaft and the virtual
``SHAFT_LOCAL`` tied to the catalogue lie.

Those axes can be RE-DERIVED from the mesh by `mesh.detect_striking_face` -- the
patch it finds is the same one they were measured from -- but that is a
DELIBERATE call, never a side effect of importing this module. Importing it
performs no file I/O and no mesh work: the module-level `FACE_NORMAL_LOCAL`,
`HEEL_TOE_LOCAL` and `SHAFT_LOCAL` are the hand-transcribed reference
constants, and they are what every function here uses unless it is handed
something else.

To use the mesh's own axes, call `club_axes(mesh)` and pass the result to
`delivered_angles`, `angles_from_pose` or `square_pose`. `club_axes()` with no
mesh loads the local cache if it is present, which is the one path that touches
the disk, and caches the result. Derived axes are checked against the reference
constants and refused past `FACE_AXES_AGREEMENT_LIMIT_DEG`, so a different club
or a differently oriented frame cannot be adopted silently. Which set is live is
a field on the returned `ClubAxes`, not a module global.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

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

MESH_ASSET_NAME = "poc_7iron.npz"

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

_STATIC_SHAFT_WORLD = np.array(
    [0.0, -math.cos(math.radians(STATIC_LIE_DEG)), math.sin(math.radians(STATIC_LIE_DEG))]
)


def _angle_deg(first: np.ndarray, second: np.ndarray) -> float:
    cosine = float(first @ second) / (np.linalg.norm(first) * np.linalg.norm(second))
    return math.degrees(math.acos(float(np.clip(cosine, -1.0, 1.0))))


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


def _grounded_basis_from(
    face_normal_local: np.ndarray,
    heel_toe_local: np.ndarray,
    dynamic_loft_deg: float,
    face_angle_deg: float,
) -> np.ndarray:
    """Local-to-world rotation putting a head with these axes on the ground."""
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

    height_local = np.cross(face_normal_local, heel_toe_local)
    height_local /= np.linalg.norm(height_local)
    local_frame = np.column_stack((face_normal_local, heel_toe_local, height_local))
    world_frame = np.column_stack((face_world, heel_world, height_world))
    return world_frame @ local_frame.T


@dataclass(frozen=True, eq=False)
class ClubAxes:
    """The three local vectors every delivered angle is measured off.

    ``source`` is ``"mesh"`` when the striking face was measured off a mesh and
    ``"reference_constants"`` when the frozen hand-transcribed values are in
    use. Identity-hashed on purpose: `club_axes` returns one shared instance per
    mesh, so downstream `lru_cache`s can key on it.
    """

    face_normal_local: np.ndarray
    heel_toe_local: np.ndarray
    shaft_local: np.ndarray
    source: str


def _build_axes(face_normal_local, heel_toe_local, source: str) -> ClubAxes:
    """Normalize a normal/heel pair and attach the virtual catalogue shaft."""
    normal = np.asarray(face_normal_local, dtype=float)
    normal = normal / np.linalg.norm(normal)
    heel = np.asarray(heel_toe_local, dtype=float)
    heel = heel - normal * float(heel @ normal)
    heel = heel / np.linalg.norm(heel)
    # A virtual shaft with catalogue geometry. The mesh's long hosel has a
    # measured 76 degree lie, so it is not usable as the shaft reference.
    shaft = _grounded_basis_from(normal, heel, STATIC_LOFT_DEG, 0.0).T @ _STATIC_SHAFT_WORLD
    shaft = shaft / np.linalg.norm(shaft)
    for vector in (normal, heel, shaft):
        vector.flags.writeable = False
    return ClubAxes(normal, heel, shaft, str(source))


REFERENCE_CLUB_AXES = _build_axes(
    REFERENCE_FACE_NORMAL_LOCAL, REFERENCE_HEEL_TOE_LOCAL, "reference_constants"
)
FACE_NORMAL_LOCAL = REFERENCE_CLUB_AXES.face_normal_local
HEEL_TOE_LOCAL = REFERENCE_CLUB_AXES.heel_toe_local
SHAFT_LOCAL = REFERENCE_CLUB_AXES.shaft_local


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


# Keyed by id(), with the mesh held alongside so the id cannot be recycled.
# TriangleMesh carries NumPy arrays and so is unhashable; `load_normalized_mesh`
# is itself cached, so one mesh file yields one object and one entry here.
_MESH_AXES_CACHE: dict[int, tuple[TriangleMesh, ClubAxes]] = {}


@lru_cache(maxsize=1)
def _default_club_axes() -> ClubAxes:
    """Axes from the local mesh cache, or the reference constants without it."""
    path = default_mesh_asset_root() / MESH_ASSET_NAME
    if not path.exists():
        return REFERENCE_CLUB_AXES
    mesh, _metadata, _digest = load_normalized_mesh(str(path))
    return _build_axes(*face_axes_from_mesh(mesh), "mesh")


def club_axes(mesh: TriangleMesh | None = None, *, strict: bool = True) -> ClubAxes:
    """The axes to measure delivered angles against, from a mesh where possible.

    Args:
        mesh: A loaded, normalized clubhead mesh. Callers that already have one
            should pass it: this does no file I/O in that case. Omit it to use
            the local mesh cache, which IS read from disk on the first call and
            then cached, and which falls back to the reference constants when
            the cache is absent -- it is a license-pinned local artefact that is
            not committed.
        strict: Refuse a mesh whose axes disagree with the reference constants.
            Pass ``strict=False`` to fall back to them instead, for callers that
            must keep working against synthetic or stand-in meshes; the returned
            ``source`` says which happened.

    Raises:
        RuntimeError: If ``strict`` and the mesh's axes fail verification.
        ValueError: If ``strict`` and the mesh has no clubface-sized patch.
    """
    if mesh is None:
        return _default_club_axes()
    cached = _MESH_AXES_CACHE.get(id(mesh))
    if cached is not None and cached[0] is mesh:
        return cached[1]
    try:
        axes = _build_axes(*face_axes_from_mesh(mesh), "mesh")
    except (RuntimeError, ValueError):
        if strict:
            raise
        axes = REFERENCE_CLUB_AXES
    _MESH_AXES_CACHE[id(mesh)] = (mesh, axes)
    return axes


def reset_club_axes_cache() -> None:
    """Drop every memoized `club_axes` result. For tests and for reloads."""
    _MESH_AXES_CACHE.clear()
    _default_club_axes.cache_clear()


def _resolve(axes: ClubAxes | None) -> ClubAxes:
    """Default to the reference constants: never touch the disk implicitly."""
    return REFERENCE_CLUB_AXES if axes is None else axes


def _grounded_basis(
    dynamic_loft_deg: float, face_angle_deg: float, axes: ClubAxes | None = None
) -> np.ndarray:
    resolved = _resolve(axes)
    return _grounded_basis_from(
        resolved.face_normal_local, resolved.heel_toe_local, dynamic_loft_deg, face_angle_deg
    )


def basis_from_angles(yaw_deg: float, pitch_deg: float, roll_deg: float) -> np.ndarray:
    """Local-to-world matrix for a pose, columns being the mesh's own axes."""
    normal, width, height = triad(yaw_deg, pitch_deg, roll_deg)
    return np.column_stack((normal, width, height))


def delivered_angles(basis_world: np.ndarray, *, axes: ClubAxes | None = None) -> dict[str, float]:
    """Return loft, face, golf lie, and sole tilt for a right-handed head.

    ``axes`` defaults to the frozen reference constants. Pass ``club_axes(mesh)``
    to measure against the club's own striking face instead.
    """
    resolved = _resolve(axes)
    basis = np.asarray(basis_world, dtype=float)
    face = basis @ resolved.face_normal_local
    shaft = basis @ resolved.shaft_local
    heel = basis @ resolved.heel_toe_local
    norms = np.asarray([np.linalg.norm(face), np.linalg.norm(shaft), np.linalg.norm(heel)])
    if not np.all(np.isfinite(norms)) or float(np.min(norms)) < 1e-9:
        raise ValueError("degenerate basis: club axes collapse to zero length")
    face, shaft, heel = face / norms[0], shaft / norms[1], heel / norms[2]
    return {
        "dynamic_loft_deg": math.degrees(math.asin(float(np.clip(face[2], -1.0, 1.0)))),
        "face_angle_deg": math.degrees(math.atan2(float(face[1]), float(face[0]))),
        "lie_deg": math.degrees(math.asin(float(np.clip(shaft[2], -1.0, 1.0)))),
        # Positive means the toe (opposite the heel-toe axis) is above the heel.
        "sole_tilt_deg": -math.degrees(math.asin(float(np.clip(heel[2], -1.0, 1.0)))),
    }


def angles_from_pose(
    yaw_deg: float, pitch_deg: float, roll_deg: float, *, axes: ClubAxes | None = None
) -> dict[str, float]:
    """Convenience wrapper: pose angles straight to delivered angles."""
    return delivered_angles(basis_from_angles(yaw_deg, pitch_deg, roll_deg), axes=axes)


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
    axes: ClubAxes | None = None,
) -> tuple[float, float, float]:
    """Build the unique grounded right-handed pose for the requested delivery.

    The default has a horizontal sole and heel toward world -y. Non-static lie
    rotates that grounded head only enough for the virtual image-shaft reference
    to reach the requested elevation. ``seed_pose`` remains API-compatible but
    cannot select the old rolled branch. ``axes`` defaults to the frozen
    reference constants.
    """
    del seed_pose
    resolved = _resolve(axes)
    grounded = _grounded_basis(float(dynamic_loft_deg), float(face_angle_deg), resolved)
    face_world = grounded @ resolved.face_normal_local

    def error(delta_deg: float) -> float:
        rotated = _rotation(face_world, math.radians(delta_deg)) @ grounded
        shaft = rotated @ resolved.shaft_local
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
