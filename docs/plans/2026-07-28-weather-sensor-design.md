# Weather Sensor: Measured Air Density for Carry Correction

*Design doc. Branch `feat/weather-sensor`. Written 2026-07-28.*

## Problem

Every carry number OpenFlight produces assumes ISA sea-level air:

- `src/openflight/ballistics.py:37` — `AIR_DENSITY_STD = 1.225  # kg/m³ at sea level, 15 °C ISA`
- `src/openflight/server.py:2368` — the only production call site is `simulate(conditions)`, i.e. the default
- `src/openflight/launch_monitor.py:90-96` — `estimate_carry_distance()` docstring already lists "Weather conditions" and "Altitude" as unmodeled ±10-15% error sources

Air density is the single largest *systematic* error in the carry pipeline and it is currently unmeasured. Numbers below are from this repo's own RK4 integrator (`simulate()`), driver = 165 mph / 12.5° / 2600 rpm, 7-iron = 120 mph / 18° / 6500 rpm:

| Condition | ρ (kg/m³) | Driver carry | Δ vs 1.225 | 7-iron Δ |
|---|---|---|---|---|
| ISA std | 1.2250 | 256.5 yd | — | — |
| 15 °C, 1013 hPa, 50% RH | 1.2211 | 256.8 yd | +0.2 | +0.2 |
| 0 °C, 1013 hPa, 30% RH | 1.2914 | 252.5 yd | **−4.0** | −3.4 |
| 35 °C, 1013 hPa, 80% RH | 1.1262 | 262.4 yd | **+5.8** | +5.1 |
| Denver, 25 °C, 835 hPa | 0.9700 | 270.9 yd | **+14.3** | +12.6 |

Sensitivity: **−1% air density ≈ +0.74 yd driver, +0.63 yd 7-iron.**

A $4 sensor removes a 14-yard bias. This is the best accuracy-per-dollar item left in the system.

## Approach

Measure station pressure, temperature and relative humidity on I²C; compute humid-air density; pass it to the integrator that already accepts it.

```
BME280 (I²C 0x76/0x77)
    │  temp_c, pressure_pa, humidity_pct   (background poll, ~1 Hz)
    ▼
air_density(temp_c, pressure_pa, humidity_pct) -> kg/m³
    │
    ├─► simulate(conditions, air_density=ρ)      # exact, ballistics path
    ├─► carry × (ρ/ρ_std)^-k                      # scalar, table-fallback path
    ├─► Shot.air_* fields → shot_to_dict → UI + session JSONL
    └─► session_start config (provenance)
```

**Station pressure already encodes altitude.** There is deliberately no elevation/altitude input in this design. A barometer reading 835 hPa *is* Denver; asking the user for elevation and then re-deriving pressure would be strictly worse. This also means the sensor tracks weather fronts that an elevation lookup never would — a 980 hPa storm low is 3.3% less dense than 1013 hPa at the same temperature, ≈ +2.4 yd driver.

### Density formula

Partial-pressure model (dry air + water vapour as ideal gases):

```
p_v  = RH/100 · p_sat(T)                    # Buck (1981) saturation vapour pressure
p_d  = p_station − p_v
ρ    = p_d/(R_d·T_K) + p_v/(R_v·T_K)        # R_d = 287.058, R_v = 461.495 J/(kg·K)
```

Buck: `p_sat = 611.21 · exp((18.678 − T/234.5)·(T/(257.14 + T)))` Pa, T in °C.

This is accurate to ~0.1% over golf conditions — far below the sensor error budget — and needs no compressibility factor (full CIPM-2007 is unnecessary here and should be explicitly rejected as over-engineering).

**Pure functions, no I/O, no sensor coupling.** They live next to the driver but take three floats and return a float, so they are trivially testable against published psychrometric tables.

### Why BME280 over BMP390

