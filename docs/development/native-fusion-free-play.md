# Native triggers in fusion free play

This experimental capture option belongs to the normal tester setup page,
**Start free play**, and the live metrics kiosk. It requires OPS243-A v1.3.3,
its J3 pin 1 sampling output wired to BCM27, the reviewed IWR RAM clock package,
and the existing measured setup and calibration. It has not been physically
qualified for same-event matching or sub-millisecond acquisition timing.

Start the tester with `--internal-sync-clock-package /path/to/package-001`.
Complete normal setup, choose the session resolution and club, select
**Use IWR + OPS native triggers**, then press **Start free play**. The tester
page provides the RAM switch and RESET instructions. Follow each displayed
instruction before confirming it. No flash write is performed.

Once the normal kiosk opens, remove the ball and press **Prepare capture**.
The system first observes the empty lane, then asks for ball placement and
waits for a stable address observation. Make one swing only after **Armed**.
Each radar triggers independently. OPS's native sampling edge retains the
camera's rolling buffer. No sound GPIO listener or forced IWR capture command
is used by this path. Repeat the empty-lane preparation for another attempt.

The existing rolling-buffer processor, calibrated IWR runtime, camera analysis,
metric display, shot logging, and **Stop & package** remain the calculation and
review path. Stop retains original OPS bytes, IWR result/clock/raw bytes,
GPIO records, frame timestamps, and association decisions. Partial attempts and
dry-swing evidence are saved even when normal shot processing emits no shot.
Speed training remains its existing separate mode.

A fusion candidate requires exact owned camera/OPS event identity, one complete
native pulse, and overlap between the conditional IWR frame-clock interval and
the actual saved camera timestamps. This is a software association check, not
independent proof of physical simultaneity. Unpaired OPS captures still reach
the ordinary speed processor, without borrowing camera or IWR data. No absolute
impact time is invented from UART receipt or the sampling pulse. Timing records
retain `physical_timing_qualified: false`.

After stopping, follow the tester's normal-boot restoration instructions and
confirm reconnection before starting another hardware action. The warning
survives a tester restart. Native startup waits for the physical confirmations;
shutdown joins the acquisition owner before releasing the shared UART.

For an isolated Pi stage using an already tested environment, set
`UV_PROJECT_ENVIRONMENT` to that environment and `OPENFLIGHT_UV_RUN_ARGS=--no-sync`.
Only the explicitly selected native test path then skips environment rebuilding;
the launcher verifies its required imports first. Stage a built `ui/dist` and
a real Git checkout so setup provenance and the normal launch checks work.
