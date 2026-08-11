# Auto-Leveling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Set the launch monitor down on any slope and have it correct itself in software — both axes, no bubble level, no phone inclinometer, no CLI flag.

**Architecture:** A LIS3DH accelerometer (~$5) on the radar's mount plate, time-averaged, zeroed once at assembly against a digital angle gauge reading so the sensor's ±2.3° zero-g offset and the sensor-to-antenna rotation cancel together. Pitch feeds `Calibration.tilt_rad`. Roll is corrected per-estimator — a `cos φ` divisor on ball elevation, and a `θ_club·sin φ` subtraction on club azimuth using geometry the code already has. Design doc: `docs/plans/2026-07-28-leveling-sensor-design.md`.

**Not the corner-reflector calibration.** That file is another board's (`docs/iwr6843/README.md:432-437`), there is no reflector solver in this repo for the IWR6843, and the tilt-candidate sweep is documented as non-functional. Design doc §4 covers why and what replaces it.

**Tech Stack:** Python 3.10+, `smbus2` (new dep, linux-gated, shared with `feat/weather-sensor`), React 19 + Zustand + socket.io-client.

**Hardware status:** Not in hand. Slices 1-4 are implementable and testable against fakes. Slice 5 needs the part and gates Slice 7.

---

## Read this before starting

**Roll and pitch are different problems.** Roll costs almost nothing on launch angle (0.038° at 5° roll) and a lot on club path (1.22° at 5° roll, ≈5.5 yd offline). The design doc §3 explains why: the ball estimator measures elevation only, the club estimator measures azimuth only, and roll mixes them. **The club-path correction in Slice 6 is where the value is** — don't let it get deprioritised because it's the harder slice.

**Backward compatibility is the safety net.** Every formula must reduce exactly to today's behaviour at `roll = 0`, and calibration files without a `roll_deg` key must load with `roll_rad = 0.0`. If the existing test suite doesn't pass untouched after Slices 3 and 6, something is wrong.

**Slice 7 is conditionally blocked** on the drift measurement in Slice 5. Don't reorder it earlier.

---

## Slice ordering

| Slice | Demo | Needs HW |
|---|---|---|
| 1. Tilt math | Pure functions, round-trip tested against synthetic gravity | No |
| 2. Driver behind a fake bus | Full poll loop over a scripted I²C device | No |
| 3. Roll in the calibration + ball path | `roll_deg` in the cal file changes launch angle predictably | No |
| 4. Live tilt + setup view | Bubble level responds to mock tilt | No |
| 5. Bring-up + **drift characterisation** | Real angles; drift number recorded | **Yes** |
| 6. **Club-path roll correction** | Synthetic rolled shot recovers true path | No (verify on HW) |
| 7. Unsupervised correction + trust tiers | Set it down crooked, numbers come out right | Yes + Slice 5 verdict |