BMP390 has no humidity channel. Humidity is the *smallest* of the three terms — 0→100% RH changes density by −0.33% at 5 °C, −1.18% at 25 °C, −2.10% at 35 °C — so a BMP390 plus an assumed 50% RH would be wrong by at most ~1% (~0.8 yd driver). That is defensible.

It is not, however, *cheaper*: BME280 and BMP390 breakouts are the same price class. BME280 removes an assumption for free, and RH is independently useful metadata for shot logs (and for the club-data camera work, where humidity correlates with haze). Recommendation: BME280, and state plainly in the docs that humidity is a sub-1-yard term so nobody over-values it.

**Counter-consideration, stated honestly:** BMP390's pressure spec (±3 Pa relative, ±50 Pa absolute) is better than BME280's (±12 Pa relative, ±100 Pa absolute). Per the error budget below, neither matters — pressure error is the *least* significant contributor. Precision pressure is not the reason to pick a part here.

## Error budget

Converting BME280 datasheet specs into carry error (driver, −1% ρ = +0.74 yd):

| Source | Spec | ρ error | Driver carry error |
|---|---|---|---|
| Pressure, absolute | ±1 hPa | ±0.10% | ±0.07 yd |
| Temperature | ±0.5 °C (±1 °C full range) | ±0.17% (±0.35%) | ±0.13 (±0.26) yd |
| Humidity | ±3% RH | <±0.06% | <±0.05 yd |
| **Self-heating, uncorrected** | **+1 to +3 °C** | **0.35–1.04%** | **0.26–0.77 yd** |
| Total (RSS, self-heat corrected) | | ~0.2% | **~0.15 yd** |

**Self-heating is the dominant term and the only one worth engineering against.** The BME280 die warms 1-3 °C above ambient in continuous mode inside a warm enclosure. Mitigations, in order of value:

1. **Forced mode, low duty cycle.** Take a single forced-mode measurement every ~2 s with oversampling ×1 on T/H and ×4 on P, and sleep between. This is the difference between "self-heats 1-3 °C" and "self-heats ~0.1 °C" — it is the mitigation.
2. **Placement.** Flush behind the rear intake grille, in the cool-air path, away from the Pi 5 and the screen. This matches the enclosure design notes (external to this repo — the v5 exterior work), which already reserve a flush position behind the vent grille at the cool-air intake. Nothing in the repo tree currently records this; Slice 4 should write the measured figure into `docs/` so the CAD work inherits a real number.
3. **Software offset.** A `--weather-temp-offset-c` flag for the user to trim against a reference thermometer. Last resort, not the plan.

Even fully un-mitigated, sensor error is ~0.8 yd against a 14 yd bias. This subsystem does not need to be precise; it needs to exist.

## Data sources and fallback chain

Provenance is a first-class field, following the existing `spin_source` / `angle_source` string convention (`launch_monitor.py`).

| Priority | Source | `air_density_source` | When |
|---|---|---|---|
| 1 | **BME280** | `"bme280"` | Sensor fitted and reading fresh (< 60 s old) |
| 2 | Manual override | `"manual"` | User typed values in the settings screen |
| 3 | Open-Meteo | `"open-meteo"` | Location known, cached reading available |
| 4 | Elevation + manual temp | `"elevation"` | No barometer — `pressure_from_elevation_pa()` |
| 5 | ISA default | `"default"` | Everything else — today's behaviour, now flagged in the UI |

**Why the sensor outranks the API even when both are available.** Open-Meteo returns a grid-cell average of *outdoor* conditions. The BME280 measures the air the ball actually flies through. Indoors those diverge completely — a 22 °C garage while it's 36 °C outside means the API applies a correction that is actively wrong, worse than no correction. The sensor has no such failure mode, which is the real argument for fitting one.

### Location: detected once, refreshed on demand

**No polling.** A launch monitor lives at one or two places and the weather does not move fast enough to matter within a session. Fetch on first setup, then only when the user taps the weather chip in the UI. Cached value used in between, with its age shown.

