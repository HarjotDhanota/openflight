"""Bound current-image observations and an unqualified object-association graph."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass

import cv2
import numpy as np

from ..rig_geometry import geometry_fingerprint


@dataclass(frozen=True)
class ClubFrameIdentity:
    """Loader-supplied image identity; unknown equipment/optics remain unknown."""

    capture_sha256: str
    frame_index: int
    frame_sha256: str
    sensor_timestamp_ns: int
    exposure_us: float
    timestamp_semantics: str
    view_id: str
    mode_id: str | None
    controls_id: str | None
    optics_id: str | None
    equipment_id: str | None

    def __post_init__(self):
        for value in (self.capture_sha256, self.frame_sha256):
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(c not in "0123456789abcdef" for c in value)
            ):
                raise ValueError("lowercase SHA256 capture and image identities required")
        for value, minimum in ((self.frame_index, 0), (self.sensor_timestamp_ns, 1)):
            if type(value) is not int or value < minimum:
                raise ValueError(
                    "nonnegative frame index and positive integer sensor time required"
                )
        if (
            isinstance(self.exposure_us, bool)
            or not math.isfinite(self.exposure_us)
            or self.exposure_us <= 0
        ):
            raise ValueError("positive finite exposure required")
        if self.timestamp_semantics not in {
            "recorded_start",
            "recorded_midpoint",
            "recorded_end",
            "unknown",
        }:
            raise ValueError("unsupported sensor-time semantics; callback time is not sensor time")
        for value in (
            self.view_id,
            self.mode_id,
            self.controls_id,
            self.optics_id,
            self.equipment_id,
        ):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError("context identities must be nonempty strings or explicit None")
        if self.view_id is None:
            raise ValueError("an explicit raw-coordinate view identity is required")

    @property
    def context_key(self):
        """Changes invalidate association/cache state, including view and equipment."""
        names = (
            "capture_sha256",
            "view_id",
            "mode_id",
            "controls_id",
            "optics_id",
            "equipment_id",
            "timestamp_semantics",
        )
        return geometry_fingerprint({name: getattr(self, name) for name in names})


@dataclass(frozen=True)
class BoundClubObservation:
    """Immutable serialization; a consumer gets its own decoded copy."""

    identity: ClubFrameIdentity
    evidence_json: bytes

    @property
    def evidence(self):
        """Return separate current-pixel and inferred-region records."""
        return json.loads(self.evidence_json)


def bind_observation(frame, evidence, identity, *, expected_identity, background_frame_indices):
    """Bind numeric-kernel output to the authoritative loader's expected frame.

    This is an integrity boundary, not proof of equipment or physical calibration.
    Background frames must precede this frame; future-assisted analyses need their
    own distinct contract rather than being mislabelled current observations.
    """
    if identity != expected_identity:
        raise ValueError("wrong capture/frame/view/mode/control/optics/equipment identity")
    if (
        frame.dtype != np.uint8
        or frame.ndim != 2
        or hashlib.sha256(frame.tobytes()).hexdigest() != identity.frame_sha256
    ):
        raise ValueError("image bytes do not match the loader's frame identity")
    background = tuple(background_frame_indices)
    if not background or any(
        type(i) is not int or i < 0 or i >= identity.frame_index for i in background
    ):
        raise ValueError("own past-frame background lineage required")
    if len(set(background)) != len(background):
        raise ValueError("duplicate background lineage")
    if evidence is not None:
        if evidence.get("schema") != "openflight.club_part_observations.v1":
            raise ValueError("unsupported part-observation schema")
        if (
            evidence.get("uses_future_frames") is not False
            or evidence.get("measurement_eligible") is not False
        ):
            raise ValueError("only current, explicitly nonmetric part evidence is admitted")
        for key in ("qualified_outline", "physical_pose", "strike"):
            if evidence.get(key) is not None:
                raise ValueError("a part observation cannot carry physical metric qualification")
        points = evidence["strong_points"] + [p["xy"] for p in evidence["weak_points"]]
        for x, y in points:
            if (
                type(x) is not int
                or type(y) is not int
                or not 0 <= x < frame.shape[1]
                or not 0 <= y < frame.shape[0]
            ):
                raise ValueError("current image pixel outside bound frame")
    payload = {
        "identity": asdict(identity),
        "image_shape": list(frame.shape),
        "background_frame_indices": background,
        "parts": evidence,
        "source": "own_current_image",
        "physical_pose": None,
        "strike": None,
    }
    encoded = json.dumps(payload, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
    return BoundClubObservation(identity, encoded)


def _regions(packet):
    payload = packet.evidence
    evidence = payload["parts"]
    if evidence is None:
        return []
    points = np.asarray(evidence["strong_points"], float).reshape(-1, 2)
    result = []
    for i, component in enumerate(evidence["region"]["components"]):
        for j, outline in enumerate(component["outlines"]):
            # The box and centre propose association only; completion is not observed.
            curve = np.asarray(outline, float)
            low, high = curve.min(axis=0), curve.max(axis=0)
            mask = np.zeros(payload["image_shape"], np.uint8)
            cv2.fillPoly(mask, [curve.astype(np.int32)], 1)
            xy = points.astype(int)
            inside = mask[xy[:, 1], xy[:, 0]] > 0
            current = points[inside]
            if not len(current):
                continue
            result.append(
                {
                    "id": f"{packet.identity.frame_index}:{i}:{j}",
                    "centre_px": current.mean(axis=0).tolist(),
                    "search_box_px": np.r_[low, high].tolist(),
                    "region_completion_is_inferred": True,
                }
            )
    return result


class ClubAssociationGraph:
    """Retain all current candidates/links; loss widens search without detections.

    The rate and pixel scale are declared search priors, not calibrated uncertainty
    or hard swing limits. This class neither selects nor qualifies a club identity.
    """

    def __init__(self, *, motion_scale_px_s, base_margin_px, reacquire_after_ns):
        values = (motion_scale_px_s, base_margin_px, reacquire_after_ns)
        if any(isinstance(v, bool) or not math.isfinite(v) or v <= 0 for v in values):
            raise ValueError("positive finite search-prior scales required")
        self.motion_scale_px_s = motion_scale_px_s
        self.base_margin_px = base_margin_px
        self.reacquire_after_ns = reacquire_after_ns
        self._last_identity = None
        self._last_current = None
        self._candidates = []

    def update(self, packet):
        """Process one current frame; no future packets or previous-shot carryover."""
        identity = packet.identity
        context_changed = (
            self._last_identity is not None
            and identity.context_key != self._last_identity.context_key
        )
        if self._last_identity is not None and not context_changed:
            if (
                identity.frame_index <= self._last_identity.frame_index
                or identity.sensor_timestamp_ns <= self._last_identity.sensor_timestamp_ns
            ):
                raise ValueError("duplicate, reordered or stale frame")
        if context_changed:
            self._last_current, self._candidates = None, []
        age = (
            None
            if self._last_current is None
            else identity.sensor_timestamp_ns - self._last_current
        )
        broad = age is None or age > self.reacquire_after_ns
        scale = self.base_margin_px + (age or 0) * 1e-9 * self.motion_scale_px_s
        current = _regions(packet)
        links = []
        if not broad:
            for candidate in current:
                for previous in self._candidates:
                    distance = np.linalg.norm(
                        np.asarray(candidate["centre_px"]) - previous["centre_px"]
                    )
                    links.append(
                        {
                            "previous": previous["id"],
                            "current": candidate["id"],
                            "search_prior_cost": float((distance / scale) ** 2),
                        }
                    )
        rois = (
            []
            if broad
            else [
                (np.asarray(c["search_box_px"]) + [-scale, -scale, scale, scale]).tolist()
                for c in self._candidates
            ]
        )
        self._last_identity = identity
        if current:
            self._last_current, self._candidates = identity.sensor_timestamp_ns, current
        elif broad:
            self._last_current, self._candidates = None, []
        return {
            "schema": "openflight.club_association_graph.v1",
            "identity": asdict(identity),
            "context_reset": context_changed,
            "current_candidates": current,
            "candidate_links": links,
            "search": "broad_reacquisition" if broad else "expanded_prior_regions",
            "prior_search_boxes_px": rois,
            "status": "current_identity_hypotheses" if current else "lost_no_current_observation",
            "uses_future_frames": False,
            "selected_identity": None,
            "physical_pose": None,
            "strike": None,
        }
