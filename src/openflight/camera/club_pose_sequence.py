"""Static-equipment temporal pose hypotheses; missing frames stay unobserved."""

import numpy as np
from scipy.spatial.transform import Rotation


def _transition(first, second, dt_s, rate_scale_deg_s, weight):
    rotations = Rotation.from_rotvec(np.asarray(first)[:, :3]).as_matrix()
    current = Rotation.from_rotvec(np.asarray(second)[:, :3]).as_matrix()
    cosine = (np.einsum("aij,bij->ab", rotations, current) - 1) / 2
    degrees = np.rad2deg(np.arccos(np.clip(cosine, -1, 1)))
    return weight * (degrees / dt_s / rate_scale_deg_s) ** 2


def sequence_hypotheses(
    pools,
    timestamps_ns,
    frame_indices,
    *,
    collision_window_ns,
    rate_scale_deg_s=5000.0,
    temporal_weight=0.05,
    paths_retained=3,
):
    """Reuse k-best static-family DP with explicit gap and collision phase breaks.

    Soft rate settings are search priors, not calibrated confidence or impossible-
    swing limits. All input candidate pools must remain available to the caller.
    """
    count = len(pools)
    if count == 0 or len(timestamps_ns) != count or len(frame_indices) != count:
        raise ValueError("one candidate pool and identity per frame required")
    if not np.isfinite(timestamps_ns).all() or not np.isfinite(frame_indices).all():
        raise ValueError("finite frame identities and sensor times required")
    if np.any(np.diff(timestamps_ns) <= 0) or np.any(np.diff(frame_indices) <= 0):
        raise ValueError("increasing sensor times and frame indices required")
    if (
        rate_scale_deg_s <= 0
        or not np.isfinite(rate_scale_deg_s)
        or temporal_weight < 0
        or not np.isfinite(temporal_weight)
        or type(paths_retained) is not int
        or paths_retained < 1
    ):
        raise ValueError("finite nonnegative temporal prior and positive path count required")
    if collision_window_ns is not None:
        low, high = collision_window_ns
        if not np.isfinite([low, high]).all() or low > high:
            raise ValueError("ordered finite collision interval required")
    for pool in pools:
        for candidate in pool:
            parameters = np.asarray(candidate["parameters"], float)
            if (
                parameters.shape != (6,)
                or not np.isfinite(parameters).all()
                or not isinstance(candidate["model_id"], str)
                or not candidate["model_id"]
            ):
                raise ValueError("finite rigid pose and explicit static model identity required")
            if candidate["score"] is not None:
                cost = candidate["score"]["cost"]
                if isinstance(cost, bool) or not np.isfinite(cost) or cost < 0:
                    raise ValueError("finite nonnegative image cost required")
    families = sorted({c["model_id"] for pool in pools for c in pool if c["score"] is not None})
    endings = []
    for family in families:
        active = [
            (
                f,
                [
                    i
                    for i, c in enumerate(pool)
                    if c["model_id"] == family and c["score"] is not None
                ],
            )
            for f, pool in enumerate(pools)
        ]
        active = [(f, indices) for f, indices in active if indices]
        first_frame, first = active[0]
        costs = np.full((len(first), paths_retained), np.inf)
        costs[:, 0] = [pools[first_frame][i]["score"]["cost"] for i in first]
        back = []
        for (oldf, old), (f, indices) in zip(active, active[1:]):
            before = [pools[oldf][i]["parameters"] for i in old]
            now = [pools[f][i]["parameters"] for i in indices]
            collision = (
                collision_window_ns is not None
                and timestamps_ns[oldf] <= high
                and timestamps_ns[f] >= low
            )
            if frame_indices[f] != frame_indices[oldf] + 1 or collision:
                transition = np.zeros((len(old), len(indices)))
            else:
                transition = _transition(
                    before,
                    now,
                    (timestamps_ns[f] - timestamps_ns[oldf]) * 1e-9,
                    rate_scale_deg_s,
                    temporal_weight,
                )
            options = (costs[:, :, None] + transition[:, None, :]).reshape(-1, len(indices))
            winners = np.argsort(options, axis=0, kind="stable")[:paths_retained].T
            costs = np.take_along_axis(options.T, winners, axis=1)
            costs += np.asarray([pools[f][i]["score"]["cost"] for i in indices])[:, None]
            back.append(winners)
        for position in range(len(active[-1][1])):
            for rank in range(paths_retained):
                if not np.isfinite(costs[position, rank]):
                    continue
                indices = [None] * count
                at, k = position, rank
                for a in range(len(active) - 1, -1, -1):
                    f, valid = active[a]
                    indices[f] = valid[at]
                    if a:
                        at, k = divmod(int(back[a - 1][at, k]), paths_retained)
                endings.append(
                    {
                        "model_id": family,
                        "indices": indices,
                        "cost": float(costs[position, rank]),
                        "observed_frames": len(active),
                    }
                )
    endings.sort(
        key=lambda p: (
            -p["observed_frames"],
            p["cost"],
            p["model_id"],
            [i if i is not None else -1 for i in p["indices"]],
        )
    )
    best_family = [next(p for p in endings if p["model_id"] == f) for f in families]
    return {
        "status": "conditional_pose_sequence" if endings else "withheld_no_current_pose_hypothesis",
        "paths": endings[:paths_retained],
        "best_by_static_model": best_family,
        "retained_endings": endings,
        "physical_pose": None,
        "qualified_face_normal": None,
        "strike": None,
    }