**Detection: IP lookup, opt-in, always editable.** `navigator.geolocation` is the obvious choice but fails on this hardware — Raspberry Pi OS ships Chromium without the Google API keys its geolocation backend requires, and it is additionally blocked on non-secure origins, so it breaks the moment someone opens the UI from a phone at `http://192.168.x.x`. IP lookup needs no permission, no key and no GPS, and its city-level accuracy is ample: temperature and pressure do not vary meaningfully across a town. Use browser geolocation opportunistically if it happens to succeed; never depend on it.

First run detects, shows what it found, and saves to `~/.config/openflight/location.json` — the same pattern as `cloud/config.py`. Location leaves the device, so this is **opt-in with a visible toggle**; the project already takes this seriously (`cloud/filtering.py:29-37` is an allowlist, not a blocklist).

**Detection is not enough on its own — added 2026-07-30.** IP lookup returns the VPN's exit node, not the venue. A user on a VPN gets weather for another country and no indication anything is wrong, because the failure produces a perfectly plausible-looking reading. Detection must therefore be a *suggestion*, with an explicit search as the primary path. See "Location search" below.

### Location search — CONFIRMED 2026-07-30

Open-Meteo publishes a **Geocoding API** on the same terms as the forecast API, so this costs no new dependency and no key:

```
GET https://geocoding-api.open-meteo.com/v1/search?name=<query>&count=10&language=en&format=json
```

Three properties make it the right fit, all CONFIRMED from the published docs:

1. **It accepts postal codes**, not just place names. That matters more here than anywhere else: a US ZIP is five digits, so it can be entered with the numeric keypad the Conditions screen already has. No on-screen alphabetic keyboard is needed for the common case — which is the difference between shipping this and building a whole QWERTY for a 7" panel.
2. **Each result carries `elevation`.** This is the field that steers `surface_pressure` in the forecast call and the one users are worst placed to know. Picking a search result should fill it in automatically rather than asking for it — a 100 m error is ~1.2% density, about 0.9 yd on a driver.
3. Results also carry `name`, `country`, `country_code`, `admin1`–`admin4`, `timezone`, `population` and a `postcodes` array — enough to disambiguate the several Springfields without another round trip.

No API key for non-commercial use; commercial use needs one and a `customer-` URL prefix. **Attribution to GeoNames is required** on top of the existing Open-Meteo CC BY 4.0 credit.

### Prior art: how other launch monitors handle this — researched 2026-07-30

Worth knowing what the category has converged on, and where it has not.

| Product | Sources offered | Notes |
|---|---|---|
| **TrackMan** (TPS) | Normalization to user-set altitude + temperature | Defaults **77 °F, sea level** — the same reference this design picked independently. Normalizes away wind *and* density because it tracked the real flight. CONFIRMED |
| **Garmin R10** | Course Location / Your Location / Custom | Three-way source choice, closely mirroring auto / sensor / manual here. "Your Location" needs phone GPS permission. CONFIRMED |
| **Foresight GCQuad, GC3** | Onboard barometric sensor, automatic | Hardware barometer adjusts ball flight for altitude with no user input. FSX additionally offers real-time weather for a simulated course, or custom values. CONFIRMED |

Three things this confirms about the design already chosen here:

- **Sensor-first is the category norm, not an eccentricity.** Foresight puts a barometer in the unit and uses it automatically. This design's precedence (`bme280 > manual > open-meteo`) matches, and for the same reason.
- **77 °F / sea level is the de facto standard reference.** Matching TrackMan's defaults means a standard-conditions figure here is directly comparable to a TrackMan number, which is worth more than any locally-optimal choice.
- **Every one of them lets the user type conditions in.** The manual path is not a fallback for broken hardware, it is a first-class mode everywhere in the category.