Slice 0 (shared `i2c.py`) is a prerequisite owned jointly with `feat/weather-sensor` — see §Slice 0.

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `src/openflight/i2c.py` | Shared bus open, `_load_smbus()` seam, chip-ID helper |
| Create | `src/openflight/leveling/__init__.py` | Exports |
| Create | `src/openflight/leveling/tilt.py` | Pure: gravity → pitch/roll, axis remap, vector averaging, stability gate |
| Create | `src/openflight/leveling/sensor.py` | `AccelerometerSensor`, LIS3DH + ADXL345 parts, poll thread |
| Create | `src/openflight/leveling/zero.py` | `ZeroReference` dataclass, load/save, bias application, staleness |
| Create | `config/leveling_zero.example.json` | Documented shape |
| Modify | `src/openflight/iwr6843/calibration.py` | `roll_rad` field; `load()` reads optional `roll_deg` → 0.0 |
| Modify | `src/openflight/iwr6843/calibration_session.py` | `clone_calibration(roll_deg=)`; log sensor pitch beside `tilt_candidate_deg` |
| Modify | `src/openflight/iwr6843/trajectory.py` | `cos(roll)` divisor in `_ground_xy` |
| Modify | `src/openflight/iwr6843/lcmf.py` | Roll in the two-ray geometry dict and direct/image angles |
| Modify | `src/openflight/iwr6843/multipath.py` | Same |
| Modify | `src/openflight/iwr6843/club.py` | `θ_club·sin(roll)` azimuth correction, roll-gated |
| Modify | `src/openflight/launch_monitor.py` | `Shot`: `mount_pitch_deg`, `mount_roll_deg`, `tilt_source` |
| Modify | `src/openflight/server.py` | `init_leveling()`, CLI, socket events, precedence, trust tiers |
| Modify | `src/openflight/session_logger.py` | Tilt fields in the optional block of `log_shot()` |
| Modify | `scripts/start-kiosk.sh` | `--leveling*` passthrough, `--leveling-zero` mode |
| Modify | `pyproject.toml` | `smbus2>=0.4.3; sys_platform == 'linux'` |
| Create | `tests/test_leveling_tilt.py`, `test_leveling_sensor.py`, `test_leveling_zero.py` | |
| Modify | `tests/test_iwr6843_pipeline.py`, `test_iwr6843_calibration_session.py`, `test_server.py` | Roll paths + backward compat |
| Create | `ui/src/stores/useTiltStore.ts`, `components/BubbleLevel.tsx` + `.css` + `.test.tsx`, `components/SetupView.tsx` | |
| Modify | `ui/src/App.tsx`, `services/socketService.ts`, `types/socket.ts`, `types/shot.ts` | |
| Modify | `docs/CHANGELOG.md`, `README.md` | |

---

## Slice 0 — Shared I²C module *(prerequisite, coordinate with `feat/weather-sensor`)*

- [ ] Create `src/openflight/i2c.py`: `_load_smbus()` with the comment `# Separated for testing.` mirroring `gpio_factory._load_gpiozero()` (`gpio_factory.py:66`); `RuntimeError` with an actionable "install via `uv sync`" message on `ImportError` (`gpio_factory.py:98-101`); `open_bus(bus_num)`; `read_chip_id(bus, addr, reg)`.
- [ ] Add `"smbus2>=0.4.3; sys_platform == 'linux'",` to `pyproject.toml` after `lgpio`. Run `uv sync`, commit `uv.lock`.
- [ ] `tests/test_i2c.py` with a `FakeSMBus` — the shared fake both branches reuse.
- [ ] **Whichever branch lands this first owns it.** The other rebases. Do not merge two copies.

---

## Slice 1 — Tilt math

*Demo: `uv run pytest tests/test_leveling_tilt.py -v`*

- [ ] `src/openflight/leveling/tilt.py`. Module docstring states the axis convention with a diagram comment **and states plainly that yaw is not observable from gravity** — that sentence saves the next reader an hour.
- [ ] `pitch_roll_from_gravity(ax, ay, az) -> (pitch_deg, roll_deg)`.
- [ ] `remap_axes(ax, ay, az, mapping)` — mounting orientation is config, not a hardcode. Support the six axis-swap/sign permutations that occur in practice.
- [ ] `average_gravity(samples)` — average the **vectors**, then convert. Averaging angles is wrong near wrap points; note the reason in a comment.
- [ ] `is_stable(samples) -> bool` — variance gate plus `|g|` within 1.0 ± 0.1. A non-unit vector means the unit is being carried and the reading is meaningless.
- [ ] Imports limited to `math`/`statistics`. No I/O, no logging.

### Tests
- [ ] Synthesise gravity from known (pitch, roll) pairs; assert round-trip to <0.01° across ±30° in both axes.
- [ ] `remap_axes` round-trips for every supported mapping.
- [ ] Vector averaging beats angle averaging on a set straddling a wrap point.
- [ ] Stability gate rejects a synthetic bump, accepts a quiet window, rejects a 1.5 g vector.
- [ ] **Pin the design-doc physics:** club-path error `|θ_club|·sin φ` at θ=14°, φ=5° is 1.22° ± 0.02°; launch-angle error `θ·(1−cos φ)` at θ=10°, φ=5° is 0.038° ± 0.002°. These two numbers justify the entire asymmetric design — if someone changes the model, these fail loudly.

