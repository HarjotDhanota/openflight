# Weather Sensor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded `AIR_DENSITY_STD = 1.225` assumption with measured air density from a BME280, correcting a systematic carry error of up to ~14 yd (driver, Denver) and ~6 yd (35 °C day at sea level).

**Architecture:** Background-polled I²C sensor → pure humid-air density function → `simulate(air_density=ρ)` on the ballistics path and a per-club power-law factor on the table-fallback path. Provenance-tagged fallback chain (sensor → Open-Meteo → ISA default). Design doc: `docs/plans/2026-07-28-weather-sensor-design.md`.

**Tech Stack:** Python 3.10+, `smbus2` (new dep, linux-gated), stdlib `urllib.request` for Open-Meteo (no new HTTP dep), React 19 + Zustand + socket.io-client on the UI side.

**Hardware status:** Not in hand. Slices 1-3 and 5-6 are fully implementable and testable against fakes. Slice 4 is the only one that needs the part.

---

## Slice ordering rationale

Each slice is independently demoable and leaves the repo green. Slice 1 alone already delivers most of the accuracy win with a manual flag — if the hardware never arrives, the project is still better off.

| Slice | Demo | Needs HW |
|---|---|---|
| 1. Density math + manual override | `--weather-density 0.97` visibly changes carry | No |
| 2. Provider + fallback chain + mock | `--mock` shows plausible ambient conditions | No |
| 3. BME280 driver behind a fake | Unit tests drive a fake I²C bus end-to-end | No |
| 4. Hardware bring-up | Real readings on a Pi | **Yes** |
| 5. Live UI panel | Conditions visible and updating on screen | No |
| 6. Open-Meteo fallback | Density without a sensor, network-sourced | No |

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `src/openflight/environment/__init__.py` | Package exports |
| Create | `src/openflight/environment/density.py` | Pure humid-air density + carry factor |
| Create | `src/openflight/environment/sensor.py` | `BME280Sensor` driver, poll thread, `_load_smbus()` seam |
| Create | `src/openflight/environment/openmeteo.py` | Keyless HTTP fallback, TTL cache |
| Create | `src/openflight/environment/provider.py` | `EnvironmentProvider`, fallback chain, provenance |
| Create | `scripts/derive_density_exponents.py` | Regenerates the per-club k table from `simulate()` |
| Modify | `src/openflight/launch_monitor.py` | `Shot` env fields; density-aware `estimated_carry_yards` |
| Modify | `src/openflight/ballistics.py` | `CLUB_DENSITY_EXPONENT` table next to `CLUB_TYPICAL_SPIN_RPM` |
| Modify | `src/openflight/server.py` | `init_weather()`, CLI flags, shot-path wiring, socket events |
| Modify | `src/openflight/session_logger.py` | Env fields in the optional block of `log_shot()` |
| Modify | `scripts/start-kiosk.sh` | `--weather*` flag passthrough |
| Modify | `pyproject.toml` | `smbus2>=0.4.3; sys_platform == 'linux'` |
| Create | `tests/test_environment_density.py` | Density math vs published values |
| Create | `tests/test_environment_sensor.py` | Driver against `FakeSMBus` |
| Create | `tests/test_environment_provider.py` | Fallback chain + provenance |
| Create | `tests/test_environment_openmeteo.py` | Parsing, TTL, failure modes |
| Modify | `tests/test_ballistics.py` | Density-scaled carry, exponent-table residuals |
| Modify | `tests/test_server.py` | Wiring, `--weather` absent ⇒ unchanged behaviour |
| Create | `ui/src/stores/useEnvironmentStore.ts` | Live conditions store |
| Create | `ui/src/components/EnvironmentPanel.tsx` + `.css` + `.test.tsx` | Display |
| Modify | `ui/src/services/socketService.ts`, `ui/src/types/socket.ts`, `ui/src/types/shot.ts` | Transport + types |
| Modify | `docs/CHANGELOG.md`, `README.md` | `[Unreleased]` entry, setup + Open-Meteo attribution |

---

## Slice 1 — Density math and manual override

*Demo: `./scripts/start-kiosk.sh --mock --ballistics --weather-density 0.97` and watch driver carry move ~+14 yd.*

### Task 1.1: Pure density module