And one thing it flags as a real risk. Garmin's own documentation warns that if the configured conditions "do not match the conditions at your normal course locations, the carry distances may seem inaccurate" — which is the exact invitation that produces the documented R10-in-E6 habit of entering 10,000 ft until the numbers look right. The warning tells users the settings control how far the ball goes, without telling them that mis-setting them corrupts everything downstream. **This is the failure mode "Do not build a fudge knob" below exists to prevent**, and it is worth noting the largest vendor in the category walked straight into it.

**UI gap nobody fills — INFERRED.** TrackMan switches *between* actual and normalized (Range Ball vs Premium Ball); it does not show both at once. Nothing found shows today's carry and a reference carry side by side. Showing both simultaneously, as this design does, appears to be genuinely novel rather than merely different — and it is the arrangement that actually answers "am I hitting it further, or is it just hot?", which a toggle cannot: a toggle makes you remember the other number.

### Indoor play

Do not solve this with a "disable location" switch — falling back to 15 °C ISA is worse than a slightly wrong correction.

**Pressure is essentially identical indoors and outdoors.** Buildings are not pressure vessels. Only temperature and humidity differ. So an **Indoors** toggle keeps the API's pressure and asks for the room temperature, which is both more accurate and less work than disabling the whole subsystem. Most sim bays are climate-controlled, so a thermostat reading is a good input.

If a BME280 is fitted, the toggle is irrelevant — the sensor is already measuring the bay.

**Open-Meteo needs no new dependency.** `src/openflight/cloud/client.py` already uses stdlib `urllib.request`; the same pattern applies. Open-Meteo is free, keyless, CC BY 4.0, and `surface_pressure` + `temperature_2m` + `relative_humidity_2m` on the `/v1/forecast` current block is exactly the triple needed. Attribution goes in `README.md`.

Open-Meteo is a *degraded* source, not an equal one: it is a gridded model value for the nearest cell, not the pressure inside the hitting bay. Treat it as better-than-1.225, worse-than-sensor, and label it as such in the UI. A stale or failed fetch silently falls through to `"default"` — never blocks a shot, never raises into the shot path.

## Architecture

New package `src/openflight/environment/`:

| Module | Contents |
|---|---|
| `density.py` | `saturation_vapour_pressure_pa()`, `air_density()`, `carry_density_factor()`. Pure. No imports beyond `math`. |
| `sensor.py` | `BME280Sensor` — connect / start / stop / `latest()`. Background daemon thread, lock-guarded latest reading. |
| `openmeteo.py` | `fetch_conditions(lat, lon)` via `urllib.request`, with TTL cache. |
| `provider.py` | `EnvironmentProvider` — owns the fallback chain, exposes one `current()` returning a reading + source string. |

**Follow existing house patterns, do not invent:**

- **Thread + lock-guarded latest reading:** copy `KLD7Tracker` (`src/openflight/kld7/tracker.py:119-869`) — class-level attribute defaults so `__new__`-constructed test instances don't `AttributeError` (`:128-146`), `connect() -> bool` that never raises (`:211-268`), `start()`/`stop()` with a daemon thread and `join(timeout=5)` (`:300-316`), broad `except Exception` in the loop, `[BME280]`-prefixed log lines.
- **Hardware import seam:** copy `gpio_factory._load_gpiozero()` (`src/openflight/gpio_factory.py:66`, comment "Separated for testing"). A `_load_smbus()` function is the single point tests monkeypatch. On `ImportError`, raise `RuntimeError` with an actionable "install via `uv sync`" message, exactly as `gpio_factory.py:98-101` does. **Do not fall back silently on a missing library** — that is a different failure from a missing sensor.
- **Server init:** copy `init_kld7()` (`server.py:1094-1179`) — `global`, everything in `try/except Exception`, `return False` + `log_session_error(...)` on failure, hardware module imported *inside* the function so the server still imports on a dev laptop, `session_log.log_connection(...)` on success.
- **Shot-path read is a snapshot, never a bus transaction.** `on_shot_detected` must not do blocking I²C. It reads the cached value under a lock.

