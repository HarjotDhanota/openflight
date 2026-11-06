"""One finite self-triggered IWR attempt on a fresh, identified RAM runtime."""

import base64
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
from usb_collector import ExchangeError

from openflight.camera import iwr_capture_plan
from openflight.camera.sync_resume import pristine_stats

# The reviewed image queues this line the moment it latches (l3_dump.c "Triggered").
NOTICE = b"Triggered\n"
NOTICE_CRLF = b"Triggered\r\n"
NOTICE_COMPLETION_NS = 50_000_000

PROFILE = tuple(
    "frameCfg 0 2 12 20000 3 1 0" if row.startswith("frameCfg ") else row for row in CONFIGURATION
)


def shot_profile(plan=iwr_capture_plan.DEFAULT):
    """The shot profile with the named plan's phaseCaptureCfg; the default is PROFILE."""
    return iwr_capture_plan.commands(PROFILE, plan)


def trigger_commands(plan=iwr_capture_plan.DEFAULT):
    """A ball can lock and arm only in the bins the plan's pre phase retains."""
    return tuple(f"triggerCfg {index} 1 1" for index in iwr_capture_plan.pre_bins(plan))


def lifecycle_commands(plan=iwr_capture_plan.DEFAULT):
    """Every line the collector may send under the plan, and those completing twice."""
    triggers = trigger_commands(plan)
    double = ("debugCfg 0", "ball cfg 1 1", "triggerCfg 32 1 0", "ball status", *triggers)
    return (*shot_profile(plan), *double, "sensorStart", "sensorStop"), double


TRIGGER_COMMANDS = trigger_commands()


class CandidateUnavailable(ValueError):
    """Known candidate loss before enable; retry still requires a verified safe stop."""


