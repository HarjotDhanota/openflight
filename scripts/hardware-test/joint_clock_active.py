"""Bounded RF clock controls using the separately verified USB clock package."""

import base64
import math
from dataclasses import asdict

from clock_evidence import map_tick
from usb_collector import PROMPT, Collector, ExchangeError

CONFIGURATION = (
    "flushCfg",
    "dfeDataOutputMode 1",
    "channelCfg 15 7 0",
    "adcCfg 2 1",
    "profileCfg 0 60.0 7 3 38 0 0 100 1 128 4000 0 0 30",
    "chirpCfg 0 0 0 0 0 0 0 1",
    "chirpCfg 1 1 0 0 0 0 0 2",
    "chirpCfg 2 2 0 0 0 0 0 4",
    "frameCfg 0 2 12 2000 3 1 0",
    "captureFormat iq16",
    "phaseCaptureCfg 20 53 9 32 53 7 47 53 47 8 1",
    "lowPower 0 0",
    "debugCfg 0",
    "triggerCfg 32 1 1",
)
RATE_BOUNDS = (199_980_000, 200_020_000)
MAX_PROXIMITY_NS = 8_000_000_000


def fields_for(raw, prefix, required):
    """Parse exactly one status line, without accepting duplicate keys."""
    lines = [line for line in raw.decode("ascii").splitlines() if line.startswith(prefix)]
    if len(lines) != 1:
        raise ValueError(f"one {prefix!r} status record required")
    pairs = [token.split("=", 1) for token in lines[0].split() if "=" in token]
    if any(len(pair) != 2 or not all(pair) for pair in pairs) or len(
        {pair[0] for pair in pairs}
    ) != len(pairs):
        raise ValueError("malformed or duplicate status fields")
    values = dict(pairs)
    if not set(required) <= values.keys():
        raise ValueError("required status fields missing")
    return values


def counter_maps(observations, build, runtime):
    """Keep every probe; reject resets, ambiguous wraps and stopped frame counters."""
    if len(observations) < 2:
        raise ValueError("at least two probes required")
    for previous, current in zip(observations, observations[1:]):
        if current.before_ns <= previous.cli_after_ns:
            raise ValueError("clock exchanges overlap or host clock reversed")
        delta = (current.reply.rx_tick - previous.reply.rx_tick) % 2**32
        frame_delta = (current.reply.frame_seq - previous.reply.frame_seq) % 2**32
        if not 0 < delta < 2**31 or not 0 <= frame_delta < 2**31:
            raise ValueError("reset or ambiguous device counter")
    if observations[-1].reply.frame_seq == observations[0].reply.frame_seq:
        raise ValueError("no RF frame advance observed")
    return [
        map_tick(
            observations,
            observation.reply.rx_tick,
            target_epoch=(build, runtime),
            rate_hz_bounds=RATE_BOUNDS,
            max_distance_ns=MAX_PROXIMITY_NS,
        )
        for observation in observations
    ]