### I²C is new to this repo

`grep -r "smbus\|i2c\|adafruit\|busio" src/` returns zero hits. This adds the first I²C dependency. Prefer **`smbus2`** (pure Python, no `board`/`busio` CircuitPython stack, ~120 lines of BME280 compensation code we control) over `adafruit-circuitpython-bme280` (pulls `Adafruit-Blinka`, which is a large dependency that probes platform hardware at import time).

Dependency line in `pyproject.toml`, matching the `gpiozero`/`lgpio` precedent:

```toml
"smbus2>=0.4.3; sys_platform == 'linux'",
```

The BME280 compensation algorithm (Bosch datasheet §4.2.3, `BME280_compensate_T_int32` / `_P_int64` / `_H_int32`) is fixed-point and finicky; it gets its own unit test against the datasheet's worked example values.

## Integration points

1. **Ballistics path — the one-line win.** `server.py:2368`, `simulate(conditions)` → `simulate(conditions, air_density=rho)`. `ballistics.py` needs **no signature change**; the parameter is already threaded through `_derivatives` (`:186,206`), `_rk4_step` (`:233`), `simulate` (`:253`).

2. **Table-fallback path — the one that's easy to forget.** `Shot.estimated_carry_yards` (`launch_monitor.py:324-335`) and `estimate_carry_with_spin` (`server.py:2388`) are density-blind, and `estimated_carry_yards` is what the UI shows when `carry_spin_adjusted` is null. If only #1 is done, the correction is invisible half the time.

   Correction: `carry × (ρ/1.225)^-k`. Fitting the repo's own integrator gives a club-dependent exponent:

   | Club | k @ ρ ratio 0.90 | 0.95 | 1.05 | 1.10 |
   |---|---|---|---|---|
   | Driver | 0.263 | 0.276 | 0.300 | 0.312 |
   | 5-iron | 0.273 | 0.288 | 0.318 | 0.331 |
   | 7-iron | 0.319 | 0.333 | 0.360 | 0.373 |
   | PW | 0.366 | 0.377 | 0.399 | 0.409 |

   A single k = 0.30 leaves residuals of ≤1.3 yd over ±10% density and +3.9 yd for a driver at −20% (Denver). A per-`ClubType` k table (generated by a checked-in script, sitting next to `CLUB_TYPICAL_SPIN_RPM` at `ballistics.py:70-92`, which is the same shape of thing) drops that under ~0.5 yd.

   **Corrected 2026-07-30, measured rather than estimated.** Sweeping k against the integrator across the whole bag (representative ball speed + `_OPTIMAL_LAUNCH` + `CLUB_TYPICAL_SPIN_RPM` per club) gives a worst residual for k = 0.30 of **2.14 yd on a driver**, not ≤1.3 yd. As a fraction of carry it is under **1% everywhere** (driver 0.85%, worst club 0.96%), which is the bound worth quoting, since the error scales with carry — the sand wedge's 0.16 yd and the driver's 2.14 yd are both sub-1% misses. The sweep also confirms k = 0.30 is the worst-case-percentage optimum, so the value itself stands; only the accuracy claim was wrong. `tests/test_ballistics.py::TestDensityCarryFactor` now pins both the 1% bound per club and the driver's absolute figure.

   **Recommendation: per-`ClubType` k table.** It is the same amount of code as the scalar and the repo already has a per-club constant dict to pattern-match. Flag for review: this is the only genuinely debatable decision in the design — a reviewer could reasonably argue "don't correct the fallback path at all, just require `--ballistics`."

3. **Shot record — three places, they must move together** (known DRY wart in this repo, no shared schema):
   - `Shot` dataclass, `launch_monitor.py:198-378` — add `air_temp_c`, `air_pressure_hpa`, `humidity_pct`, `air_density_kg_m3`, `air_density_source`
   - `shot_to_dict()`, `server.py:825-897` — rounding for the wire
   - `SessionLogger.log_shot()`, `session_logger.py:429-438` — the conditional `if x is not None:` block for newer optional fields
   - `ui/src/types/shot.ts` — matching TS fields

