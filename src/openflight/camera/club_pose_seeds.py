"""Retain contact-frame initialization modes when discrete paths reconverge."""

from .club_pose_sequence import sequence_hypotheses


def anchored_sequence_hypotheses(
    pools,
    timestamps_ns,
    frame_indices,
    *,
    anchor_frame_index,
    collision_window_ns,
    rate_scale_deg_s=5000.0,
    temporal_weight=0.05,
    paths_retained=3,
):
    """Compute k-best paths through each scored current anchor candidate.

    These are conditional initializers, not confidence-ranked physical poses.
    Input pools and the unconditioned paths remain available. Anchoring constrains
    a discrete hypothesis; it does not mark its geometry as a new observation.
    """
    options = {
        "collision_window_ns": collision_window_ns,
        "rate_scale_deg_s": rate_scale_deg_s,
        "temporal_weight": temporal_weight,
        "paths_retained": paths_retained,
    }
    unanchored = sequence_hypotheses(pools, timestamps_ns, frame_indices, **options)
    if (
        not isinstance(anchor_frame_index, int)
        or isinstance(anchor_frame_index, bool)
        or anchor_frame_index not in frame_indices
    ):
        raise ValueError("an existing explicit anchor frame identity is required")
    anchor = list(frame_indices).index(anchor_frame_index)
    groups = []
    for candidate_index, candidate in enumerate(pools[anchor]):
        if candidate["score"] is None:
            continue
        family = candidate["model_id"]
        mapping = [
            [i for i, current in enumerate(pool) if current["model_id"] == family] for pool in pools
        ]
        mapping[anchor] = [candidate_index]
        selected = [
            [pool[i] for i in indices] for pool, indices in zip(pools, mapping, strict=True)
        ]
        conditional = sequence_hypotheses(selected, timestamps_ns, frame_indices, **options)
        paths = []
        for path in conditional["paths"]:
            original_indices = [
                None if index is None else mapping[f][index]
                for f, index in enumerate(path["indices"])
            ]
            assert original_indices[anchor] == candidate_index
            paths.append({**path, "indices": original_indices})
        groups.append(
            {"anchor_candidate_index": candidate_index, "model_id": family, "paths": paths}
        )
    return {
        "status": "anchor_conditioned_initialization_paths"
        if groups
        else "withheld_no_current_anchor_pose",
        "anchor_frame_index": anchor_frame_index,
        "paths_by_anchor_candidate": groups,
        "unanchored": unanchored,
        "selected_path": None,
        "physical_pose": None,
        "qualified_face_normal": None,
        "strike": None,
    }
