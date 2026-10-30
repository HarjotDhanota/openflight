"""Declared acquisition capabilities and publication policy; no clock or device owner."""

from copy import deepcopy

POLICIES = {
    "sound": ("Sound baseline", "Existing sound capture route.", True),
    "iwr_led": (
        "IWR-led (recommended experiment)",
        "Unavailable: completed IWR notification follows about 1.15 s of post capture; "
        "OPS retains only 136.5 ms. No verified early IWR trigger line.",
        False,
    ),
    "ops_led": (
        "OPS-led (IWR association only)",
        "OPS retains OPS/camera; IWR must self-detect. OPS cannot initiate IWR capture.",
        True,
    ),
    "independent": (
        "Independent native detectors",
        "Each radar retains independently; OPS retains camera. IWR-only evidence is partial.",
        True,
    ),
    "hybrid": (
        "Hybrid confirmation (OPS retention)",
        "Retain OPS/camera promptly; publish only with owned IWR frame-clock/clip overlap. "
        "IWR notification cannot initiate prompt OPS retention on this rig.",
        True,
    ),
    "common_hardware": (
        "Common hardware trigger",
        "Unavailable: common wiring, firmware capture handler and buffer semantics "
        "have not been verified across the sensors.",
        False,
    ),
}
CONFIRMATION_RULE = "owned_event_valid_gpio_and_iwr_frame_interval_overlaps_actual_camera_coverage"


def capabilities(package):
    """Expose unavailable routes with reasons; configuration is not physical qualification."""
    return [
        {
            "id": key,
            "label": value[0],
            "reason": value[1]
            if key == "sound" or package is not None or not value[2]
            else "Unavailable: start tester with the reviewed IWR clock package.",
            "available": value[2] and (key == "sound" or package is not None),
            "recommended_experiment": key == "iwr_led",
        }
        for key, value in POLICIES.items()
    ]


def validate(policy, *, package=True):
    """Fail before arming; never substitute another detector."""
    routes = {row["id"]: row for row in capabilities(package)}
    if not isinstance(policy, str) or policy not in routes:
        raise ValueError("Unknown trigger_policy")
    if not routes[policy]["available"]:
        raise ValueError(routes[policy]["reason"])
    return policy


def selection(payload, action, package):
    """Map legacy native_internal explicitly and reject conflicting saved selections."""
    mode = payload.get("capture_mode")
    if mode is not None and (not isinstance(mode, str) or mode not in {"sound", "native_internal"}):
        raise ValueError("capture_mode must be sound or native_internal")
    policy = payload.get("trigger_policy", "independent" if mode == "native_internal" else "sound")
    validate(policy, package=package)
    actual_mode = "sound" if policy == "sound" else "native_internal"
    if mode is not None and mode != actual_mode:
        raise ValueError("capture_mode conflicts with trigger_policy")
    if actual_mode != "sound" and action != "free_play":
        raise ValueError("native trigger policies require tester free play")
    return policy


def snapshot(policy):
    """Freeze policy settings independently of per-attempt association and metric extraction."""
    validate(policy)
    return {
        "schema": "openflight.trigger-policy.v1",
        "policy": policy,
        "label": POLICIES[policy][0],
        "limitation": POLICIES[policy][1],
        "capture_mode": "sound" if policy == "sound" else "native_internal",
        "retention_route": "sound_hardware"
        if policy == "sound"
        else "independent_radar_freeze_ops_gpio_camera",
        "iwr_initiated_by_ops": False,
        "confirmation_rule": CONFIRMATION_RULE if policy == "hybrid" else None,
        "physical_timing_qualified": False,
        "native_speed_training_supported": False,
    }


def decision(settings, *, ops, iwr, association):
    """Publication permission never changes retention or upgrades an association's quality."""
    policy = settings["policy"]
    confirmed = association.get("status") == "experimental_software_candidate"
    allowed = bool(ops and (policy != "hybrid" or confirmed))
    return {
        "settings": deepcopy(settings),
        "candidate_opened": bool(ops or (iwr and policy == "independent")),
        "ops_observed": bool(ops),
        "iwr_observed": bool(iwr),
        "iwr_role": "candidate" if policy == "independent" else "confirmation_or_passive_observer",
        "confirmed": confirmed,
        "publication_allowed": allowed,
        "reason": "eligible_for_existing_ball_strike_processor"
        if allowed
        else "no_ops_capture"
        if not ops
        else "hybrid_unconfirmed: " + association.get("reason", "missing association"),
        "physical_timing_qualified": False,
    }
