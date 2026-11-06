# Tester internal-trigger capture experiment

The tester can collect the native IWR self-trigger, native OPS speed trigger,
OPS sampling GPIO edges and camera clip during one operator-armed attempt.
This is an isolated capture mode. It does not publish production shot metrics.
Physical sub-ms synchronization and contact-time accuracy remain unvalidated.

Start the tester with `--internal-sync-clock-package PATH` pointing to the
reviewed freeze-safe shot-dump package (on the prepared Pi,
`/home/harjot-openflight/clock-packages/freeze/package-001`), then open
`/internal-sync.html`. The existing single tester job owner runs the capture
worker. Other capture jobs must be stopped first. The worker checks ownership
before asking for physical setup and never writes flash.

The page walks through RAM boot switches, opening Enhanced UART, RESET,
temporary upload, empty-lane background learning, ball placement and arming.
Switch/RESET confirmations are bound to the current session phase. The IWR
uses this attempt's observed ball bin within the retained profile, not a fixed
tee-distance assumption. Its RF run is finite (60 nominal seconds); placement
and arming share a 50-second host budget and the armed wait is at most 30 seconds.

The separate **Start ball-lock inspection** button is an explicit diagnostic
selection. Configure it with `--internal-sync-diagnostic-package PATH` pointing
to the separately allowlisted diagnostic package; the capture package does not
enable it. `POST /api/tester/internal-sync/start` accepts
`{"mode":"ball_lock_inspection"}`; the legacy empty payload still selects
capture. Status exposes immutable `mode`, `inspection_configured` and
`start_available`/`start_reason`. A running session never changes mode.
After a terminal session, a new explicit start requires released resources and
completed normal-boot restoration. Previous session files remain retained.

During inspection, do not swing. Begin with an empty lane and stay still for
the empty/still control, including after background learning. The first
host-polled lock stops the radar and saves detector evidence before OPS/camera
preparation. Timeout or Stop retains available evidence. A later deliberate
placement check can add a ball after background learning finishes; the software
does not verify either physical condition. This is a saved detector trace,
not verified ball presence, a captured shot, or a false-lock correction.
No normal three-activity plan or swing invitation appears in diagnostic mode.
No RESET is needed between inspections while the same loaded owner stays open.
Explicit Next/Retry keeps the existing safe-stop checks and creates a new raw
attempt directory; inspection does not advance planned shot slots. Diagnostic
Resume after closure is unsupported. End and restoration remain separate.

Inspection attempts report `diagnostic_saved_unqualified` or a failed/partial
inspection with the observed outcome (`host_observed_lock`, `timeout`, or
`operator_stop`). Downloads include only files observed on disk: `RESULT.json`,
`iwr-exchanges.json`, and `ball-evidence/reply.bin`, `exchange.json`,
`decoded.json`, `coverage.json`. Failed exports can retain only partial reply
bytes and exchange records. Missing links do not establish export completeness.
Coverage remains structural/session-bound until the pinned offline C analyzer
checks retained transitions; host receipt times are not sample or contact times.

An IWR lock is a sensor candidate, not independently verified ball presence.
A `locked` candidate whose reason is `gone` stays in the finite placement wait;
it cannot start sensor preparation. Before enable, a fresh snapshot must retain
the same healthy state/follow/bin and lock/release epoch/window. No numerical
confidence floor is added. After enable, a lost candidate gives sticky
do-not-swing guidance while the bounded result wait continues: departure can
precede a legitimate trigger. A later latch/notice still retains its result,
and reacquisition alone cannot invite another swing. These host guards do not
eliminate firmware false candidates in an empty lane or qualify detection.

The on-screen first-test guide plans one dry swing followed by two real strikes.
Remove the ball before Prepare, place it only when asked, and wait for Armed
before one swing within the 30-second armed budget. A dry-swing timeout retains
useful partial evidence. A partial/error strike pauses for review; after three
saved attempts Prepare stays disabled until the first two planned strikes are
reviewed outside this guide. Slots are planned activities, not verified labels.
The counter comes from the current server session, including after refresh.
No RESET is needed between normal attempts. Neither the stopped Next wait nor
the internal-sync parent job has a lifetime deadline. Each acquisition retains
its finite RF/placement/armed limits, and manual Stop remains available. Job
status reports timeout_s=null for no lifetime deadline; numeric values use
job.started_at. The guide retains the legacy one-hour fallback only when an
older runner omits timeout_s. Other timed tester jobs keep their deadlines.

