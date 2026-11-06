"""Read-only eligibility for the single supported never-started-RF resume case."""

import base64
import hashlib
import json
import re
from pathlib import Path

BUILD = "9378430e391e8da317ce7552c6173159c8416490a5090f659b1da04fc37d0879"
MANIFEST_SHA256 = "21f9d6c4d14b44b9d5ea0dbd23a8ba885dd1f49e52bcae04f92b4aab715d2573"
FILES = ("status.json", "startup.bin", "commands.jsonl", "iwr-exchanges.json")


def _fields(raw, prefix):
    lines = [line for line in raw.decode("ascii").splitlines() if line.startswith(prefix)]
    if len(lines) != 1:
        raise ValueError("Incomplete prior idle evidence")
    tokens = lines[0].split()
    if "=" not in tokens[0]:
        tokens = tokens[1:]
    pairs = [token.split("=", 1) for token in tokens]
    if any(len(pair) != 2 for pair in pairs) or len(dict(pairs)) != len(pairs):
        raise ValueError("Malformed prior idle evidence")
    return dict(pairs)


def pristine_stats(raw):
    """The reviewed image's pre-configuration state, not merely a reset frame count."""
    raw = raw.replace(b"\r\n", b"\n")
    if (
        not raw.endswith(b"\nDone\nl3dump:/>")
        or raw.splitlines().count(b"Done") != 1
        or b"Error" in raw
        or b"not recognized" in raw
    ):
        raise ValueError("Pristine stats reply is rejected or incomplete")
    capture = _fields(raw, "frames=")
    retention = _fields(raw, "iq8_packed=")
    trigger = _fields(raw, "trig phase=")
    ball = _fields(raw, "ball state=")
    zero_capture = "frames wraps active rf_faults hwa_frames hwa_out hwa_rearms hwa_rearm_err hwa_missed freeze_req freeze_done freeze_to loops".split()
    zero_retention = "iq8_packed iq8_overrun iq8_clipped pending pre_seen post_kept post_seen iq8_edma_done iq8_edma_err iq8_edma_wait".split()
    if (
        any(capture.get(key) != "0" for key in zero_capture)
        or any(retention.get(key) != "0" for key in zero_retention)
        or any(
            capture.get(key) != value
            for key, value in {
                "calib": "0x0",
                "format": "iq16",
                "plan": "0pre/16post",
                "used": "0/786432",
            }.items()
        )
        or retention.get("iq8_busy") != "0/0"
        or any(
            trigger.get(key) != value
            for key, value in {"phase": "off", "enabled": "0", "latched": "0"}.items()
        )
        or ball.get("state") != "off"
        or ball.get("follow") != "0"
        or ball.get("bin") != "0"
    ):
        raise ValueError("Runtime is not pristine, disabled, unlatched and never configured")
    return {"capture": capture, "retention": retention, "trigger": trigger, "ball": ball}


