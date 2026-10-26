# Temperature-request isolation diagnostic

`L3_DUMP_SKIP_TEMPERATURE` is an opt-in research build flag, disabled by
default. It requires stage tracing and capture evidence, and cannot be combined
with two-round transfer. Its only purpose is to compare a full capture that
calls the temperature service with one that does not. It is not a production
fix, timeout implementation, clock calibration or physical accuracy claim.

The full-dump path normally freezes capture, stops RF, requests temperature,
then writes the binary header. An observed trace stopped at `temp_enter`
without a header. That observation alone cannot distinguish a blocked service
from lost output. In the retained TI SDK, the link command takes a global mutex
with an indefinite wait, and its semaphore-wait callback discards the wait's
result. Neither the configured acknowledgement timeout nor a longer host read
window establishes a bound on that whole service call.

With isolation enabled, the dump emits `temp_skipped` with status -1, makes no
temperature-service call, selects the existing evidence-without-temperature
version 9, and writes no temperature extension. It retains the normal frame
evidence, IQ stream and trailer. Missing temperature must remain unavailable;
any correction requiring it must remain unavailable too. No placeholder or
cached temperature is substituted. Capture-start, stop and other TI calls
still use the unchanged SDK and can still block.

Build the ordinary diagnostic and isolation variant from the same source,
geometry and toolchain, with distinct source fingerprints. Retain the exact
flags, image hashes and captured original bytes. Prefer a temporary RAM boot
and restore normal saved firmware afterward. Never silently retry, pad a
short dump, or interpret a valid CRC as proof of correct physical sampling.
The default build must remain byte-identical to its reference when this flag
is absent.

Software checks execute the actual pre-header C path against simulated SDK
calls. Isolation tests abort if the temperature service is called or any
temperature extension is written; they require the existing no-temperature
version and normal stop-before-header ordering. They do not prove hardware
repeatability. A controlled hardware comparison must still establish complete
bytes/CRC, associated frame evidence, and clean stop/restart across cycles.