- [ ] Create `src/openflight/environment/density.py` with a module docstring in house style (state the model, cite Buck 1981, note the ~0.1% accuracy claim and why CIPM-2007 is deliberately not used).
- [ ] `saturation_vapour_pressure_pa(temp_c: float) -> float` — Buck equation.
- [ ] `air_density(temp_c: float, pressure_pa: float, humidity_pct: float) -> float` — partial-pressure model, `R_d = 287.058`, `R_v = 461.495`.
- [ ] Validate inputs: raise `ValueError` on `pressure_pa <= 0`, `temp_c < -80 or > 80`, `humidity_pct` outside 0-100 (clamp RH rather than raise — a sensor reading 100.3% is normal).
- [ ] No imports beyond `math`. No logging. No I/O.

### Task 1.2: Density math tests

- [ ] Create `tests/test_environment_density.py`, `class TestAirDensity`.
- [ ] Assert `air_density(15, 101325, 0) == pytest.approx(1.225, abs=0.001)` — the ISA anchor.
- [ ] Assert monotonicity: density falls with temperature, rises with pressure, falls with humidity.
- [ ] Assert the humidity magnitude claim from the design doc: 0→100% RH at 25 °C is between −1.0% and −1.4%.
- [ ] Assert Denver-ish (25 °C, 83500 Pa, 40%) lands at ~0.970 ±0.005.
- [ ] Assert `ValueError` on nonsense pressure/temperature; assert RH 100.5 clamps rather than raises.

### Task 1.3: Per-club density exponent table

- [ ] Create `scripts/derive_density_exponents.py` — sweeps `simulate()` over ρ ratios 0.80-1.15 for one representative launch condition per `ClubType`, least-squares fits `carry ∝ (ρ/ρ_std)^-k`, prints a paste-ready dict. Checked in so the table is reproducible, not magic.
- [ ] Add `CLUB_DENSITY_EXPONENT: dict[ClubType, float]` to `src/openflight/ballistics.py` immediately after `CLUB_TYPICAL_SPIN_RPM` (`:70-92`), with a comment pointing at the generator script.
- [ ] Add `density_carry_factor(club: ClubType, air_density: float) -> float` returning `(air_density / AIR_DENSITY_STD) ** -k`, defaulting to k = 0.30 for unknown clubs.

### Task 1.4: Wire density into both carry paths

- [ ] `src/openflight/server.py:2368` — change `simulate(conditions)` to `simulate(conditions, air_density=<resolved density>)`. For this slice the resolved density comes from a new module global set by `--weather-density`; the provider replaces it in Slice 2.
- [ ] `src/openflight/launch_monitor.py:324-335` — make `Shot.estimated_carry_yards` apply `density_carry_factor(...)` when `self.air_density_kg_m3` is set. Keep the property pure and total: no sensor knowledge, no None-explosions.
- [ ] `src/openflight/server.py:2388` — apply the same factor to the `estimate_carry_with_spin` fallback.
- [ ] Add `air_density_kg_m3: Optional[float] = None` and `air_density_source: Optional[str] = None` to the `Shot` dataclass (`launch_monitor.py:198-378`) with docstring entries matching the existing attribute-doc style.
- [ ] Add `--weather-density FLOAT` to `server.py:main()` (manual override / bench tool), plumbed through `scripts/start-kiosk.sh`.

### Task 1.5: Carry-path tests

- [ ] Extend `tests/test_ballistics.py`: `class TestDensityScaling` — driver carry at ρ = 0.97 exceeds carry at ρ = 1.225 by 12-16 yd; carry is monotonically decreasing in density for all club types.
- [ ] Assert the exponent table's residual against the integrator is < 1.0 yd for every `ClubType` over ρ ratio 0.85-1.10. **This test is the guard on the whole fallback-path approximation** — if someone retunes the aero coefficients, it fails loudly.
- [ ] Assert `Shot.estimated_carry_yards` is unchanged when `air_density_kg_m3 is None`.

---

## Slice 2 — Provider, fallback chain, mock

*Demo: `--mock` reports plausible ambient conditions with `air_density_source == "mock"`; no sensor ⇒ `"default"` and today's exact numbers.*

### Task 2.1: Reading type and provider

- [ ] Create `src/openflight/environment/provider.py` with `@dataclass EnvironmentReading(temp_c, pressure_pa, humidity_pct, air_density_kg_m3, source, timestamp)`.
- [ ] `EnvironmentProvider.current() -> EnvironmentReading` implementing the chain: fresh sensor reading (< 60 s) → cached Open-Meteo (< 15 min) → ISA default. Never raises. Never blocks on I/O.
- [ ] Staleness thresholds are named module constants with comments, not literals.