class ShotCollector(ActiveCollector):
    """Fixed profile, observed ball lock, independent expected result identities.

    The class attributes are the default plan's; an instance accepts exactly the
    lines of the plan it was built with.
    """

    LIFECYCLE_COMMANDS, DOUBLE_COMPLETION_COMMANDS = lifecycle_commands()
    profile = PROFILE
    trigger_bins = iwr_capture_plan.pre_bins(iwr_capture_plan.DEFAULT)
    capture_plan = iwr_capture_plan.record(iwr_capture_plan.DEFAULT, PROFILE)

    def __init__(self, *args, capture_plan=iwr_capture_plan.DEFAULT, **kwargs):
        profile = shot_profile(capture_plan)
        self.LIFECYCLE_COMMANDS, self.DOUBLE_COMPLETION_COMMANDS = lifecycle_commands(capture_plan)
        self.profile = profile
        self.trigger_bins = iwr_capture_plan.pre_bins(capture_plan)
        self.capture_plan = iwr_capture_plan.record(capture_plan, profile)
        super().__init__(*args, **kwargs)
        self.generation = 0
        self.next_shot = 1
        self.fresh_runtime = False
        self.observed_ball = None
        self.candidate_loss = None
        self._pending_trigger_callback = False
        self.trigger_observation = None
        self.notice_rows = []
        self.command_boundary = None
        self._ball_status_count = 0
        self._ball_status_signature = None

    def establish_fresh_runtime(self):
        """Only the RAM loader's fresh startup may initialize generation tracking."""
        if self.context is not None or self.generation:
            raise ValueError("fresh runtime can only be established once")
        self.identity(initial=True)
        self.idle()
        self.fresh_runtime = True

    def attach_zero_attempt_runtime(self, expected_runtime):
        """Use only after authenticating the original never-started-RF journal."""
        if self.context is not None or self.generation or self.fresh_runtime or self.failed:
            raise ValueError("zero-attempt attachment requires a new collector")
        original_nonce = self.nonce

        def distinct_nonce():
            token = original_nonce()
            if token == expected_runtime:
                raise ValueError("resume request must not re-seed the prior runtime after a reset")
            return token

        self.nonce = distinct_nonce

        def pristine():
            raw, _ = self.exchange("stats")
            return pristine_stats(raw)

        try:
            self.identity(initial=False)
            if self.context.boot != expected_runtime or self.context.frame_seq != 0:
                raise ValueError("existing runtime differs or RF frames have occurred")
            before = pristine()
            raw, _ = self.exchange("triggerLog result", done_count=2)
            result = fields_for(raw, "result ", ("shot", "ready"))
            if result["ready"] != "0" or result["shot"] != "0":
                raise ValueError("held or previous result present; resume cannot discard it")
            self.identity()
            if self.context.frame_seq != 0:
                raise ValueError("RF frames occurred during resume verification")
            after = pristine()
            self.fresh_runtime = True
            return {
                "before": before,
                "after": after,
                "reused_clock_mapping": False,
                "read_only": True,
            }
        except BaseException:
            self.failed = True
            raise
        finally:
            self.nonce = original_nonce

    def exchange(self, *args, **kwargs):
        """An interrupted reply must never be followed by speculative cleanup writes."""
        if self.command_boundary is not None:
            self.command_boundary()
        try:
            return super().exchange(*args, **kwargs)
        except BaseException:
            self.failed = True
            raise

    def command(self, command):
        """Honor cooperative controls before sending, never in an incomplete reply."""
        if self.command_boundary is not None:
            self.command_boundary()
        return super().command(command)

    def snapshot(self):
        """Retain state without interpreting a fitted crossing as sample time."""
        raw, _ = self.exchange("stats")
        capture = fields_for(raw, "frames=", ("frames", "active", "hwa_missed", "rf_faults"))
        trigger = fields_for(raw, "trig phase=", ("enabled", "latched", "phase"))
        ball = fields_for(
            raw, "ball state=", ("state", "follow", "bin", "reason", "locks", "releases", "window")
        )
        if capture["hwa_missed"] != "0" or capture["rf_faults"] != "0":
            raise ValueError("IWR reports RF fault or missed frame")
        return {"capture": capture, "trigger": trigger, "ball": ball}

    def begin(self):
        """Start at most 60 nominal RF seconds; learn the empty lane before placement."""
        if not self.fresh_runtime or self.failed:
            raise ValueError("fresh identified runtime required")
        if self.generation and not self.state["stop_verified"]:
            raise ValueError("previous attempt has not stopped")
        for command in self.profile:
            self.command(command)
        self.state.update(stop_verified=False, stop_acknowledged=False, rf_start_attempted=True)
        self.command("sensorStart")
        # The pinned image resets ResultClock only at sensorStart and dump/release rearm.
        self.generation += 1
        self.observed_ball = None
        self.candidate_loss = None
        self._pending_trigger_callback = False
        self.trigger_observation = None
        self._ball_status_count = 0
        self._ball_status_signature = None
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
            stats_index = len(self.rows) - 1
            publish("learning_background" if phase == "building" else "place_ball", state)
            self._retain_ball_status(state, stats_index, cancelled)
            if phase == "locked":
                if state["ball"]["reason"] == "gone":
                    self.sleep(0.1)
                    continue
                index = int(state["ball"]["bin"])
                self._require_candidate(state, index)
                self.observed_ball = (self.generation, index, state)
                return index, state
            self.sleep(0.1)
        raise TimeoutError("no stable IWR ball placement observed")

    def _retain_ball_status(self, state, stats_index, cancelled):
        """Keep bounded debug replies beside their preceding, non-atomic stats."""
        if cancelled():
            raise InterruptedError("session stopped")
        if state["trigger"]["enabled"] != "0" or state["trigger"]["latched"] != "0":
            return
        signature = tuple(
            state["ball"][key]
            for key in ("state", "follow", "bin", "reason", "locks", "releases", "window")
        )
        if self._ball_status_count >= 16 or signature == self._ball_status_signature:
            return
        self._ball_status_count += 1
        self._ball_status_signature = signature
        row_index = len(self.rows)
        try:
            self.command("ball status")
        finally:
            if len(self.rows) > row_index:
                self.rows[row_index]["ball_status_context"] = {
                    "preceding_stats_exchange_index": stats_index,
                    "preceding_stats_state": deepcopy(state),
                    "non_atomic": True,
                    "timestamp_meaning": "Host status intervals; state may change between replies.",
                    "physical_timing_qualified": False,
                }

    def _require_candidate(self, state, index, previous=None):
        """Require current firmware support, without inventing a confidence threshold."""
        ball = state["ball"]
        if index not in self.trigger_bins:
            raise ValueError("observed ball outside retained profile")
        if (
            ball["state"] not in {"off", "building", "waiting", "candidate", "locked"}
            or ball["follow"] not in {"0", "1"}
            or ball["reason"] not in {"none", "no_delta", "too_wide", "unstable", "gone"}
            or (ball["state"] == "locked" and ball["reason"] not in {"none", "gone"})
        ):
            raise ValueError("invalid IWR ball candidate state contract")
        if (
            ball["state"] != "locked"
            or ball["follow"] != "1"
            or ball["reason"] != "none"
            or int(ball["bin"]) != index
        ):
            raise CandidateUnavailable("IWR ball candidate is not a current healthy lock")
        if previous is not None and any(
            ball[key] != previous[key] for key in ("locks", "releases", "window")
        ):
            raise CandidateUnavailable("IWR ball candidate lock epoch or window changed")

    def _note_candidate_loss(self, state):
        """After enable, loss quiets guidance but cannot rule out a pending launch."""
        if self.candidate_loss is not None:
            return
        try:
            if self.observed_ball is None:
                raise ValueError("IWR ball candidate observation missing")
            self._require_candidate(state, self.observed_ball[1], self.observed_ball[2]["ball"])
        except ValueError as error:
            self.candidate_loss = {
                "reason": str(error),
                "ball": deepcopy(state["ball"]),
                "host_observed_mono_ns": self.now(),
                "physical_timing_qualified": False,
            }

    def arm(self, index):
        """Only a bin observed by wait_ball may be enabled by the caller."""
        if (
            type(index) is not int
            or self.observed_ball is None
            or self.observed_ball[:2] != (self.generation, index)
        ):
            raise ValueError("observed tee bin outside profile")
        state = self.snapshot()
        self._require_candidate(state, index, self.observed_ball[2]["ball"])
        if (
            state["capture"]["active"] != "1"
            or state["trigger"]["latched"] != "0"
            or state["trigger"]["enabled"] != "0"
        ):
            raise ValueError("IWR not ready before arming")
        self.command(f"triggerCfg {index} 1 1")
        state = self.snapshot()
        if state["trigger"]["latched"] == "1":
            self._observe_trigger(state, "polled_latch", self.now(), None)
            self._pending_trigger_callback = True
            return state
        if state["capture"]["active"] != "1" or state["trigger"]["enabled"] != "1":
            raise ValueError("IWR did not arm cleanly")
        self._note_candidate_loss(state)
        return state

    def wait_notice(self, budget_s):
        """Between exchanges, read only the image's unsolicited Triggered line.

        Returns the host receipt of its first byte, or None if nothing arrived in
        the budget. Any other byte leaves the connection uncertain: no more writes.
        """
        if self.failed:
            raise ExchangeError("uncertain connection; no recovery writes permitted")
        row = {"before_ns": self.now(), "reads": [], "notice_ns": None, "error": None}
        self.notice_rows.append(row)
        raw = bytearray()
        deadline = row["before_ns"] + round(budget_s * 1e9)
        try:
            while True:
                line = bytes(raw)
                if line in (NOTICE, NOTICE_CRLF):
                    return row["notice_ns"]
                if not (NOTICE.startswith(line) or NOTICE_CRLF.startswith(line)):
                    raise ValueError("unexpected unsolicited IWR output")
                now = self.now()
                if not raw and now >= deadline:
                    return None
                if raw and now >= row["notice_ns"] + NOTICE_COMPLETION_NS:
                    raise TimeoutError("incomplete unsolicited IWR line")
                chunk = self.port.read(max(1, self.port.in_waiting))
                if chunk:
                    stamp = self.now()
                    if not raw:
                        row["notice_ns"] = stamp
                    raw.extend(chunk)
                    row["reads"].append({"received_ns": stamp, "bytes_total": len(raw)})
        except (OSError, ValueError, TimeoutError) as error:
            self.failed = True
            row["error"] = type(error).__name__
            raise ExchangeError(str(error)) from error
        except BaseException as error:
            self.failed = True
            row["error"] = type(error).__name__
            raise
        finally:
            row["after_ns"] = self.now()
            row["received_b64"] = base64.b64encode(raw).decode("ascii")

    def _observe_trigger(self, state, source, received_ns, on_trigger):
        rows = getattr(self, "rows", ())
        row = rows[-1] if rows else {}
        self.trigger_observation = {
            "schema": "openflight.iwr-trigger-observation.v1",
            "notice_source": source,
            "expected_shot_id": self.next_shot,
            "expected_generation": self.generation,
            "host_received_mono_ns": received_ns,
            "exchange_index": len(rows) - 1 if rows else None,
            "notice_read_index": len(self.notice_rows) - 1 if source == "triggered_line" else None,
            "stats_exchange": {
                key: row.get(key) for key in ("before_ns", "write_return_ns", "cli_ready_ns")
            },
            "capture_active_in_status": state["capture"]["active"] == "1",
            "device_timestamp": None,
            "timestamp_meaning": "host receipt of the IWR notice or polled latch; "
            "not physical trigger or contact",
            "physical_timing_qualified": False,
        }
        if on_trigger is not None:
            on_trigger(deepcopy(self.trigger_observation))

    def wait_result(self, publish, cancelled, *, on_trigger=None):
        """Poll within the finite RF budget; fetch a held result without forcing capture.

        Between polls the image's Triggered line is awaited, so the first notice is
        not held back by the poll interval; a polled latch still notifies without it.
        """
        deadline = min(self.deadline_ns, self.now() + 30_000_000_000)
        if self._pending_trigger_callback:
            if cancelled():
                raise InterruptedError("session stopped")
            self._pending_trigger_callback = False
            if on_trigger is not None:
                on_trigger(deepcopy(self.trigger_observation))
        state = None
        while self.now() < deadline:
            if cancelled():
                raise InterruptedError("session stopped")
            state = self.snapshot()
            if state["trigger"]["latched"] == "1" and self.trigger_observation is None:
                self._observe_trigger(state, "polled_latch", self.now(), on_trigger)
            if self.trigger_observation is None:
                self._note_candidate_loss(state)
            publish(
                "candidate_lost"
                if self.candidate_loss and self.trigger_observation is None
                else "armed",
                {
                    **state,
                    "candidate_loss": self.candidate_loss,
                    "trigger_observed": self.trigger_observation is not None,
                },
            )
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
            if self.trigger_observation is None:
                received = self.wait_notice(0.05)
                if received is not None:
                    self._observe_trigger(state, "triggered_line", received, on_trigger)
            else:
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