class ActiveCollector(Collector):
    """Separate active-mode lifecycle; the sealed idle collector stays unchanged."""

    def __init__(self, *args, sleep, **kwargs):
        super().__init__(*args, **kwargs)
        self.sleep = sleep
        self.state = {
            "rf_start_attempted": False,
            "stop_acknowledged": False,
            "stop_verified": False,
            "physical_timing_qualified": False,
        }

    def command(self, command):
        """Only the fixed finite profile and stop/start may change device state."""
        if self.failed:
            raise ExchangeError("uncertain connection; no recovery writes permitted")
        if command not in (*CONFIGURATION, "sensorStart", "sensorStop"):
            raise ValueError("command outside bounded RF clock scope")
        row = {
            "command": command,
            "before_ns": self.now(),
            "received_b64": "",
            "reads": [],
            "error": None,
        }
        self.rows.append(row)
        raw = bytearray()
        completions = 2 if command in ("debugCfg 0", "triggerCfg 32 1 1") else 1
        row["expected_completions"] = completions
        try:
            encoded = (command + "\n").encode("ascii")
            count = self.port.write(encoded)
            row["write_return_ns"], row["written_bytes"] = self.now(), count
            if type(count) is not int or count != len(encoded):
                raise ValueError("short lifecycle command write")
            timeout_ns = 20_000_000_000 if command == "sensorStart" else 10_000_000_000
            deadline = row["before_ns"] + timeout_ns
            while self.now() < deadline:
                raw.extend(self.port.read(min(max(1, self.port.in_waiting), 16_384 - len(raw))))
                stamp = self.now()
                row["reads"].append({"received_ns": stamp, "bytes_total": len(raw)})
                if stamp >= deadline:
                    raise TimeoutError("lifecycle deadline exceeded")
                if raw.endswith(PROMPT):
                    normalized = bytes(raw).replace(b"\r\n", b"\n")
                    normalized.decode("ascii")
                    lines = normalized[: -len(PROMPT)].splitlines()
                    if (
                        lines.count(b"Done") != completions
                        or not normalized.endswith(b"Done\n" * completions + PROMPT)
                        or b"Error" in normalized
                        or b"not recognized" in normalized
                    ):
                        raise ValueError("lifecycle command rejected or misframed")
                    row["cli_ready_ns"] = stamp
                    return bytes(raw)
                if len(raw) >= 16_384:
                    raise ValueError("lifecycle byte limit exceeded")
            raise TimeoutError("incomplete lifecycle reply")
        except (OSError, ValueError, TimeoutError) as error:
            self.failed, row["error"] = True, type(error).__name__
            raise ExchangeError(str(error)) from error
        finally:
            row["after_ns"] = self.now()
            row["received_b64"] = base64.b64encode(raw).decode()

    def status(self, *, active):
        raw, row = self.exchange("stats")
        try:
            status = fields_for(raw, "frames=", ("frames", "active", "rf_faults", "hwa_missed"))
            trigger = fields_for(raw, "trig phase=", ("latched", "enabled", "phase"))
            if (
                status["active"] != str(int(active))
                or status["rf_faults"] != "0"
                or status["hwa_missed"] != "0"
                or (active and (trigger["enabled"] != "1" or trigger["latched"] != "0"))
            ):
                raise ValueError("RF state, frame misses, fault or unexpected held capture")
            return {"capture": status, "trigger": trigger}
        except ValueError:
            row["error"] = "StatusQualificationError"
            raise

    def collect_active(self, *, count=24, pause_s=0.025, on_active=None, fresh=True):
        """No swing needed: finite RF frames and an enabled nominal tee detector."""
        if type(count) is not int or not 2 <= count <= 64:
            raise ValueError("probe count must be in 2..64")
        if (
            isinstance(pause_s, bool)
            or not isinstance(pause_s, (int, float))
            or not (math.isfinite(pause_s) and 0.005 <= pause_s <= 0.05)
        ):
            raise ValueError("bounded probe pause required")
        if type(fresh) is not bool or (not fresh and not self.state["stop_verified"]):
            raise ValueError("a repeated trial requires the previous verified stop")
        self.identity(initial=fresh)
        if fresh:
            self.idle()
        else:
            self.status(active=False)
        for command in CONFIGURATION:
            self.command(command)
        try:
            self.state["stop_acknowledged"] = self.state["stop_verified"] = False
            self.state["rf_start_attempted"] = True
            self.command("sensorStart")
            self.sleep(pause_s)
            before = self.status(active=True)
            if on_active is not None:
                on_active()
            observations = []
            deadline = self.now() + 4_000_000_000
            for _ in range(count):
                if self.now() >= deadline:
                    raise TimeoutError("active probe batch exceeded its window")
                observations.append(self.compact())
                self.sleep(pause_s)
            after = self.status(active=True)
            self.identity()
            maps = counter_maps(observations, self.build, self.context.boot)
            return {
                "schema": "openflight.iwr-active-clock-trial.v1",
                "host_boot": self.host_boot,
                "connection": self.connection,
                "build": self.build,
                "runtime": self.context.boot,
                "configuration": list(CONFIGURATION),
                "finite_rf_duration_ms_configured": 6000,
                "tee_bin": {"value": 32, "meaning": "nominal bench detector, not calibrated tee"},
                "before": before,
                "after": after,
                "clock_observations": [asdict(observation) for observation in observations],
                "conditional_maps": maps,
                "physical_timing_qualified": False,
            }
        finally:
            if not self.failed:
                self.command("sensorStop")
                self.state["stop_acknowledged"] = True
                self.status(active=False)
                self.state["stop_verified"] = True