### Task 2.2: Server wiring

- [ ] Add `init_weather(...) -> bool` to `server.py`, modelled line-for-line on `init_kld7()` (`:1094-1179`): `global`, `try/except Exception`, `log_session_error(..., component="weather", ...)`, hardware import *inside* the function, `session_log.log_connection(device="bme280", ...)` on success.
- [ ] Call it from `main()` alongside the other init calls (`:3370-3452`), guarded by `--weather`.
- [ ] Register the provider's `stop()` in `_cleanup_hardware_for_shutdown()` (`server.py:151`).
- [ ] In `on_shot_detected`, snapshot `provider.current()` into the `Shot` **before** the carry block (`server.py:2364-2401`). Snapshot only — no I²C in the shot path.
- [ ] Add `config["environment"]` to `_session_start_config()` (`server.py:804-816`).
- [ ] Give `MockLaunchMonitor` (`server.py:2676-2869`) a mock environment source so `--mock` demos the full path.

### Task 2.3: Shot record propagation

- [ ] Add `air_temp_c`, `air_pressure_hpa`, `humidity_pct` to `Shot` (`launch_monitor.py:198-378`).
- [ ] Add all five env fields to `shot_to_dict()` (`server.py:825-897`) with sensible rounding (temp 0.1, pressure 0.1, RH 0, density 0.0001).
- [ ] Add them to the conditional optional-field block in `SessionLogger.log_shot()` (`session_logger.py:429-438`).
- [ ] Add matching fields to `ui/src/types/shot.ts`.

### Task 2.4: Provider tests

- [ ] Create `tests/test_environment_provider.py` with a locally-defined `FakeSensor` (house convention: `Fake*` classes, `monkeypatch` injection — see `tests/test_kld7.py:274`).
- [ ] Cover: sensor fresh → `"bme280"`; sensor stale → falls through; sensor raising → falls through and does not propagate; no sources → `"default"` with ρ exactly `AIR_DENSITY_STD`.
- [ ] **Regression test: with `--weather` absent, `on_shot_detected` produces byte-identical carry numbers to today.** Add to `tests/test_server.py`.

---

## Slice 3 — BME280 driver behind a fake bus

*Demo: `uv run pytest tests/test_environment_sensor.py -v` drives the full driver against a synthetic I²C device.*

### Task 3.1: Dependency and import seam

- [ ] Add `"smbus2>=0.4.3; sys_platform == 'linux'",` to `pyproject.toml` `dependencies` (after `lgpio`), run `uv sync`, commit `uv.lock`.
- [ ] In `src/openflight/environment/sensor.py`, add `_load_smbus()` with the comment `# Separated for testing.` — mirror `gpio_factory._load_gpiozero()` (`gpio_factory.py:66`). Raise `RuntimeError` with an actionable "install via `uv sync`" message on `ImportError`, per `gpio_factory.py:98-101`.

### Task 3.2: Bosch compensation

- [ ] Implement calibration-register read (0x88-0xA1, 0xE1-0xE7) and the fixed-point compensation from Bosch BME280 datasheet §4.2.3 — `compensate_temperature`, `compensate_pressure`, `compensate_humidity` as module-level pure functions taking raw ADC values + calibration struct.
- [ ] **Test them against the datasheet's worked example** before wiring any bus code. This is the piece most likely to be subtly wrong and it is fully testable with zero hardware.

### Task 3.3: Driver class

- [ ] `BME280Sensor` following `KLD7Tracker` (`kld7/tracker.py:119-869`): class-level attribute defaults (`:128-146`), flat named-kwarg `__init__`, `connect() -> bool` that logs and returns `False` rather than raising (`:211-268`), `start()`/`stop()` daemon thread with `join(timeout=5)` (`:300-316`), lock-guarded latest reading, `[BME280]` log prefix.
- [ ] Chip-ID check (`0xD0` must read `0x60`); a BMP280 reports `0x58` — detect it, log a clear "no humidity channel" warning, and continue with RH = 50% assumed rather than failing. Cheap robustness, and BMP280 boards are widely mislabelled as BME280.
- [ ] **Forced mode, not normal mode.** Trigger one measurement per poll interval and sleep between. Oversampling ×1 T/H, ×4 P, IIR filter off. This is the self-heating mitigation from the design doc — it is not optional and the reason belongs in a code comment.
- [ ] Apply `temp_offset_c` after compensation; record it in the reading so logs show whether a trim was in play.

### Task 3.4: Driver tests

