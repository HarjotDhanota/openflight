"""Experimental joint SE(3) branch refinement with exposure and static equipment."""

from __future__ import annotations

import hashlib
from dataclasses import asdict

import numpy as np
from scipy.ndimage import distance_transform_edt
from scipy.optimize import least_squares

from ..rig_geometry import geometry_fingerprint
from . import _club_object_support, club_exposure, club_pose_hypotheses, club_rigid_motion
from .club_sequence import ClubFrameIdentity


def _mask(value, shape):
    result = np.asarray(value)
    if result.dtype != bool or result.shape != shape:
        raise ValueError("matching boolean current image channels required")
    return result.copy()


def _prepare(frame, seed, collision, samples, contact_hypothesis):
    identity = frame["identity"]
    image = np.asarray(frame["image"])
    if (
        not isinstance(identity, ClubFrameIdentity)
        or image.dtype != np.uint8
        or image.ndim != 2
        or hashlib.sha256(image.tobytes()).hexdigest() != identity.frame_sha256
    ):
        raise ValueError("own current image and bound sensor identity required")
    if list(frame["camera"]["size"]) != list(image.shape[::-1]):
        raise ValueError("explicit current camera view size required")
    focal = np.asarray(frame["camera"]["focal_xy_px"], float)
    principal = np.asarray(frame["camera"]["principal_px"], float)
    if (
        focal.shape != (2,)
        or principal.shape != (2,)
        or not np.isfinite(np.r_[focal, principal]).all()
        or (focal <= 0).any()
    ):
        raise ValueError("finite calibrated-view camera coordinates required")
    result = {"identity": identity, "status": "missing_current_pose_initialization"}
    if seed is None:
        return result
    club_rigid_motion.from_parameters(seed)
    if identity.timestamp_semantics == "unknown":
        return {**result, "status": "withheld_unknown_exposure_timestamp_semantics"}
    contact_offset = (
        None
        if contact_hypothesis is None
        else (contact_hypothesis - identity.sensor_timestamp_ns) * 1e-9
    )
    offsets, weights = club_rigid_motion.exposure_offsets(
        identity.exposure_us,
        identity.timestamp_semantics,
        samples=samples,
        collision_offset_s=contact_offset,
    )
    beginning, ending = club_rigid_motion.exposure_interval_ns(
        identity.sensor_timestamp_ns, identity.exposure_us, identity.timestamp_semantics
    )
    if (
        contact_hypothesis is None
        and collision is not None
        and beginning < collision[1]
        and ending > collision[0]
    ):
        return {**result, "status": "withheld_exposure_intersects_unknown_contact_interval"}
    shape = image.shape
    edges = _mask(frame["edges"], shape)
    unknown = _mask(frame["unknown"], shape)
    foreground = _mask(frame.get("current_foreground", np.zeros(shape, bool)), shape)
    background = _mask(frame.get("current_background", np.zeros(shape, bool)), shape)
    if np.any(foreground & background):
        raise ValueError("contradictory current foreground/background evidence")
    axis = np.asarray(frame["axis_pixels"], float).reshape(-1, 2)
    if not np.isfinite(axis).all() or len(axis) == 1:
        raise ValueError("finite current infinite-axis support required")
    objects = _club_object_support.support_candidates(frame.get("current_object_support"), shape)
    if objects is not None:
        pixel = objects["pixel"]
        objects["owners"][unknown[pixel[:, 1], pixel[:, 0]]] = "unassigned"
    if (
        not (edges & ~unknown).any()
        and not ((foreground | background) & ~unknown).any()
        and not len(axis)
        and (objects is None or not np.any(objects["owners"] == "head"))
    ):
        return {**result, "status": "withheld_no_current_image_evidence"}
    balls = np.asarray(frame["ball_masks"])
    if balls.shape != (len(offsets), *shape) or balls.dtype != bool:
        raise ValueError("explicit ball-mask hypotheses at every exposure sample required")
    if frame["depth_order"] not in {"ball_front", "head_front"}:
        raise ValueError("one explicit depth order per retained branch required")
    return {
        **result,
        "status": "conditional_current_image_fit",
        "seed": np.asarray(seed, float),
        "camera": frame["camera"],
        "edges": edges,
        "unknown": unknown,
        "axis": axis,
        "objects": objects,
        "foreground": foreground & ~unknown,
        "background": background & ~unknown,
        "distance": np.minimum(distance_transform_edt(~edges), 6)
        if edges.any()
        else np.zeros(shape),
        "offsets": offsets,
        "weights": weights,
        "balls": balls,
        "depth_order": frame["depth_order"],
        "contact_offset_s": contact_offset,
        "exposure_interval_ns": [beginning, ending],
        "contact_within_exposure": contact_hypothesis is not None
        and beginning < contact_hypothesis < ending,
    }