4. **Session provenance.** `_session_start_config()` (`server.py:804-816`) already nests `config["iwr6843"]`; add `config["environment"]`. Sensor present/absent, source, lat/lon if used.

5. **Live UI push.** Ambient conditions change *between* shots, so shot-attached data alone is not enough. Mirror the `trigger_status` pattern: `@socketio.on("get_environment")` (cf. `server.py:1516`) + periodic `socketio.emit("environment", ...)` + include a snapshot in the `connect` handler (`server.py:1486-1507`) so the panel renders immediately. Frontend: new `ui/src/stores/useEnvironmentStore.ts`, listener registered in `socketService.setupListeners()` (`ui/src/services/socketService.ts:33`), types in `ui/src/types/socket.ts`.

6. **Cloud upload allowlist.** `src/openflight/cloud/filtering.py:29-37` `KEEP_ENTRY_TYPES` is an intentional privacy allowlist. Environmental fields riding inside `shot_detected` / `session_start` are uploaded automatically. A *separate* `environment_reading` entry type would be silently dropped — so don't create one unless it's added to the allowlist deliberately.

7. **GSPro/OpenConnect:** nothing to do. The protocol (`gspro/codec.py:56-85`) has no environmental fields.

## CLI surface

Following the `--kld7*` / `--iwr6843*` precedent in `server.py:main()` and mirrored in `scripts/start-kiosk.sh`:

```
--weather                     enable the subsystem
--weather-i2c-bus N           default 1
--weather-i2c-addr 0x76|0x77  default 0x76
--weather-poll-s N            default 2.0
--weather-temp-offset-c X     manual trim, default 0.0
--weather-fallback-openmeteo  enable network fallback
--weather-lat / --weather-lon required if the above is set
```

`--weather` absent ⇒ byte-identical behaviour to today. That property is worth an explicit regression test.

## Normalization: how TrackMan does it, and what the sensor changes — researched 2026-08-06

The "standard-conditions carry" in this design is the same feature TrackMan
calls **Normalization**. Their methodology, from their own documentation:

- **They re-fly the measured launch data** — ball speed, launch angle, spin
  rate — through their aerodynamic model at the reference conditions. The
  normalized number is a fresh simulation, not a scaling of the observed
  carry.
- **The reference is user-typed, defaulting to 77 °F and sea level.** TPS
  asks the user to input altitude and temperature; wind is always assumed
  calm. TrackMan does not measure the day's conditions for this feature at
  all — CONFIRMED from their blog and Help Center.
- **A premium ball is assumed**; non-premium launch data is first passed
  through their Ball Conversion.
- Their aero model comes from robot/air-cannon testing across the ball
  speed × spin envelope.

### The counter-intuitive finding: normalization does not need the sensor

Launch data is measured **at impact**, before the air has acted on the ball,
so it is condition-independent. Re-flying it at reference air needs only the
launch data and the model:

```
carry_normalized = simulate(launch_data, ρ_reference)     # ballistics path
carry_normalized = table(launch_data) × factor(ρ_ref)     # table path; ×1.0 for ISA
```

Neither line contains today's density. On the table path with the ISA
reference, the **uncorrected table number already IS the normalized number**
— the tables encode standard-air carry, which is why the pre-weather
OpenFlight numbers were "normalized" all along without anyone calling them
that.

### What the sensor actually buys, stated precisely

1. **It makes the ACTUAL carry real.** Without it OpenFlight shows one
   number that is neither today's carry nor labelled as standard. With it,
   "carried 263 today / plays like 256 standard" is a truthful pair, and the
   gap between the two is measured rather than invented.
