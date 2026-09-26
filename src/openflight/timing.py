"""Versioned, duration-only timing provenance for live shot processing."""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from typing import Callable

SCHEMA_NAME = "openflight.shot_stage_timing"
SCHEMA_VERSION = 1


def unavailable_stage(start_event: str, end_event: str, reason: str) -> dict:
    """Describe a duration that this process cannot truthfully measure."""
    return {
        "status": "unavailable",
        "duration_ns": None,
        "start_event": start_event,
        "end_event": end_event,
        "reason": reason,
    }


def measured_stage(
    start_ns: int,
    end_ns: int,
    start_event: str,
    end_event: str,
    **qualifiers: object,
) -> dict:
    """Describe one non-negative duration in the host monotonic clock domain."""
    if int(end_ns) < int(start_ns):
        return unavailable_stage(start_event, end_event, "monotonic_boundary_order_invalid")
    stage = {
        "status": "measured",
        "duration_ns": int(end_ns) - int(start_ns),
        "start_event": start_event,
        "end_event": end_event,
    }
    stage.update(qualifiers)
    return stage


def new_stage_timing() -> dict:
    """Return a complete contract whose unmeasured stages are explicit."""
    return {
        "schema": SCHEMA_NAME,
        "version": SCHEMA_VERSION,
        "clock": {
            "domain": "host_monotonic",
            "source": "time.monotonic_ns",
            "unit": "ns",
        },
        "ops": {
            "blocking_operation": unavailable_stage(
                "trigger_wait_started",
                "trigger_strategy_returned",
                "trigger_strategy_boundaries_not_observed",
            ),
            "trigger_observation_wait": unavailable_stage(
                "trigger_wait_started",
                "first_valid_dump_marker_observed",
                "first_valid_dump_marker_not_observed",
            ),
            "physical_trigger_to_observation": unavailable_stage(
                "physical_trigger_edge",
                "first_valid_dump_marker_observed",
                "physical_trigger_edge_not_observed_by_host",
            ),
            "acquisition_window": unavailable_stage(
                "radar_capture_window_started",
                "radar_capture_window_completed",
                "no_independent_host_capture_window_boundaries",
            ),
            "uart_transport": unavailable_stage(
                "first_valid_dump_marker_observed",
                "full_dump_response_received",
                "dump_response_boundaries_not_observed",
            ),
            "post_transport_processing": unavailable_stage(
                "full_dump_response_received",
                "trigger_strategy_completed",
                "trigger_strategy_completion_not_observed",
            ),
            "analysis": unavailable_stage(
                "capture_analysis_started",
                "capture_analysis_completed",
                "capture_analysis_not_observed",
            ),
        },
        "iwr6843": {
            "capture_wait": unavailable_stage(
                "iwr_process_shot_started",
                "iwr_capture_returned",
                "iwr_capture_wait_not_observed",
            ),
            "uart_transport": unavailable_stage(
                "iwr_dump_started",
                "iwr_dump_completed",
                "iwr_dump_boundaries_not_observed",
            ),
            "estimator_analysis": unavailable_stage(
                "iwr_estimator_started",
                "iwr_estimator_completed",
                "iwr_estimator_not_observed",
            ),
            "aggregate": unavailable_stage(
                "iwr_process_shot_started",
                "iwr_process_shot_completed",
                "iwr_process_shot_not_observed",
            ),
        },
        "server": {
            "publication": unavailable_stage(
                "shot_callback_started",
                "server_websocket_emit_invoked",
                "server_publication_not_observed",
            )
        },
        "browser": {
            "receive": unavailable_stage(
                "server_websocket_emit_invoked",
                "browser_message_received",
                "browser_receive_not_instrumented",
            ),
            "paint": unavailable_stage(
                "browser_message_received",
                "browser_shot_painted",
                "browser_paint_not_instrumented",
            ),
        },
        "legacy_fields": {},
    }


def clone_stage_timing(value: object | None) -> dict:
    """Copy a valid contract, or create a fresh one for legacy callers."""
    if isinstance(value, dict) and value.get("schema") == SCHEMA_NAME:
        return copy.deepcopy(value)
    return new_stage_timing()


def set_legacy_provenance(
    timing: dict,
    field_name: str,
    *,
    semantics: str,
    clock_domain: str,
    ambiguity: str,
) -> None:
    """Document a preserved legacy number without changing its serialized value."""
    timing.setdefault("legacy_fields", {})[field_name] = {
        "semantics": semantics,
        "clock_domain": clock_domain,
        "ambiguity": ambiguity,
    }


@dataclass
class OpsCaptureTimer:
    """Capture host-observable OPS boundaries with one monotonic clock."""

    wait_started_ns: int
    first_marker_ns: int | None = None
    response_completed_ns: int | None = None

    @classmethod
    def start(cls) -> "OpsCaptureTimer":
        """Start an OPS capture timer at the current host monotonic instant."""
        return cls(wait_started_ns=time.monotonic_ns())

    def first_marker_callback(self, callback: Callable[[], None] | None = None) -> None:
        """Record the first valid dump marker before forwarding UI feedback."""
        if self.first_marker_ns is None:
            self.first_marker_ns = time.monotonic_ns()
        if callback is not None:
            callback()

    def response_completed(self) -> None:
        """Record return of the complete UART dump response."""
        self.response_completed_ns = time.monotonic_ns()

    def ops_section(self, strategy_completed_ns: int | None = None) -> dict:
        """Build the OPS portion of the timing contract."""
        section = new_stage_timing()["ops"]
        if self.first_marker_ns is not None:
            section["trigger_observation_wait"] = measured_stage(
                self.wait_started_ns,
                self.first_marker_ns,
                "trigger_wait_started",
                "first_valid_dump_marker_observed",
                includes_golfer_idle=True,
                interpretation="host_wait_to_observation_not_physical_edge_latency",
            )
        if self.first_marker_ns is not None and self.response_completed_ns is not None:
            section["uart_transport"] = measured_stage(
                self.first_marker_ns,
                self.response_completed_ns,
                "first_valid_dump_marker_observed",
                "full_dump_response_received",
            )
        if self.response_completed_ns is not None and strategy_completed_ns is not None:
            section["post_transport_processing"] = measured_stage(
                self.response_completed_ns,
                strategy_completed_ns,
                "full_dump_response_received",
                "trigger_strategy_completed",
            )
        return section
