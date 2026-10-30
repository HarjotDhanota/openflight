# Tester internal-trigger capture experiment

The tester can collect the native IWR self-trigger, native OPS speed trigger,
OPS sampling GPIO edges and camera clip during one operator-armed attempt.
This is an isolated capture mode. It does not publish production shot metrics.
Physical sub-ms synchronization and contact-time accuracy remain unvalidated.

Start the tester with `--internal-sync-clock-package PATH` pointing to the
reviewed sealed `usb-result-clock-test-20261027/package-001` package, then open
`/internal-sync.html`. The existing single tester job owner runs the capture
worker. Other capture jobs must be stopped first. The worker checks ownership
before asking for physical setup and never writes flash.

The page walks through RAM boot switches, opening Enhanced UART, RESET,
temporary upload, empty-lane background learning, ball placement and arming.
Switch/RESET confirmations are bound to the current session phase. The IWR
uses this attempt's observed ball bin within the retained profile, not a fixed
tee-distance assumption. Its RF run is finite (60 nominal seconds); placement
and arming share a 50-second host budget and the armed wait is at most 30 seconds.
If preparation times out, remove the ball and start the next attempt.

OPS v1.3.3 runs native speed triggering at 25 mph, magnitude 25, 30 ksps,
six pre-trigger segments, with P3 acknowledged. GPIO27 records its sampling
signal and requests the camera clip. The camera requests 640×400 at 300 fps,
300 µs, gain 4, 200 ms prehistory and 150 ms tail. Saved metadata reports actual
delivery and timing. There is no sound GPIO listener or IWR forced trigger.
This does not assert that a still-connected sound wire is electrically isolated.

Each attempt retains original IWR result bytes and CRC-bound clock sidecar
before the raw dump is retrieved over the same leased UART. Fresh runtime,
shot ID and capture generation are checked independently. The pinned image
advances generation at sensorStart and successful dump rearm; shot IDs advance
only when a result is built. Interrupted or misframed exchanges end acquisition
without speculative recovery writes. Partial captures and dry swings are kept.
For a dry swing, place the ball normally and swing without striking it.

Saved files include `iwr-held.json`, `iwr-result.bin`, `iwr-clock.bin`,
`iwr-dump.bin`, the original UART journals, OPS capture evidence, GPIO edges,
camera frame clocks, `frames.npz`, metadata and per-attempt `RESULT.json`.
The page links these records. “Captured” requires both radar captures and
one saved camera array with matching event ownership and frame clocks. It
does not establish that independent triggers selected the same physical event.

The IWR mapping is a conditional host-monotonic interval for frame ISR/output
ticks, under the package's declared UART/oscillator bounds. It is not an ADC
sample timestamp or contact timestamp. OPS GPIO is observed in the kernel's
monotonic clock; camera SensorTimestamp uses CLOCK_BOOTTIME, with the existing
runtime recording translation bounds. Do not align samples using the roughly
7 ms pulse discrepancy seen in the no-swing controls. No such correction is
applied here.

Stop retains evidence and checks released UART/camera ownership. The page
then instructs normal switches (S1.1 OFF, S1.2 OFF, S1.3 ON; leave S1.4/S1.5)
and reconnecting only IWR USB. The owner's acknowledgement is recorded
separately from independent firmware verification. A new experiment session
requires this restoration acknowledgement. Do not start a full swing block
until the first two ball-strike captures have been inspected for matching
events, impact coverage, clipping and complete radar data.

Software checks cover fixed finite commands, split/coalesced serial replies,
runtime/result identity, original-byte retention, Stop and ownership failures,
saved camera arrays, API phase checks and download boundaries. Browser checks
cover the wizard, drag versus tap and 1024×600, 800×400 and 800×480 layouts.
These checks do not constitute physical trigger or timing validation.