---

## Slice 2 — Driver behind a fake bus

*Demo: `uv run pytest tests/test_leveling_sensor.py -v` drives connect → poll → average → stop.*

- [ ] Minimal part protocol: `whoami() -> int`, `configure()`, `read_acceleration_g() -> (x, y, z)`.
- [ ] `LIS3DH` — 0x18/0x19, `WHO_AM_I` 0x0F == 0x33, high-resolution mode (1 mg/LSB = 0.057°), ODR 50 Hz, ±2 g. **This is the shipped part** (Adafruit 2809, ~$5). Keep under ~50 lines.
- [ ] `ADXL345` — 0x53/0x1D, `DEVID` 0x00 == 0xE5, FULL_RES, ±2 g. Second implementation, mainly to prove the part seam works before it's needed for a real swap. Same size budget.
- [ ] Auto-detect by probing `WHO_AM_I` at both addresses for both parts; log which was found. Kills a whole class of "wrong address" support questions.
- [ ] `AccelerometerSensor` following `KLD7Tracker` (`kld7/tracker.py:119-869`): class-level attribute defaults (`:128-146`), flat named-kwarg `__init__`, `connect() -> bool` that logs and returns `False` rather than raising (`:211-268`), `start()`/`stop()` daemon thread with `join(timeout=5)` (`:300-316`), `[LEVEL]` log prefix.
- [ ] Poll ~50 Hz into a `deque`; `latest()` returns averaged pitch/roll + stability flag + sample count. **Never a raw single sample.**
- [ ] Survive transient bus `OSError` without killing the thread (cf. `kld7/tracker.py:67,332,348`).

### Tests
- [ ] LIS3DH detected; ADXL345 detected; neither → `connect()` returns `False`.
- [ ] `OSError` mid-poll → thread survives and keeps retrying.
- [ ] `latest()` before any successful read → `None`.
- [ ] Unstable window → stability flag false.
- [ ] `stop()` joins cleanly.

---

## Slice 3 — Roll enters the calibration and the ball path

*Demo: set `roll_deg` in the cal file, watch launch angle shift by the predicted (tiny) amount.*

- [ ] `Calibration` (`iwr6843/calibration.py:25-49`) gains `roll_rad: float = 0.0`. `load()` reads `raw.get("roll_deg", 0.0)`.
- [ ] `clone_calibration()` (`calibration_session.py:78-106`) accepts `roll_deg=` alongside the existing `tilt_deg=`.
- [ ] `trajectory.py:50` `_ground_xy` — divide elevation by `cos(cal.roll_rad)`.
- [ ] `lcmf.py:238-241` and the `geometry` dict at `:715` — carry `roll_rad`, apply in the direct/image angles.
- [ ] `multipath.py:88-102` — same.
- [ ] **Open question #4 from the design doc gets answered here:** read `lcmf.py`'s two-ray model closely first. If the direct/image geometry doesn't take a roll term cleanly, stop and re-scope rather than forcing it.

### Tests
- [ ] **Backward compatibility, non-negotiable:** a calibration file with no `roll_deg` key loads with `roll_rad == 0.0`, and the entire existing `tests/test_iwr6843_pipeline.py` passes untouched.
- [ ] Synthetic ball track at known launch angle with `roll_deg = 5` recovers the true angle to <0.05°.
- [ ] `Calibration.identity()` still has zero roll.

---

## Slice 4 — Live tilt and the setup view

*Demo: `./scripts/start-kiosk.sh --mock --leveling` shows a bubble responding to mock tilt.*

