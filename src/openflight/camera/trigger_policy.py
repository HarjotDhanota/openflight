"""Declared acquisition capabilities and publication policy; no clock or device owner."""

from copy import deepcopy

POLICIES = {
    "sound": ("Sound baseline", "Existing sound capture route; not a free-play route.", True),
    "ops_led": (
        "OPS master (IWR self-detects)",
        "OPS detects the shot and retains OPS/camera; the IWR self-triggers and the host "
        "associates it. OPS cannot start an IWR capture on this rig.",
        True,
    ),
    "iwr_led": (
        "IWR master",
        "The IWR's immediate Triggered notice asks the OPS for one capture (S! through the "
        "single OPS reader) and the camera follows that capture's GPIO edge. Not "
        "timing-qualified: the notice delay against the chosen OPS split is measured on the "
        "rig (C4), and the OPS speed trigger is guarded, with every dump's origin checked.",
        True,
    ),
    "independent": (
        "Independent triggers",
        "Each radar self-triggers and retains independently; OPS retains camera; the host "
        "correlates captures. IWR-only evidence is partial.",
        True,
    ),
    "hybrid": (
        "Hybrid (OPS retention, IWR confirmation)",
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
FREE_PLAY_POLICIES = ("ops_led", "iwr_led", "independent", "hybrid", "common_hardware")
CONFIRMATION_RULE = "owned_event_valid_gpio_and_iwr_frame_interval_overlaps_actual_camera_coverage"
# OPS S#n: blocks of 128 samples (4.267 ms at 30 ksps) kept before the capture request.
NATIVE_OPS_SPLIT = 6
IWR_MASTER_DEFAULT_SPLIT = 16
NO_PACKAGE = (
    "Unavailable: the reviewed IWR clock package was not found. Start the tester from the "
    "staged packet or pass --internal-sync-clock-package."
)


def capabilities(package):
    """Free play's trigger architectures, each unavailable one with its reason."""
    rows = []
    for key in FREE_PLAY_POLICIES:
        label, reason, built = POLICIES[key]
        available = built and package is not None
        rows.append(
            {
                "id": key,
                "label": label,
                "reason": reason if available or not built else NO_PACKAGE,
                "available": available,
                "recommended_experiment": key == "iwr_led",
            }
        )
    return rows


def validate(policy, *, package=True):
    """Fail before arming; never substitute another detector."""
    if policy == "sound":
        return policy
    routes = {row["id"]: row for row in capabilities(package)}
    if not isinstance(policy, str) or policy not in routes:
        raise ValueError("Unknown trigger_policy")
    if not routes[policy]["available"]:
        raise ValueError(routes[policy]["reason"])
    return policy


def selection(payload, action, package):
    """Free play runs only an explicitly chosen architecture; other actions use sound."""
    mode = payload.get("capture_mode")
    if mode is not None and (not isinstance(mode, str) or mode not in {"sound", "native_internal"}):
        raise ValueError("capture_mode must be sound or native_internal")
    if action != "free_play":
        policy = payload.get("trigger_policy", "sound")
        if policy != "sound" or mode == "native_internal":
            raise ValueError("native trigger policies require tester free play")
        return policy
    if mode == "sound" or payload.get("trigger_policy") == "sound":
        raise ValueError("Free play does not use the sound trigger; choose an architecture")
    policy = payload.get("trigger_policy", "independent" if mode == "native_internal" else None)
    if policy is None:
        raise ValueError("Choose a trigger architecture for free play")
    return validate(policy, package=package)


def ops_split(policy, requested=None):
    """IWR master's chosen OPS split (default 16); every other route keeps the native 6."""
    if policy != "iwr_led":
        if requested is not None:
            raise ValueError("only IWR master takes an OPS split")
        return NATIVE_OPS_SPLIT
    if requested is None:
        return IWR_MASTER_DEFAULT_SPLIT
    if type(requested) is not int or not 0 <= requested <= 32:
        raise ValueError("OPS split must be a whole number of 128-sample blocks, 0..32")
    return requested


def snapshot(policy, ops_pre_trigger_segments=None):
    """Freeze policy settings independently of per-attempt association and metric extraction."""
    validate(policy)
    split = ops_split(policy, ops_pre_trigger_segments)
    return {
        "schema": "openflight.trigger-policy.v1",
        "policy": policy,
        "label": POLICIES[policy][0],
        "limitation": POLICIES[policy][1],
        "capture_mode": "sound" if policy == "sound" else "native_internal",
        "retention_route": "sound_hardware"
        if policy == "sound"
        else "iwr_notice_ops_request_ops_gpio_camera"
        if policy == "iwr_led"
        else "independent_radar_freeze_ops_gpio_camera",
        "iwr_initiated_by_ops": False,
        "ops_initiated_by_iwr": policy == "iwr_led",
        "ops_pre_trigger_segments": split,
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
        "iwr_role": "candidate"
        if policy == "independent"
        else "initiator"
        if policy == "iwr_led"
        else "confirmation_or_passive_observer",
        "confirmed": confirmed,
        "publication_allowed": allowed,
        "reason": "eligible_for_existing_ball_strike_processor"
        if allowed
        else "no_ops_capture"
        if not ops
        else "hybrid_unconfirmed: " + association.get("reason", "missing association"),
        "physical_timing_qualified": False,
    }