2. **It beats TrackMan's own workflow at the input.** TrackMan trusts the
   user to type altitude and temperature; a typed 77 °F on a 97 °F afternoon
   silently corrupts the actual-vs-normalized comparison. A sensor cannot be
   mistyped and tracks a 5 °C evening drop the user would never re-enter.
3. **It future-proofs inversion.** Any later feature that works backwards
   from an observed landing (camera carry validation, model fitting) must
   divide today's air out first — impossible without knowing it.

What the sensor does **not** do is move the normalized figure itself. Claiming
otherwise in a PR description would be wrong, and a reviewer who understands
the physics would catch it.

### Two caveats that bound the feature's honesty

- **Normalized carry inherits spin provenance.** With `spin_source ==
  "club_typical"` the normalized number is partly fabricated no matter how
  good the air data is — surface the provenance next to it, exactly as rule
  2 below says for actual carry.
- **Reference presets must be explicit.** TrackMan's 77 °F/sea-level air
  (~1.177 kg/m³ with their unpublished RH taken as 50%) is ~4% thinner than
  ISA's 1.225, so an ISA-normalized OpenFlight driver reads ~2-3 yd shorter
  than a TrackMan-normalized one for identical launch data. Not a bug —
  a different reference — but it must be labelled or users will "fix" it
  with the knob this document forbids.

## Do not build a fudge knob

Garmin R10 users on the Garmin forums report setting elevation to **10,000 ft** in E6 Connect to make their distances look right. That is air 31% thinner than sea level — not an elevation correction for anyone outside Leadville, but a tuning knob being abused to fix something else.

Running the numbers through this repo's own integrator shows it cannot even work: spin errors push **drivers long and irons short**.

| Driver 165/12.5°, true spin 2600 | Carry | | 7-iron 120/18°, true spin 6500 | Carry |
|---|---|---|---|---|
| correct | 256.5 y | | correct | 182.6 y |
| model thinks 3500 rpm | 264.9 y (**+8.4**) | | model thinks 9500 rpm | 174.4 y (**−8.2**) |

A single density fudge corrects one and worsens the other. Whoever set 10,000 ft fixed their 7-iron and made their driver fantasy.

**The likely real causes** (inference — E6 and the R10 are closed):

1. **Spin estimation.** The R10 does not measure spin without an RCT ball. Over-estimated iron spin shortens carry exactly as above.
2. **Sea-level vs station pressure.** Weather APIs return MSL-adjusted pressure by default. Using it as station pressure over-estimates density, shortens carry, and **the error grows with the user's real elevation** — which is why "add elevation" feels like the fix. This is the failure mode the `elevation=` request parameter exists to prevent.
3. Ball speed under-read from misalignment.
4. The reference being optimistic. Tuning until the number matches what you believe you hit is not calibration.

**OpenFlight is exposed to #1.** Spin detection runs ~50-60% indoors and `resolve_launch` substitutes `CLUB_TYPICAL_SPIN_RPM` — PGA Tour averages that will not match every player. No amount of correct weather data fixes a wrong spin assumption.

### Three rules this imposes on the design

1. **Validate elevation against the detected location** and warn hard on a mismatch: *"you entered 10,000 ft, your location is at 30 ft."* Right now no elevation field exists, so the temptation does not either — adding one creates it.
2. **Surface `spin_source` prominently.** Every shot already records `"measured"` or `"club_typical"`. If carry looks wrong on club-typical spin, that is the thing that is wrong, not the air. The R10 does not make this distinction, which is part of why its users reach for the wrong knob.
3. **No general carry-calibration multiplier, ever.** Every app that ships one becomes a machine for confirming what the user already believed. If the numbers are off, the answer is to find out why.

## Non-goals

- **Wind.** A launch monitor cannot measure the wind the ball actually flies through. Do not add an anemometer, do not add wind to the ballistics model.
- **Elevation input.** Superseded by station pressure (see above).
- **Weather forecasting / "playing conditions" scoring.** Out of scope.
- **Altimeter / "shots gained by altitude" features.** Out of scope.
- **Correcting historical shots.** Density is recorded per shot going forward; past sessions are not retro-adjusted.