- [ ] `init_leveling(...) -> bool` modelled on `init_kld7()` (`server.py:1094-1179`): `global`, `try/except Exception`, `log_session_error(..., component="leveling")`, hardware import inside the function, `session_log.log_connection()` on success. Registered in `_cleanup_hardware_for_shutdown()` (`:151`).
- [ ] `@socketio.on("get_tilt")` mirroring `get_trigger_status` (`:1516`); ~10 Hz `socketio.emit("tilt", ...)` gated by a streaming flag (cf. camera gating `:1304-1332`); snapshot in the `connect` handler (`:1486-1507`).
- [ ] CLI: `--leveling`, `--leveling-i2c-bus`, `--leveling-i2c-addr`, plumbed through `scripts/start-kiosk.sh`.
- [ ] Mock tilt source on `MockLaunchMonitor` (`server.py:2676-2869`).
- [ ] `ui/src/stores/useTiltStore.ts`; listener in `socketService.setupListeners()` (`services/socketService.ts:32`); `TiltStatus` in `types/socket.ts`.
- [ ] `BubbleLevel.tsx` + `.css` + colocated `.test.tsx` — inline SVG, two-axis bubble, numeric readout, **deviation from calibrated pose not absolute angle**, and a distinct "no reference captured" state.
- [ ] `SetupView.tsx` + `'setup'` in the `View` union (`ui/src/App.tsx:33`) + nav icon.
- [ ] `Shot` fields `mount_pitch_deg`, `mount_roll_deg`, `tilt_source` → `shot_to_dict()` (`server.py:825-897`, round 0.01) → optional block of `log_shot()` (`session_logger.py:429-438`) → `ui/src/types/shot.ts`.
- [ ] `config["leveling"]` in `_session_start_config()` (`server.py:804-816`).
- [ ] **Regression test:** `--leveling` absent ⇒ shot output byte-identical to today.

---

## Slice 5 — Bring-up and drift characterisation *(needs parts; gates Slice 7)*

- [ ] Wire to Pi 5 I²C-1 per `docs/hardware-integration.md` §3 — SDA pin 3, SCL pin 5, 3.3 V pin 17, GND pin 9. Confirm with `i2cdetect -y 1`.
- [ ] Verify repeatability against known angles (sine bar or printed wedge). Expect several degrees of *absolute* error — that's the point of Slice 7's zeroing.
- [ ] **The gating measurement.** Read the part's zero-g offset temperature-drift spec; record the figure and datasheet revision in `docs/`. Then bench-soak: unit rigidly fixed, log pitch/roll across a ≥25 °C excursion, plot drift vs temperature.
- [ ] **Decision gate:**
  - **< 0.25° / 30 °C** → proceed to Slice 7 unchanged.
  - **0.25-1.0°** → proceed, and add the accelerometer's **on-die** temperature to the zero reference plus a delta warning. Die temperature, not ambient — it's what drives the drift, and only a relative reading is needed.
  - **> 1.0°** → wrong part. Ship Slices 1-6, escalate to ADXL355, record the decision in the design doc.
- [ ] Record the verdict and data in the PR's manual-testing section — `CONTRIBUTING.md` requires documented manual testing and "tests pass" is explicitly rejected.

---

## Slice 6 — Club-path roll correction *(the one that matters)*

*Demo: a synthetic club track generated with 5° roll recovers the true path to <0.2°.*

- [ ] Derive `θ_club ≈ atan2(h_club − radar_height_m, x_club) − pitch` inside `club.py`. `radar_height_m` is a `Calibration` property (`calibration.py:41-44`); `x_club` comes from the track's range fit (`club.py:255`).
- [ ] **Resolve design-doc open question #2 first:** use a fixed clubhead-height assumption, or derive it from `tee_ball_height_m` (`calibration.py:38`)? The latter is nearly free and better grounded — prefer it unless it proves awkward.
- [ ] Subtract `θ_club·sin(roll)` from `azimuth_rad` (`club.py:248`) **before** the Cartesian conversion at `:256-257`.
- [ ] **Gate on a roll threshold (~2°).** Below it the correction is smaller than the `h_club` assumption's own error — applying it adds noise. Threshold is a named constant with the design-doc reference in its comment.
- [ ] Pass pitch/roll into `club.py`'s entry point; keep the signature explicit rather than reaching for a global.

