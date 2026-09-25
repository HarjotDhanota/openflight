from __future__ import annotations

import numpy as np
import pytest

from openflight.camera.timing_validation import analyze_timing

CONTEXT = "a" * 64


def capture(capture_id, split, sensor, host, source_hash=None, context=CONTEXT):
    return {
        "id": capture_id,
        "split": split,
        "sensor_timestamp_ns": np.asarray(sensor, dtype=np.int64),
        "host_timestamp_ns": np.asarray(host, dtype=np.int64),
        "context_sha256": context,
        "clock_domain_id": "pi-boot-2026-09-24T08:00Z",
        "source_sha256": source_hash or (capture_id.encode().hex().ljust(64, "0")[:64]),
    }


def synthetic(epoch=8_000_000_000_000_000, slope=1.000025, offset=70_000):
    sensor = epoch + np.arange(12, dtype=np.int64) * 2_000_000
    host = epoch + offset + np.rint((sensor - epoch) * slope).astype(np.int64)
    return sensor, host


def test_large_epoch_fit_is_invariant_and_validation_is_held_out():
    fit_sensor, fit_host = synthetic(epoch=2**53 + 8_000_000_000)
    validation_sensor = fit_sensor[-1] + np.arange(1, 13, dtype=np.int64) * 2_000_000
    validation_host = (
        fit_host[0]
        + np.rint((validation_sensor - fit_sensor[0]) * 1.000025).astype(np.int64)
        + 4_000
    )
    result = analyze_timing(
        [
            capture("fit", "fit", fit_sensor, fit_host),
            capture("held-out", "validation", validation_sensor, validation_host),
        ]
    )

    assert result["mapping"]["drift_ppm"] == pytest.approx(25.0, abs=0.1)
    assert result["fit"]["max_abs_ns"] < 1.0
    assert result["validation"]["signed_mean_ns"] == pytest.approx(4_000, abs=1)
    shifted_fit_sensor = fit_sensor + 3_000_000_000_000_000
    shifted_fit_host = fit_host + 3_000_000_000_000_000
    shifted_validation_sensor = validation_sensor + 3_000_000_000_000_000
    shifted_validation_host = validation_host + 3_000_000_000_000_000
    shifted = analyze_timing(
        [
            capture("fit", "fit", shifted_fit_sensor, shifted_fit_host),
            capture("held-out", "validation", shifted_validation_sensor, shifted_validation_host),
        ]
    )
    assert shifted["fit"] == pytest.approx(result["fit"])
    assert shifted["validation"] == pytest.approx(result["validation"])
    assert result["sensor_timestamp_semantics"].startswith("unverified")
    assert result["promotion"] == "prohibited"


def test_independent_event_is_bounded_and_incomplete_evidence_never_passes():
    sensor, host = synthetic()
    result = analyze_timing(
        [
            capture("fit", "fit", sensor[:6], host[:6]),
            capture("hold", "validation", sensor[6:], host[6:]),
        ],
        [
            {
                "id": "complete",
                "capture_id": "hold",
                "optical_frame_interval": [1, 2],
                "independent_host_timestamp_ns": int(host[7]),
                "uncertainty_ns": 100,
                "provenance": "LED transition and contact logger",
            },
            {"id": "missing", "capture_id": "hold"},
            {
                "id": "overflow",
                "capture_id": "hold",
                "optical_frame_interval": [1, 2],
                "independent_host_timestamp_ns": 10**1000,
                "uncertainty_ns": 0,
                "provenance": "invalid logger",
            },
        ],
    )

    complete, incomplete, overflow = result["independent_events"]
    assert complete["status"] == "evaluated"
    assert complete["interval_separation_ns"] == 0
    assert complete["passes_threshold"] is None
    assert incomplete["status"] == "incomplete"
    assert overflow["status"] == "incomplete"
    assert "signed 64-bit" in overflow["reason"]
    assert result["independent_evidence_status"] == "incomplete"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda items: items[1].update(context_sha256="b" * 64), "mixed"),
        (lambda items: items[1].update(clock_domain_id="another-boot"), "clock domains"),
        (
            lambda items: items[1].update(source_sha256=items[0]["source_sha256"]),
            "duplicate capture",
        ),
        (
            lambda items: items[0].update(sensor_timestamp_ns=np.array([1, 2, 2], dtype=np.int64)),
            "strictly increasing",
        ),
    ],
)
def test_invalid_identity_or_timestamps_are_rejected(mutation, message):
    sensor, host = synthetic()
    items = [capture("fit", "fit", sensor, host), capture("hold", "validation", sensor, host)]
    mutation(items)
    with pytest.raises(ValueError, match=message):
        analyze_timing(items)