## Hardening: what an external audit found — 2026-07-31

An independent pass over the branch found fourteen issues; thirteen were in
code this work introduced. The pattern behind almost all of them is one thing:

> Every value in this subsystem arrives from somewhere we do not control — a
> JSON config a user can hand-edit, a Socket.IO payload from any client on the
> LAN, or a third-party HTTP response. Nothing else in the repo has that
> property, so nothing else in the repo needed to be defensive in this way.

The dangerous half is that this subsystem also sits *on the shot path*.
`EnvironmentProvider.current()` runs inside shot handling, outside the caller's
exception guard, so anything that raises there loses the shot itself, not just
the air-density correction. That asymmetry is what makes these worth fixing
rather than tolerating.

What changed, and why each one was reachable:

| Fix | How it was reached |
| --- | --- |
| `load_config` rejects a non-dict JSON root | `[]` is valid JSON; `.get()` then raises while the server builds its global provider, so it never boots |
| `_build` catches `TypeError` as well as `ValueError` | A string or list in `manual_temp_c` reaches the arithmetic; on the shot path this loses the shot |
| That handler logs with `%r`, not `%.1f` | `%.1f` on a string raises *inside* the handler meant to swallow exactly that |
| `air_density` rejects non-finite humidity | NaN survives `min`/`max` untouched and silently poisons the density; `inf` clamps to "saturated" as though real. Merely huge finite values still clamp, which is right — they are out of range the same way `120` is |
| `search_locations` requires a string `name` | A numeric `name` builds a `LocationResult` whose `label` raises in the *caller*, so the UI waits forever for results that never arrive |
| `lookup_location` filters non-strings out of its label | Same failure, different endpoint |
| `handle_set_weather_settings` rejects a non-mapping payload | Socket.IO hands the handler whatever the client emitted; `.get()` on `None` raises in the event loop that every other tab shares |
| Choosing a location clears the previous venue's cached weather | Otherwise the panel shows the old city's conditions under the new city's name — wrong in a way that looks entirely plausible |
| An unknown elevation now *clears* the old one | The previous behaviour kept a stale elevation for a new venue. A test had enshrined it, so the test was wrong too and was replaced |
| The UI sends a patch, not its whole draft | The draft is a snapshot from before an in-flight fetch. Editing anything while "Detect location" was running wrote the pre-fetch coordinates back over the ones that had just landed |

Every one of these has a regression test that was confirmed to fail with the
fix reverted. Three tests that existed but asserted nothing useful were also
replaced — one checked that density was `None` without ever computing a carry,
one asserted a property of the Python `or` operator rather than of the server,
and one checked hint text rather than the two elevation fields it was named
for.

### Still open

- **The 0.30 carry exponent** is documented as empirical but has no citation in
  the repo. Whether to source it, measure it, or restate the claim more
  narrowly is deferred — it changes what the docs assert, not what the code
  does.
- **Settings writes are not atomic.** Two clients editing at once can
  interleave; a partial payload narrows the window a lot but does not close it.
- **Refresh requests can pile up.** Nothing coalesces concurrent fetches.

## Open questions for review

1. Per-`ClubType` k table vs single k = 0.30 vs no fallback-path correction at all (integration point #2).
2. Is Open-Meteo fallback worth the code, or is "no sensor ⇒ 1.225" acceptable for v1? It is ~80 lines plus tests and it introduces the project's first outbound HTTP call in the hot path's vicinity.
3. `smbus2` + hand-rolled Bosch compensation vs `adafruit-circuitpython-bme280` + Blinka. The recommendation above is `smbus2`, but it means owning ~120 lines of fixed-point compensation math.
4. Should `air_density_source == "default"` be surfaced in the UI as a warning ("carry assumes standard conditions"), or silently?
