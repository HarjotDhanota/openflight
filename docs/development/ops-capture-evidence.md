# OPS capture evidence

The hardware-trigger reader retains bounded original bytes and host read
observations before parsing, clock synchronization and rearming. A capture ID
joins the `ops_capture_evidence` session entry to an accepted
`rolling_buffer_capture` entry. Rejected, cancelled and failed reads keep their
evidence too; a failed evidence callback does not replace the capture outcome.

The record includes the boot ID when available, arm-command host bounds,
pre-trigger segment setting, preamble/read observations, trigger-notification
lines, raw capture bytes and their SHA-256. Truncated evidence and malformed
notification lines carry explicit issues. Receipt and command timestamps are
host observations; they do not establish ADC sample time, impact time, device
acknowledgement or physical clock synchronization.

The retained preamble is capped at 65,536 bytes, raw capture at 262,144 bytes,
and preamble read observations at 1,024 entries. Full received-byte counts remain
available when storage is truncated. Original bytes are encoded as base64 in
the session record; the existing ASCII decoder and speed processing are unchanged.

This ports the runtime evidence portion of `fd02979e`. The separate GPIO sync
recorder and offline association tooling are outside this change. No new GPIO
access, firmware requirement, trigger selection or clock correction is enabled.
The existing serial read, clock synchronization and rearm ordering is retained.

Validation uses simulated serial chunks, malformed input, cancellation/error
paths, rearm context and session logging. Real same-capture GATE/sample
association and logging cost still need hardware validation.