def inspect_prior(folder, *, manifest_sha256, capture_plan):
    """Validate saved bytes only; availability never asserts current device state."""
    folder = Path(folder)
    if manifest_sha256 != MANIFEST_SHA256:
        raise ValueError("Resume requires the reviewed freeze-safe package")
    if not re.fullmatch(r"[a-f0-9]{32}", folder.name):
        raise ValueError("Invalid prior session identity")
    if folder.is_symlink():
        raise ValueError("Prior session must be an owned directory")
    if any(folder.glob("attempt-*")) or (folder / "resume.json").exists():
        raise ValueError("Only the original zero-attempt session can resume")
    if (folder / "owner-restoration.json").exists():
        raise ValueError("Normal boot restoration was acknowledged; fresh RAM load required")
    if any((folder / name).is_symlink() for name in FILES):
        raise ValueError("Prior evidence must be regular session files")
    raw = {name: (folder / name).read_bytes() for name in FILES}
    state = json.loads(raw["status.json"])
    if (
        not isinstance(state, dict)
        or not isinstance(state.get("iwr_lifecycle"), dict)
        or not isinstance(state.get("iwr_capture_plan"), dict)
    ):
        raise ValueError("Incomplete prior session status")
    if (
        state.get("phase") != "stopped"
        or not state.get("phase_token")
        or state.get("attempts") != []
        or state.get("attempt_number") != 1
        or state.get("resources_released") is not True
        or state.get("normal_boot_restore_needed") is not True
        or state.get("iwr_lifecycle", {}).get("rf_start_attempted") is not False
    ):
        raise ValueError("Prior session must be stopped, released and never started RF")
    if state.get("iwr_capture_plan", {}).get("name") != capture_plan:
        raise ValueError("Resume cannot change the retained capture plan")
    commands = [json.loads(line) for line in raw["commands.jsonl"].splitlines()]
    if any(not isinstance(row, dict) for row in commands):
        raise ValueError("Malformed prior command row")
    if [row.get("command") for row in commands] != ["switches_set", "reset_pressed"] or any(
        not row.get("phase_token") for row in commands
    ):
        raise ValueError("Prior commands must contain only initial switch and RESET confirmations")
    if b"usb-result-freeze-fix" not in raw["startup.bin"] or not raw[
        "startup.bin"
    ].rstrip().endswith(b"l3dump:/>"):
        raise ValueError("Prior fresh RAM startup evidence missing")
    rows = json.loads(raw["iwr-exchanges.json"])
    if not isinstance(rows, list) or len(rows) != 2:
        raise ValueError("Exactly the initial identity and idle exchanges are required")
    if any(not isinstance(row, dict) or not isinstance(row.get("command"), str) for row in rows):
        raise ValueError("Malformed prior journal row")
    match = re.fullmatch(
        r"triggerLog clockProbe ([a-f0-9]{8}) ([a-f0-9]{8}) 00000000", rows[0]["command"]
    )
    if not match or rows[1]["command"] != "stats":
        raise ValueError("Prior journal is not a fresh zero-RF initialization")
    runtime = match[1] + match[2]
    if int(runtime, 16) == 0:
        raise ValueError("Invalid prior runtime")
    replies = []
    previous = -1
    for row in rows:
        if (
            not {
                "error",
                "before_ns",
                "write_return_ns",
                "cli_ready_ns",
                "after_ns",
                "written_bytes",
                "received_b64",
            }
            <= row.keys()
        ):
            raise ValueError("Incomplete prior journal row")
        stamps = [
            row.get(name) for name in ("before_ns", "write_return_ns", "cli_ready_ns", "after_ns")
        ]
        if (
            row.get("error") is not None
            or not all(type(stamp) is int for stamp in stamps)
            or stamps != sorted(stamps)
            or stamps[0] <= previous
            or row.get("written_bytes") != len(row["command"]) + 1
        ):
            raise ValueError("Prior exchange is partial or uncertain")
        previous = stamps[-1]
        reply = base64.b64decode(row["received_b64"], validate=True).replace(b"\r\n", b"\n")
        if not reply.endswith(b"\nDone\nl3dump:/>") or reply.splitlines().count(b"Done") != 1:
            raise ValueError("Prior exchange completion is uncertain")
        replies.append(reply)
    expected = (
        rf"OFCLKPROBE1 build={BUILD} request={runtime} boot={runtime} seq=0 tick_hz=200000000 counter_bits=32\n"
        rf"OFCLKSTAMP1 rx_tick=[0-9]+ tx_tick=[0-9]+ frame_seq=0 physical=unqualified\n"
        rf"OFCLKPROBEEND1 request={runtime} seq=0\nDone\nl3dump:/>"
    )
    if not re.fullmatch(expected, replies[0].decode("ascii")):
        raise ValueError("Prior fresh identity, reviewed build or zero frame counter differs")
    pristine_stats(replies[1])
    return {
        "prior_session_id": folder.name,
        "runtime": runtime,
        "build": BUILD,
        "manifest_sha256": manifest_sha256,
        "capture_plan": capture_plan,
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in raw.items()},
        "reused_clock_mapping": False,
    }