def refine_window(
    model,
    model_id,
    frames,
    initial_parameters,
    *,
    collision_window_ns,
    association_branch,
    temporal_weight=0.05,
    angular_scale_rad_s=90.0,
    translation_scale_model_s=200.0,
    samples=3,
    max_evaluations=20,
    collision_time_hypothesis_ns=None,
    analysis_scale=1.0,
    cache_exposure=True,
):
    """Refine one retained static-model/object/layer branch without qualification.

    Image channels remain candidate evidence. No current background is inferred
    from low change. Each caller-supplied branch remains available to its owner;
    convergence and a lower cost never identify a unique face or physical strike.
    """
    if not frames or len(frames) != len(initial_parameters):
        raise ValueError("one initialization or explicit missing state per frame required")
    if (
        not np.isfinite(analysis_scale)
        or analysis_scale <= 0
        or not isinstance(cache_exposure, bool)
    ):
        raise ValueError("positive analysis/reference pixel scale and explicit cache mode required")
    vertices = np.asarray(model["vertices"], float)
    triangles = np.asarray(model["triangles"], float)
    if (
        vertices.ndim != 2
        or vertices.shape[1:] != (3,)
        or not len(vertices)
        or triangles.ndim != 3
        or triangles.shape[1:] != (3, 3)
        or not len(triangles)
        or not np.isfinite(vertices).all()
        or not np.isfinite(triangles).all()
    ):
        raise ValueError("finite static model vertices and full triangles required")
    if (
        not isinstance(model_id, str)
        or not model_id
        or not isinstance(association_branch, str)
        or not association_branch
    ):
        raise ValueError("explicit static model and association branch identities required")
    if (
        not np.isfinite([temporal_weight, angular_scale_rad_s, translation_scale_model_s]).all()
        or temporal_weight < 0
        or min(angular_scale_rad_s, translation_scale_model_s) <= 0
    ):
        raise ValueError("finite soft motion-prior scales required")
    if collision_window_ns is not None:
        low, high = collision_window_ns
        if not np.isfinite([low, high]).all() or low > high:
            raise ValueError("ordered finite contact interval required")
    if collision_time_hypothesis_ns is not None:
        if (
            collision_window_ns is None
            or not np.isfinite(collision_time_hypothesis_ns)
            or not low <= collision_time_hypothesis_ns <= high
        ):
            raise ValueError("conditional contact-time hypothesis must lie inside own interval")
    prepared = [
        _prepare(frame, seed, collision_window_ns, samples, collision_time_hypothesis_ns)
        for frame, seed in zip(frames, initial_parameters, strict=True)
    ]
    for row in prepared:
        if "seed" in row and row["edges"].any():
            row["distance"] = np.minimum(distance_transform_edt(~row["edges"]) / analysis_scale, 6)
    identities = [row["identity"] for row in prepared]
    if len({identity.context_key for identity in identities}) != 1:
        raise ValueError("one capture/view/control/optics/equipment context per window required")
    if len({geometry_fingerprint(frame["camera"]) for frame in frames}) != 1:
        raise ValueError("camera numeric view changed without context reset")
    if np.any(np.diff([identity.sensor_timestamp_ns for identity in identities]) <= 0) or np.any(
        np.diff([identity.frame_index for identity in identities]) <= 0
    ):
        raise ValueError("increasing own-frame sensor identities required")
    active = [i for i, row in enumerate(prepared) if "seed" in row]
    base = {
        "model_id": model_id,
        "association_branch": association_branch,
        "frames": [],
        "physical_pose": None,
        "qualified_face_normal": None,
        "strike": None,
        "depth_source": "CAD_conditioned_not_measured_range",
        "capture_sha256": identities[0].capture_sha256,
        "camera_coordinate_frame": "declared_pinhole_optical_RDF",
        "translation_units": "static_model_units",
        "optics_id": identities[0].optics_id,
        "collision_time_hypothesis_ns": collision_time_hypothesis_ns,
        "contact_time_is_measured": False,
        "analysis_to_reference_pixel_scale": analysis_scale,
        "motion_prior": {
            "weight": temporal_weight,
            "angular_scale_rad_s": angular_scale_rad_s,
            "translation_scale_model_s": translation_scale_model_s,
            "is_sensor_measurement": False,
        },
    }
    if not active:
        return {
            **base,
            "status": "withheld_no_current_pose_observation",
            "frames": [
                {
                    "frame_index": row["identity"].frame_index,
                    "identity": asdict(row["identity"]),
                    "status": row["status"],
                    "conditional_parameters": None,
                }
                for row in prepared
            ],
        }
    phases, phase_of = [], {}
    for index in active:
        identity = identities[index]
        if phases:
            previous = phases[-1][-1]
            old = identities[previous]
            interval = (
                collision_window_ns
                if collision_time_hypothesis_ns is None
                else [collision_time_hypothesis_ns] * 2
            )
            collision = (
                interval is not None
                and old.sensor_timestamp_ns <= interval[1]
                and identity.sensor_timestamp_ns >= interval[0]
            )
            linked = identity.frame_index == old.frame_index + 1 and not collision
        else:
            linked = False
        if not linked:
            phases.append([])
        phases[-1].append(index)
        phase_of[index] = len(phases) - 1
    for index in active:
        row = prepared[index]
        row["before_phase"] = row["after_phase"] = phase_of[index]
        if row["contact_within_exposure"]:
            before = [
                i
                for i in active
                if identities[i].sensor_timestamp_ns < collision_time_hypothesis_ns
            ]
            after = [
                i
                for i in active
                if identities[i].sensor_timestamp_ns >= collision_time_hypothesis_ns
            ]
            for name, nearby in (("before_phase", before[-1:]), ("after_phase", after[:1])):
                if nearby:
                    row[name] = phase_of[nearby[0]]
                else:
                    row[name] = len(phases)
                    phases.append([])
    phase_support = [[] for _ in phases]
    for index in active:
        row = prepared[index]
        low, high = row["exposure_interval_ns"]
        spans = [(row["before_phase"], low, high)]
        if row["contact_within_exposure"]:
            spans = [
                (row["before_phase"], low, collision_time_hypothesis_ns),
                (row["after_phase"], collision_time_hypothesis_ns, high),
            ]
        for phase, start, end in spans:
            phase_support[phase].append(
                {
                    "identity": asdict(row["identity"]),
                    "full_exposure_interval_ns": [low, high],
                    "used_interval_ns": [start, end],
                    "split_contact_hypothesis_ns": collision_time_hypothesis_ns
                    if row["contact_within_exposure"]
                    else None,
                }
            )
    active_position = {index: position for position, index in enumerate(active)}
    seeds = np.asarray([prepared[index]["seed"] for index in active])
    twists = []
    links = []
    for phase_index, phase in enumerate(phases):
        velocities = []
        for first, second in zip(phase, phase[1:]):
            duration = (
                identities[second].sensor_timestamp_ns - identities[first].sensor_timestamp_ns
            ) * 1e-9
            velocities.append(
                club_rigid_motion.body_velocity(
                    club_rigid_motion.from_parameters(prepared[first]["seed"]),
                    club_rigid_motion.from_parameters(prepared[second]["seed"]),
                    duration,
                )
            )
            links.append((first, second, phase_index, duration))
        twists.append(np.mean(velocities, axis=0) if velocities else np.zeros(6))
    initial = np.r_[seeds.ravel(), np.asarray(twists).ravel()]
    scale = np.r_[np.full(3, angular_scale_rad_s), np.full(3, translation_scale_model_s)]
    axis_evaluations = 0
    render_evaluations = 0
    cache_hits = 0
    scene_cache = {}

    def render(index, parameters, rates):
        nonlocal render_evaluations, cache_hits
        row = prepared[index]
        before, after = rates[row["before_phase"]], rates[row["after_phase"]]
        key = (parameters.tobytes(), before.tobytes(), after.tobytes())
        cached = scene_cache.get(index)
        if cache_exposure and cached is not None and cached[0] == key:
            cache_hits += 1
            return cached[1]
        scene = club_exposure.render_exposure(
            model,
            parameters,
            row["camera"],
            before,
            row["offsets"],
            row["weights"],
            row["balls"],
            depth_order=row["depth_order"],
            after_twist=after,
            collision_offset_s=row["contact_offset_s"],
        )
        scene["object_support_residual"] = np.empty(0)
        if row["objects"] is not None and np.any(row["objects"]["owners"] == "head"):
            projected = [
                club_pose_hypotheses.project(
                    model["triangles"], club_rigid_motion.to_parameters(transform), row["camera"]
                )
                for transform in scene["transforms"]
            ]
            scene["object_support_residual"] = _club_object_support.exposure_support_residual(
                projected,
                row["balls"],
                row["objects"],
                depth_order=row["depth_order"],
                analysis_scale=analysis_scale,
            )
        render_evaluations += 1
        if cache_exposure:
            scene_cache[index] = (key, scene)
        return scene

    def blocks(value):
        nonlocal axis_evaluations
        poses = value[: len(active) * 6].reshape(-1, 6)
        rates = value[len(active) * 6 :].reshape(-1, 6)
        result = {
            key: []
            for key in (
                "edge_candidates",
                "axis",
                "foreground",
                "background",
                "object_support",
                "motion",
                "rate_prior",
                "domain",
            )
        }
        for index in active:
            row, parameters = prepared[index], poses[active_position[index]]
            scene = render(index, parameters, rates)
            result["object_support"].append(scene["object_support_residual"])
            visible = scene["head_boundary"] * ~row["unknown"]
            if row["edges"].any():
                result["edge_candidates"].append(
                    (
                        np.sqrt(np.maximum(visible, 0))
                        * row["distance"]
                        / np.sqrt(max(1, scene["full_head_boundary"].sum()))
                    ).ravel()
                )
            for name in ("foreground", "background"):
                chosen = row[name]
                if chosen.any():
                    residual = 1 - scene["head"] if name == "foreground" else scene["head"]
                    result[name].append(residual[chosen] / np.sqrt(chosen.sum()))
            if len(row["axis"]):
                for transform, weight in zip(scene["transforms"], row["weights"], strict=True):
                    axis_evaluations += 1
                    axis = club_pose_hypotheses.axis_residual(
                        club_rigid_motion.to_parameters(transform),
                        model,
                        row["axis"],
                        row["camera"],
                    )
                    axis = axis / analysis_scale
                    robust = np.sign(axis) * np.sqrt(18 * (np.hypot(1, axis / 3) - 1))
                    result["axis"].append(np.sqrt(0.5 * weight / len(axis)) * robust)
            result["domain"].append(scene["domain_residual"] / analysis_scale)
        for first, second, phase, duration in links:
            velocity = club_rigid_motion.body_velocity(
                club_rigid_motion.from_parameters(poses[active_position[first]]),
                club_rigid_motion.from_parameters(poses[active_position[second]]),
                duration,
            )
            result["motion"].append(np.sqrt(temporal_weight) * (velocity - rates[phase]) / scale)
        for rate in rates:
            result["rate_prior"].append(np.sqrt(temporal_weight * 0.01) * rate / scale)
        return {
            key: np.concatenate(values) if values else np.empty(0) for key, values in result.items()
        }

    def residual(value):
        return np.concatenate(list(blocks(value).values()))

    radius = max(float(np.linalg.norm(model["vertices"], axis=1).max()), 1e-3)
    steps = np.r_[
        np.tile([0.003, 0.003, 0.003, 0.003 * radius, 0.003 * radius, 0.001], len(active)),
        np.tile(scale * 0.001, len(phases)),
    ]

    def jacobian(value):
        original = residual(value)
        columns = []
        for index, step in enumerate(steps):
            changed = value.copy()
            changed[index] += step
            columns.append((residual(changed) - original) / step)
        return np.column_stack(columns)

    lower, upper = np.full(len(initial), -np.inf), np.full(len(initial), np.inf)
    lower[np.arange(len(active)) * 6 + 5] = np.log(radius * 1.01)
    upper[np.arange(len(active)) * 6 + 5] = np.log(radius * 1e6)
    before = blocks(initial)
    fit = least_squares(
        residual,
        initial,
        jac=jacobian,
        max_nfev=max_evaluations,
        loss="soft_l1",
        f_scale=2,
        x_scale="jac",
        bounds=(lower, upper),
    )
    after = blocks(fit.x)
    poses = fit.x[: len(active) * 6].reshape(-1, 6)
    rates = fit.x[len(active) * 6 :].reshape(-1, 6)
    records = []
    for index, row in enumerate(prepared):
        record = {
            "frame_index": row["identity"].frame_index,
            "identity": asdict(row["identity"]),
            "status": row["status"],
            "conditional_parameters": None,
        }
        if index in active_position:
            scene = render(index, poses[active_position[index]], rates)
            visible_fraction = float(
                (scene["head_boundary"] * ~row["unknown"]).sum()
                / max(1, scene["full_head_boundary"].sum())
            )
            status = row["status"]
            if visible_fraction == 0:
                status = "withheld_no_visible_head_boundary"
            elif not row["edges"].any() and not (row["foreground"] | row["background"]).any():
                status = (
                    "conditional_assigned_object_support_only"
                    if (row["objects"] is not None and np.any(row["objects"]["owners"] == "head"))
                    else "conditional_axis_only"
                )
            record.update(
                status=status,
                conditional_parameters=poses[active_position[index]].tolist(),
                phase=phase_of[index],
                visible_boundary_fraction=visible_fraction,
                current_edge_candidate_pixels=int((row["edges"] & ~row["unknown"]).sum()),
                explicit_foreground_pixels=int(row["foreground"].sum()),
                explicit_background_pixels=int(row["background"].sum()),
                current_axis_points=len(row["axis"]),
                current_object_support=None
                if row["objects"] is None
                else {
                    "association_id": row["objects"]["association_id"],
                    "observation_ids": row["objects"]["observation_ids"],
                    "assigned_head_count": int(np.sum(row["objects"]["owners"] == "head")),
                    "scope": (
                        "fixed current-pixel assignment hypothesis; "
                        "conservative visible-support distance lower bound"
                    ),
                    "is_verified_foreground": False,
                    "is_visibility_confidence": False,
                },
                contact_within_exposure=row["contact_within_exposure"],
                intra_exposure_motion="joint_conditional_body_twist"
                if len(phases[phase_of[index]]) > 1
                else "singleton_twist_not_observed",
            )
        records.append(record)
    return {
        **base,
        "status": "conditional_joint_SE3_fit",
        "frames": records,
        "phase_body_twists": rates.tolist(),
        "phase_exposure_support": phase_support,
        "phase_frames": [[identities[i].frame_index for i in phase] for phase in phases],
        "phase_motion_observed_intervals": [max(0, len(phase) - 1) for phase in phases],
        "initial_factor_squared_norms": {
            key: float(value @ value) for key, value in before.items()
        },
        "factor_squared_norms": {key: float(value @ value) for key, value in after.items()},
        "axis_factor_evaluations": axis_evaluations,
        "render_evaluations": render_evaluations,
        "render_cache_hits": cache_hits,
        "render_cache_scope": "one state per own frame within this invocation only",
        "optimizer_success": bool(fit.success),
        "optimizer_evaluations": int(fit.nfev),
        "factor_scope": (
            "conditional current edge candidates and explicit pixel labels; "
            "motion priors are not sensor measurements"
        ),
    }
