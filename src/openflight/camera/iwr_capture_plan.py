"""Named IWR capture plans for the self-triggered shot session.

A plan is one ``phaseCaptureCfg`` line. Only its pre phase (the frames retained
before the trigger) differs between plans; the impact and ball phases are the
reviewed ones. ``check`` mirrors the reviewed image's rules
(firmware/iwr6843/l3_dump.c ``l3_cli_phaseCaptureCfg`` and
``l3_finalizeCapturePlan``) so a plan the firmware would refuse never reaches it.
"""

from __future__ import annotations

DEFAULT = "default"
PHASE_CAPTURE = {
    # pre bins 20..72 (0.94-3.4 m) x 9 frames: today's reviewed line, byte-identical
    "default": "phaseCaptureCfg 20 53 9 32 53 7 47 53 47 8 1",
    # pre bins 16..47 (0.75-2.2 m) x 17 frames (51 ms at 3 ms): the ball's address
    # bin and the club's walk to it; 753 KB of the 768 KB L3, 32 frames
    "club51": "phaseCaptureCfg 16 32 17 32 53 7 47 53 47 8 1",
}
FIELDS = (
    "pre_start",
    "pre_bins",
    "pre_frames",
    "impact_start",
    "impact_bins",
    "impact_frames",
    "post_start",
    "post_bins",
    "late_start",
    "ball_frames",
    "ball_stride",
)
# Fields the firmware refuses at zero.
NONZERO = (
    "pre_bins",
    "pre_frames",
    "impact_bins",
    "impact_frames",
    "post_bins",
    "ball_frames",
    "ball_stride",
)
# l3_dump.c, CONFIGURABLE_CAPTURE
N_SAMPLES = 128
MAX_CAPTURE_FRAMES = 64
MAX_POST_STRIDE = 16
MIN_LOOPS, MAX_LOOPS = 2, 16
L3_TOTAL_BYTES = 6 * 128 * 1024
IQ16_BYTES_PER_COMPLEX = 4


def names() -> tuple[str, ...]:
    """Every selectable plan, the default first."""
    return tuple(PHASE_CAPTURE)


def phase_line(name: str) -> str:
    """The plan's ``phaseCaptureCfg`` line."""
    try:
        return PHASE_CAPTURE[name]
    except (KeyError, TypeError):
        raise ValueError(
            f"unknown IWR capture plan {name!r}; choose one of {', '.join(names())}"
        ) from None


def commands(base, name: str) -> tuple[str, ...]:
    """``base`` with its one ``phaseCaptureCfg`` line replaced by the plan's."""
    base = tuple(base)
    if sum(row.startswith("phaseCaptureCfg ") for row in base) != 1:
        raise ValueError("a capture profile needs exactly one phaseCaptureCfg line")
    line = phase_line(name)
    return tuple(line if row.startswith("phaseCaptureCfg ") else row for row in base)


def _values(line: str) -> dict[str, int]:
    name, *tokens = line.split()
    if name != "phaseCaptureCfg" or len(tokens) != len(FIELDS):
        raise ValueError("phaseCaptureCfg needs 11 values")
    if not all(token.isdigit() and int(token) <= 0xFF for token in tokens):
        raise ValueError("phaseCaptureCfg values must be uint8 integers")
    return dict(zip(FIELDS, (int(token) for token in tokens)))


def phases(line: str) -> dict[str, dict[str, int]]:
    """The pre, impact and ball phases a ``phaseCaptureCfg`` line asks for."""
    v = _values(line)
    return {
        "pre": {"start": v["pre_start"], "bins": v["pre_bins"], "frames": v["pre_frames"]},
        "impact": {
            "start": v["impact_start"],
            "bins": v["impact_bins"],
            "frames": v["impact_frames"],
        },
        "ball": {
            "start": v["post_start"],
            "late_start": v["late_start"],
            "bins": v["post_bins"],
            "frames": v["ball_frames"],
            "stride": v["ball_stride"],
        },
    }


