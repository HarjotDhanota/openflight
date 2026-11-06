# Experimental net-impact recording

This profile records a longer radar window for investigating automatic net
distance from ball deceleration or rebound. It is a dedicated calibration
experiment. It has not been validated on hardware and does not detect a net,
update setup, or qualify launch angles.

Use the standalone collector below. Keep the normal kiosk launch profile and
the existing calculated ball distances. The net profile keeps full IQ16
precision and the same RF range scale, but reduces each frame from 12 loops
to 4. Its launch-angle performance has not been established.

| Property | Normal wide launch profile | Net experiment |
|---|---:|---:|
| Saved frames | 24 | 48 |
| Frame spacing | 3 ms | 5 ms |
| Nominal recording span | 72 ms | 240 ms |
| Pre-request history | 27 ms | 45 ms |
| Post-request frame allocation | 45 ms | 195 ms |
| Loops per frame | 12 | 4 |
| Stored bins per frame | 53 | 84 |
| IQ representation | IQ16 | IQ16 |
| L3 sample payload | 732,672 bytes | 774,144 bytes |
| Late apparent range, bin centres | 2.203–4.641 m | 2.063–5.953 m |

The firmware arena has 786,432 bytes; the new payload leaves 12,288 bytes.
All 39 post-request frames are retained without stride gaps. Capture timing
must still be checked against firmware evidence and health counters. A
planned descriptor is not proof that the hardware met that cadence.

At roughly 45–97 mph, eight feet of travel takes about 56–121 ms before
allowing for angle and deceleration. The longer window leaves room to inspect
what follows arrival, provided the trigger, range coverage, and ball returns
are usable. It does not guarantee an impact observation for slower shots.
The radial return at impact also depends on the ball's height and lateral
position. A slant distance is not automatically a net-plane forward distance.

The unchanged RF profile spans about six metres. For example, a monitor 2.2 m
behind the ball and a net 13 ft beyond it puts the net plane about 6.16 m
forward, beyond this profile even before allowing for launch height. A return
at a saved-window edge cannot establish net distance. Wider-range RF changes
would need separate calibration and validation.

## Collect on the Pi

Use the existing configurable-capture firmware; no new firmware image is
provided. Confirm that it supports 3 TX, 128 ADC samples, configurable even
loop counts, and IQ16. An older fixed-geometry build must reject this profile.
Record the installed release's declared SHA-256; the collector cannot read
the flashed image back to verify it.

Stop the kiosk through its usual launcher or service before running the
collector, so the collector owns the radar and sound-trigger GPIO. From this
checkout on the Pi, first collect five transport checks without hitting balls:

```bash
uv run python scripts/iwr6843/feasibility_run.py \
  --profile net \
  --config config/iwr6843_net_calibration_48f5ms_84bin_iq16.cfg \
  --out-root ~/openflight-net-calibration \
  --image-sha256 <installed-release-sha256> \
  --port /dev/ttyUSB0 --soak 5
```

Check the capture plan, saved headers, and health counters described below.
Once those pass, collect the real-ball attempts and clap controls:

```bash
uv run python scripts/iwr6843/feasibility_run.py \
  --profile net \
  --config config/iwr6843_net_calibration_48f5ms_84bin_iq16.cfg \
  --out-root ~/openflight-net-calibration \
  --image-sha256 <installed-release-sha256> \
  --port /dev/ttyUSB0 \
  --soak 0 --real-ball 5 --claps 2 \
  --placement-label net-calibration --placement-m <measured-monitor-to-ball-m> \
  --note "Net impact experiment; record measured ball-to-net distance separately as grading truth"
```

The placement value is logged for grading and does not configure the tee
range or feed an estimator. Do not replace it with a placement default. The
script waits for a ready ring, prints READY for each triggered attempt, and
retains each outcome, including failed and missing captures. Let every dump
complete before another attempt. At 1,041,667 baud the sample payload alone
takes about 7.43 seconds at full 8N1 wire rate; calibration collection is not
a live-shot latency demonstration.

The `sensorStart` reply should declare:

```text
Capture plan: loops=4 pre=9x84@5000us impact=7x84@5000us ball=32x84@5000us frames=48 bytes=774144/786432 starts=20/44/44/44 stride=1
```

Verify all of the following in the saved artifacts before interpreting them:

- 48 frames, 12 chirps per frame, 3 TX, 4 RX, 84 bins, IQ16.
- 5,000 µs frame spacing and a complete payload declared by its own header.
- Nine pre-request windows starting at bin 20; 39 later windows at bin 44.
- Complete dump/CLI recovery, no new RF faults, HWA misses, or rearm errors.
- No freeze timeout: the firmware still uses its existing 250 BIOS-tick wait;
  confirm the installed build's tick period and that the 195 ms post phase fits.
- Where available, associated frame evidence agrees with the planned order
  and timing. Keep missing timing evidence explicitly unobserved.

If a smoke capture fails, retain the failure artifacts and resolve it before
ball collection. The collector ends with sensor stop and keeps its config
copy, responses, raw dumps, hashes, and counters in a new run directory.
Resume the kiosk with its original wide profile after collection.

## What would establish a net candidate

Inspect the ungated outgoing ball returns and a subsequent sharp slowdown or
rebound, with enough later observations to distinguish it from losing the
ball. The production tracker's endpoint is not independent evidence: it
already uses the configured net distance minus a 25 cm margin.

Reject candidate events at recording/window boundaries, competing tracks,
or signal loss without observed slowing. Net flex means the deepest stopping
point can lie beyond the resting net plane. Keep an apparent or corrected
slant-range candidate separate from any reconstructed forward plane distance.
Tape measurements and video may grade detections; they must not seed them.

No automatic net detector or setup handoff is implemented by this change.
The next analysis needs the new real recordings, including negative controls.
