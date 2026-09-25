# Offline commissioning report

The commissioning report summarizes evidence already saved by the live
pipeline. It does not own sensors, rerun estimators, or infer physical swings
that produced no sensor record.

Run it over one or more completed session logs:

```bash
uv run python scripts/analysis/commissioning_report.py \
  ~/openflight_sessions/session_*.jsonl \
  --output commissioning-report.json
```

Pass `--overwrite` only when replacing an existing report intentionally. Input
JSONL files are read once with byte and record limits, SHA-256 hashed, and never
used as output paths. A final line without a newline is treated as unfinished
and is not parsed. The CLI exits 0 for complete ended sessions, 3 for partial
evidence, and 2 for malformed inputs.

The report joins events only by the recorded session UUID and positive shot
number. Duplicate session starts are rejected. Repeated capture events for one
shot and stage are labeled ambiguous. Camera and IWR capture paths must still
exist as nonsymlink artifacts; camera folders must contain `frames.npz` and
`metadata.json`. This is an availability check, not content verification.
Missing events, recorded capture errors, and unavailable saved artifacts remain
separate states. Report output is also barred from every referenced artifact or
capture directory, including relative paths resolved beside the session file.

Recorded timing summaries include trigger latency, rolling-buffer trigger and
impact-transition timing, camera and IWR trigger deltas, IWR dump duration, and
saved `pipeline_ms` stages when present. Camera metadata exposes frame count,
delivered FPS, `gap_count`, and `max_interval_ms`. These values retain their
producer's clock semantics. The tool does not combine wall-clock timestamps into
a synthetic end-to-end latency or claim cross-clock validation.

`recorded_sensor_shots` is a count of shot-numbered log evidence. Trigger-event
counts and acquisition states help find transport and capture failures, but
they are not a physical swing denominator. Compare them with the independent
operator attempt ledger during commissioning; never reinterpret their
difference as a measured no-read rate without shot-level reconciliation.

This report has no built-in pass limits. It is a practical troubleshooting and
commissioning artifact for reviewing acquisition failures, dropped-frame
evidence, gaps, and latency before collecting a frozen held-out accuracy run.