### Tests
- [ ] **Backward compatibility:** `roll = 0` reproduces today's `path_deg` exactly on the existing synthetic fixture (`club.py` references one dated 2026-07-25).
- [ ] Synthetic club track built with a known roll recovers true path to <0.2° at 5° roll.
- [ ] Below-threshold roll leaves azimuth untouched.
- [ ] `h_club` sensitivity: a ±0.13 m error in assumed clubhead height produces <0.3° residual at 2° roll.

---

## Slice 7 — Unsupervised correction and trust tiers *(gated by Slice 5)*

*Demo: set the unit down visibly crooked, hit a shot, get correct numbers without touching a flag.*

- [ ] `leveling/zero.py`: `ZeroReference(pitch_bias_deg, roll_bias_deg, measured_pitch_deg, measured_roll_deg, temperature_c, captured_at, calibration_source)`, `load()`/`save()`, `DEFAULT_ZERO_PATH = "config/leveling_zero.json"` — mirroring `calibration.py:22,51-63`. Plus `config/leveling_zero.example.json`. Store both the gauge readings and the derived biases so a bad zero is diagnosable after the fact.
- [ ] `--leveling-zero --pitch <deg> --roll <deg>` mode: the operator reads both angles off a digital angle gauge resting on the reference pad (design doc §7.2) and passes them in. The command takes a stable averaged accelerometer reading, stores the difference as the bias, prints a summary, exits. **Refuse loudly** on an unstable window or out-of-range `|g|` — a bad zero poisons everything downstream.
- [ ] `--roll` defaults to 0.0 so a builder who only has a single-axis gauge still gets the pitch correction, with a logged warning that roll is unreferenced.
- [ ] Print the resulting bias magnitudes. A bias beyond ~±5° means the sensor is mounted differently than the CAD intends — worth surfacing rather than silently storing.
- [ ] Staleness: record the calibration file the reference was captured alongside; warn if that file changes afterwards.
- [ ] Document the procedure in `README.md` as a build step, not a user step: assemble → gauge on the reference pad → one command → done.
- [ ] **Trust tiers** (design doc §9): ≤2° silent · 2-10° applied but surfaced for confirmation · >10°, no valid reference, or unstable ⇒ refuse and fall back with a loud warning.
- [ ] **Precedence, logged at INFO every startup:** explicit `--iwr6843-tilt-deg` > sensor-measured > calibration-file. Surface the resolved value and its source in `iwr6843_runtime_config` (`server.py:1054-1073`).
- [ ] Cross-check logging: sensor pitch beside `tilt_candidate_deg`/`tilt_candidate_score` (`calibration_session.py:149-150,175-176`), agreement in `CalibrationSummary.to_dict` (`:204-205`).
- [ ] Design-doc open question #5: the confirm tier **annotates** shots rather than blocking capture. Never stop someone mid-session.

### Tests
- [ ] Precedence order across all three sources.
- [ ] Refuses with no valid reference; clamp triggers on an absurd reading.
- [ ] Zero reference round-trips; bias application recovers the reference pose.
- [ ] Stale/mismatched reference detected.
- [ ] Unstable window refuses to write.

---

## Definition of done

- [ ] `uv run pytest tests/ -v` green — **including every pre-existing IWR6843 test, unmodified**
- [ ] `uv run pylint src/openflight/ --fail-under=9`
- [ ] `uv run ruff check src/openflight/` and `ruff format --check src/openflight/`
- [ ] `cd ui && npm run lint && npm run build`
- [ ] Slice 5 drift verdict recorded in the design doc
- [ ] `docs/CHANGELOG.md` `[Unreleased]` entry
- [ ] `README.md` documents wiring, the build-time zeroing step, and what auto-leveling does **and does not** cover (slope yes, aim no)
- [ ] PR title: `feat(leveling): auto-level pitch and roll from an accelerometer`
- [ ] PR body fills all three CI-checked sections: why it was required / automated tests / manual testing performed