An eligible retained session can offer Resume loaded session. This is an
explicit operator action, not a new RAM upload or normal-boot restoration.
The server decides availability and checks the existing loaded runtime before
showing Next; no swing or RF start follows Resume automatically. Keep power
and IWR USB connected and do not press RESET. A refusal preserves the evidence
and explains why Resume is unavailable. Do not start a new RAM load unsupervised
after Stop or failure; a new load requires physical switch/RESET access.

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
Each attempt result also retains full or partial unsolicited IWR notice replies
in `iwr_notices`, with raw base64 bytes, read times and errors. Its
`iwr_notice_start_index` maps local rows to the session-wide `notice_read_index`
in a trigger observation. Session cleanup and pause/retry checkpoints save the
complete journal as `iwr-notices.json`, including notices before the first attempt.
These timestamps describe host byte receipt, not physical trigger or contact time.
The page downloads these records without leaving the guide. “Captured” requires both radar captures and
one saved camera array with matching event ownership and frame clocks. It
does not establish that independent triggers selected the same physical event.

The IWR mapping is a conditional host-monotonic interval for frame ISR/output
ticks, under the package's declared UART/oscillator bounds. It is not an ADC
sample timestamp or contact timestamp. OPS GPIO is observed in the kernel's
monotonic clock; camera SensorTimestamp uses CLOCK_BOOTTIME, with the existing
runtime recording translation bounds. Do not align samples using the roughly
7 ms pulse discrepancy seen in the no-swing controls. No such correction is
applied here.

On the standalone internal-sync page, Stop attempt and End session are separate
controls. Stop attempt asks the loaded worker to pause at a safe exchange
boundary, retain the attempt files, and stop/verify RF; it does not kill the
worker or reload RAM. A serial transfer
already in progress must finish its bounded framing before cleanup. Keep power
and USB connected and do not press RESET while stopping. Button acknowledgement
is not a verified stop; wait for Paused or the final session status.

Retry is an explicit action on a paused, still-owned session. It is offered only
after fresh same-runtime checks prove a known stopped state without an
unconsumed trigger/result or other retained capture that a new start would
discard. Interrupted framing, uncertain stop, mismatched identities or a lost
worker refuse Retry. A stale status file alone cannot authorize it. Retry uses
a new attempt directory and preserves previous partial/error evidence; an
aborted setup keeps its planned activity slot while the raw attempt number
advances. It does not automatically repeat the swing or qualify the detector's
ball candidate.

End session requests final cleanup and closure of the loaded owner. Only after
resources are reported released does the page instruct normal switches
(S1.1 OFF, S1.2 OFF, S1.3 ON; leave S1.4/S1.5) and reconnecting only IWR USB.
The owner's acknowledgement is separate from independent firmware verification.
Never-attempted Resume is a different restricted path, not recovery for an old
ended session with RF activity or interrupted UART framing. A new experiment
session requires normal-boot restoration; none of these controls writes flash.
Do not start a full swing block until the first two ball-strike captures have
been inspected for matching events, impact coverage, clipping and complete radar
data. Software Retry checks do not establish USB-close reliability or physical
sensor accuracy/timing.

Software checks cover fixed finite commands, split/coalesced serial replies,
runtime/result identity, original-byte retention, Stop and ownership failures,
saved camera arrays, API phase checks and download boundaries. Browser checks
cover the wizard, drag versus tap and 1024×600, 800×400 and 800×480 layouts.
These checks do not constitute physical trigger or timing validation.

Run the standalone mocked browser checks with
`node --test ui/tests/internal-sync.browser.cjs` (the UI dependencies must be
available). They do not run a backend or contact devices. Status freshness,
pending/uncertain commands, the three-slot/partial-strike gates, terminal jobs,
reported/legacy timeouts, explicit Resume and physical confirmations are included. A command is never
treated as a phase advance before the server reports it; stale/offline status
disables controls. Stop remains available while a Next command awaits advance
and status is current.

Inspection browser checks additionally cover explicit starts after retained
terminal sessions, package unavailability, malformed armed/owner states,
initial offline/stalled status, safe Retry, partial file availability and
mouse-drag/touch-tap at all three kiosk sizes. These are local fake-browser
checks, not device, touchscreen, detector or hardware timing validation.
