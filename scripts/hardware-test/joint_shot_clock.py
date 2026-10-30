"""One finite self-triggered IWR attempt on a fresh, identified RAM runtime."""

import hashlib
from copy import deepcopy
from dataclasses import asdict

from joint_clock_active import (
    CONFIGURATION,
    MAX_PROXIMITY_NS,
    RATE_BOUNDS,
    ActiveCollector,
    fields_for,
)

PROFILE = tuple(
    "frameCfg 0 2 12 20000 3 1 0" if row.startswith("frameCfg ") else row for row in CONFIGURATION
)
TRIGGER_COMMANDS = tuple(f"triggerCfg {index} 1 1" for index in range(20, 73))


class ShotCollector(ActiveCollector):
    """Fixed profile, observed ball lock, independent expected result identities."""

    LIFECYCLE_COMMANDS = (
        *PROFILE,
        "debugCfg 0",
        "ball cfg 1 1",
        "triggerCfg 32 1 0",
        *TRIGGER_COMMANDS,
        "sensorStart",
        "sensorStop",
    )
    DOUBLE_COMPLETION_COMMANDS = (
        "debugCfg 0",
        "ball cfg 1 1",
        "triggerCfg 32 1 0",
        *TRIGGER_COMMANDS,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.generation = 0
        self.next_shot = 1
        self.fresh_runtime = False
        self.observed_ball = None
        self.trigger_observation = None

    def establish_fresh_runtime(self):
        """Only the RAM loader's fresh startup may initialize generation tracking."""
        if self.context is not None or self.generation:
            raise ValueError("fresh runtime can only be established once")
        self.identity(initial=True)
        self.idle()
        self.fresh_runtime = True

    def exchange(self, *args, **kwargs):
        """An interrupted reply must never be followed by speculative cleanup writes."""
        try:
            return super().exchange(*args, **kwargs)
        except BaseException:
            self.failed = True
            raise

    def snapshot(self):
        """Retain state without interpreting a fitted crossing as sample time."""
        raw, _ = self.exchange("stats")
        capture = fields_for(raw, "frames=", ("frames", "active", "hwa_missed", "rf_faults"))
        trigger = fields_for(raw, "trig phase=", ("enabled", "latched", "phase"))
        ball = fields_for(raw, "ball state=", ("state", "follow", "bin"))
        if capture["hwa_missed"] != "0" or capture["rf_faults"] != "0":
            raise ValueError("IWR reports RF fault or missed frame")
        return {"capture": capture, "trigger": trigger, "ball": ball}

    def begin(self):
        """Start at most 60 nominal RF seconds; learn the empty lane before placement."""
        if not self.fresh_runtime or self.failed:
            raise ValueError("fresh identified runtime required")
        if self.generation and not self.state["stop_verified"]:
            raise ValueError("previous attempt has not stopped")
        for command in PROFILE:
            self.command(command)
        self.state.update(stop_verified=False, stop_acknowledged=False, rf_start_attempted=True)
        self.command("sensorStart")
        # The pinned image resets ResultClock only at sensorStart and dump/release rearm.
        self.generation += 1
        self.observed_ball = None
        self.trigger_observation = None
        self.command("debugCfg 0")
        self.command("triggerCfg 32 1 0")
        self.command("ball cfg 1 1")
        self.deadline_ns = self.now() + 50_000_000_000

    def wait_ball(self, publish, cancelled):
        """Use only this attempt's firmware ball placement evidence to choose the bin."""
        while self.now() < self.deadline_ns:
            if cancelled():
                raise InterruptedError("session stopped")
            state = self.snapshot()
            if state["capture"]["active"] != "1":
                raise ValueError("IWR stopped before ball placement")
            phase = state["ball"]["state"]
            publish("learning_background" if phase == "building" else "place_ball", state)
            if phase == "locked":
                index = int(state["ball"]["bin"])
                if state["ball"]["follow"] != "1" or not 20 <= index < 73:
                    raise ValueError("observed ball outside retained profile")
                self.observed_ball = (self.generation, index, state)
                return index, state
            self.sleep(0.1)
        raise TimeoutError("no stable IWR ball placement observed")

    def arm(self, index):
        """Only a bin observed by wait_ball may be enabled by the caller."""
        if (
            type(index) is not int
            or self.observed_ball is None
            or self.observed_ball[:2] != (self.generation, index)
        ):
            raise ValueError("observed tee bin outside profile")
        self.command(f"triggerCfg {index} 1 1")
        state = self.snapshot()
        if (
            state["capture"]["active"] != "1"
            or state["trigger"]["enabled"] != "1"
            or state["trigger"]["latched"] != "0"
        ):
            raise ValueError("IWR did not arm cleanly")
        return state

    def wait_result(self, publish, cancelled, *, on_trigger=None):
        """Poll within the finite RF budget; fetch a held result without forcing capture."""
        deadline = min(self.deadline_ns, self.now() + 30_000_000_000)
        while self.now() < deadline:
            if cancelled():
                raise InterruptedError("session stopped")
            state = self.snapshot()
            if state["trigger"]["latched"] == "1" and self.trigger_observation is None:
                rows = getattr(self, "rows", ())
                row = rows[-1] if rows else {}
                self.trigger_observation = {
                    "schema": "openflight.iwr-trigger-observation.v1",
                    "expected_shot_id": self.next_shot,
                    "expected_generation": self.generation,
                    "host_received_mono_ns": self.now(),
                    "exchange_index": len(rows) - 1 if rows else None,
                    "stats_exchange": {
                        key: row.get(key)
                        for key in ("before_ns", "write_return_ns", "cli_ready_ns")
                    },
                    "capture_active_in_status": state["capture"]["active"] == "1",
                    "device_timestamp": None,
                    "timestamp_meaning": "host observation of polled latch; not physical trigger or contact",
                    "physical_timing_qualified": False,
                }
                if on_trigger is not None:
                    on_trigger(deepcopy(self.trigger_observation))
            publish("armed", state)
            if state["trigger"]["latched"] == "1" and state["capture"]["active"] == "0":
                raw, _ = self.exchange("triggerLog result", done_count=2)
                if fields_for(raw, "result ", ("ready",))["ready"] != "1":
                    self.sleep(0.05)
                    continue
                held = self.collect_held_result(
                    expected_shot_id=self.next_shot,
                    expected_generation=self.generation,
                    rate_hz_bounds=RATE_BOUNDS,
                    max_distance_ns=MAX_PROXIMITY_NS,
                )
                self.next_shot += 1
                return held
            if state["capture"]["active"] != "1":
                raise ValueError("RF ended without a held trigger result")
            self.sleep(0.05)
        raise TimeoutError("no held IWR result during armed interval")

    def read_raw_dump(self, radar):
        """The existing dump reader borrows this same UART; it opens no second port."""
        if self.failed:
            raise ValueError("uncertain UART; raw dump not requested")
        try:
            raw, timing = radar.read_dump_timed(timeout_s=15)
            self.generation += 1
            return raw, {
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
                "transfer_timing": asdict(timing),
                "physical_timing_qualified": False,
            }
        except BaseException:
            self.failed = True
            raise

    def finish(self):
        """Stop and verify when framing is known; finite RF bounds uncertain cases."""
        if not self.failed and self.state["rf_start_attempted"]:
            self.command("sensorStop")
            self.state["stop_acknowledged"] = True
            self.status(active=False)
            self.state["stop_verified"] = True