def pre_bins(name: str) -> range:
    """The range bins the plan's pre phase retains: where its ball may lock and arm."""
    pre = phases(phase_line(name))["pre"]
    return range(pre["start"], pre["start"] + pre["bins"])


def _only(lines, name: str) -> list[str]:
    rows = [row.split()[1:] for row in lines if row.split()[:1] == [name]]
    if len(rows) != 1:
        raise ValueError(f"a capture profile needs exactly one {name} line")
    return rows[0]


def check(lines) -> dict:
    """Refuse a profile the reviewed image would refuse; summarize what it retains."""
    lines = tuple(lines)
    if _only(lines, "captureFormat") != ["iq16"]:
        raise ValueError("shot capture plans are checked for captureFormat iq16 only")
    frame = _only(lines, "frameCfg")
    chirp_start, chirp_end, loops = (int(value) for value in frame[:3])
    period_us = round(float(frame[4]) * 1000)
    receivers = bin(int(_only(lines, "channelCfg")[0])).count("1")
    if not MIN_LOOPS <= loops <= MAX_LOOPS or loops % 2:
        raise ValueError(f"loops must be even and between {MIN_LOOPS} and {MAX_LOOPS}")
    line = "phaseCaptureCfg " + " ".join(_only(lines, "phaseCaptureCfg"))
    v = _values(line)
    if (
        any(v[key] == 0 for key in NONZERO)
        or v["ball_stride"] > MAX_POST_STRIDE
        or period_us * v["ball_stride"] > 0xFFFF
    ):
        raise ValueError(
            "phaseCaptureCfg needs non-zero windows and frames and a ball stride of "
            f"1-{MAX_POST_STRIDE}"
        )
    if any(
        v[start] + v[bins] > N_SAMPLES
        for start, bins in (
            ("pre_start", "pre_bins"),
            ("impact_start", "impact_bins"),
            ("post_start", "post_bins"),
            ("late_start", "post_bins"),
        )
    ):
        raise ValueError(f"phaseCaptureCfg windows must end inside the {N_SAMPLES}-bin range FFT")
    post_frames = v["impact_frames"] + v["ball_frames"]
    total_frames = v["pre_frames"] + post_frames
    if post_frames >= MAX_CAPTURE_FRAMES or total_frames > MAX_CAPTURE_FRAMES:
        raise ValueError(
            f"phaseCaptureCfg needs 1-{MAX_CAPTURE_FRAMES - 1} post frames and at most "
            f"{MAX_CAPTURE_FRAMES} frames in all"
        )
    # bytes one range bin takes in one retained frame: every chirp x every RX x IQ16
    per_bin = (chirp_end - chirp_start + 1) * loops * receivers * IQ16_BYTES_PER_COMPLEX
    post_bytes = per_bin * (
        v["impact_bins"] * v["impact_frames"] + v["post_bins"] * v["ball_frames"]
    )
    used = post_bytes + per_bin * v["pre_bins"] * v["pre_frames"]
    if post_bytes >= L3_TOTAL_BYTES or used > L3_TOTAL_BYTES:
        raise ValueError(f"capture needs {used} bytes; L3 has {L3_TOTAL_BYTES}")
    return {
        **phases(line),
        "frames": total_frames,
        "bytes": used,
        "capacity_bytes": L3_TOTAL_BYTES,
        "bytes_per_bin_frame": per_bin,
        "pre_ms": v["pre_frames"] * period_us / 1000,
        "pre_window_bins": [v["pre_start"], v["pre_start"] + v["pre_bins"] - 1],
    }


def record(name: str, lines) -> dict:
    """What a session retains about its plan: the name and every line actually sent."""
    lines = tuple(lines)
    if _only(lines, "phaseCaptureCfg") != phase_line(name).split()[1:]:
        raise ValueError(f"profile does not carry the {name} capture plan")
    summary = check(lines)
    return {
        "schema": "openflight.iwr-capture-plan.v1",
        "name": name,
        "commands": list(lines),
        "phase_capture": summary,
        "trigger_bins": summary["pre_window_bins"],
        "physical_timing_qualified": False,
    }