- [ ] Create `tests/test_environment_sensor.py` with `class FakeSMBus` exposing `read_byte_data` / `read_i2c_block_data` / `write_byte_data` over a scripted register map.
- [ ] Cover: successful read cycle; wrong chip ID; `OSError` from the bus mid-poll (thread must survive and keep retrying); `stop()` joins cleanly; `latest()` before any successful read returns `None`.

---

## Slice 4 — Hardware bring-up *(blocked on parts)*

*Demo: real readings on the Pi, cross-checked against a reference.*

### Task 4.1: Bench validation

- [ ] Wire BME280 to Pi 5 I²C-1 (pin 3 SDA / pin 5 SCL / 3V3 / GND). Confirm with `i2cdetect -y 1` at 0x76 or 0x77.
- [ ] Cross-check temperature against a reference thermometer and pressure against the nearest METAR/airport altimeter setting corrected to station elevation. **Record both in the PR's manual-testing section** — `CONTRIBUTING.md` requires it and "tests pass" is explicitly not acceptable.
- [ ] Soak test: log temperature for 30 min with the Pi under load, lid on. Quantify actual self-heating. If it exceeds ~1 °C, revisit placement before adding a software offset.

### Task 4.2: Enclosure note

- [ ] Record the measured self-heating figure and final mounting position in `docs/` so the enclosure CAD work (rear intake grille) inherits a real number instead of the 1-3 °C datasheet range.

---

## Slice 5 — Live UI panel

*Demo: conditions visible on the kiosk and updating between shots.*

### Task 5.1: Transport

- [ ] Add `@socketio.on("get_environment")` to `server.py` mirroring `get_trigger_status` (`:1516`).
- [ ] Emit `environment` on a timer and include a snapshot in the `connect` handler (`:1486-1507`) so the panel renders immediately rather than after the first tick.
- [ ] Add the event to `ui/src/types/socket.ts` and register the listener in `socketService.setupListeners()` (`ui/src/services/socketService.ts:33`), pushing into a new `ui/src/stores/useEnvironmentStore.ts` via `getState().set...` — the existing store-update idiom.

### Task 5.2: Component

- [ ] `ui/src/components/EnvironmentPanel.tsx` + `.css` + colocated `.test.tsx` (Vitest, sibling-file convention per `ShotDisplay.test.tsx`).
- [ ] Show temp / pressure / RH / density and a **source badge**. `"default"` must be visually distinct — the user needs to know when carry is assuming standard conditions. (This is open question #4 in the design doc; default to showing it.)
- [ ] Inline SVG for any iconography — house style, see `App.tsx:36-67`.
- [ ] Place it inside `DebugPanel` or as a new `View` in `App.tsx:33`; prefer `DebugPanel` for v1 to avoid growing the bottom nav.

---

## Slice 6 — Open-Meteo fallback

*Demo: no sensor, network up ⇒ `air_density_source == "open-meteo"` and a non-1.225 density.*

- [ ] `src/openflight/environment/openmeteo.py` — `fetch_conditions(lat, lon)` via stdlib `urllib.request` following `src/openflight/cloud/client.py`'s pattern. Query `current=temperature_2m,relative_humidity_2m,surface_pressure`. Hard timeout, TTL cache (15 min), all exceptions swallowed to `None`.
- [ ] Fetch on a background timer, never in the shot path.
- [ ] Add `--weather-fallback-openmeteo`, `--weather-lat`, `--weather-lon`; `parser.error()` if the flag is set without coordinates (matches the validation style at `server.py:3250-3270`).
- [ ] `tests/test_environment_openmeteo.py` — fake transport (see `tests/test_cloud_client.py:10 FakeTransport`), assert parsing, TTL behaviour, timeout → `None`, malformed JSON → `None`.
- [ ] Add Open-Meteo CC BY 4.0 attribution to `README.md`.

---

## Definition of done

- [ ] `uv run pytest tests/ -v` green
- [ ] `uv run pylint src/openflight/ --fail-under=9`
- [ ] `uv run ruff check src/openflight/` and `uv run ruff format --check src/openflight/`
- [ ] `cd ui && npm run lint && npm run build`
- [ ] `docs/CHANGELOG.md` `[Unreleased]` entry added
- [ ] `README.md` documents the sensor, wiring, flags, and Open-Meteo attribution
- [ ] PR title: `feat(weather): add BME280 air-density carry correction`
- [ ] PR body fills all three CI-checked sections: why it was required / automated tests / manual testing performed
