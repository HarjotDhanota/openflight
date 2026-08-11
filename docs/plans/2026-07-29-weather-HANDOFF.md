# Weather Subsystem — Agent Handoff

*Branch `feat/weather-sensor`. Written 2026-07-29. Read this plus the design doc before touching anything.*

**Design doc:** `docs/plans/2026-07-28-weather-sensor-design.md`
**Original plan:** `docs/plans/2026-07-28-weather-sensor-plan.md` (slice numbering below supersedes it)
**Hardware context:** `docs/hardware-integration.md`, `docs/build-checklist.md` (on `main`)

---

## 0. Scope — one PR, decided

**Title:** `feat(weather): correct carry for air density`

This ships as a **single PR** covering both the density correction and the standard-conditions carry figure. `CONTRIBUTING.md:114-117` asks for one feature per PR, and the judgement here is that these are one feature, not two:

- The standard figure is not new physics or a new data path. It is the *same* density correction run against a fixed reference instead of today's air, rendered as one extra line.
- It shares every dependency: `EnvironmentProvider`, `WeatherConfig`, `simulate()`, the settings screen.
- The "why" is a single story: carry was uncorrected for air; this corrects it, and lets you see what the number would be at a fixed reference so sessions on different days compare.

Splitting them would produce a second PR whose entire content is "display the number we already computed."

**If a maintainer disagrees and asks for a split**, §4 gives the exact file seam — the standard-carry work was kept isolated during development for precisely this reason.

**Do not add `src/openflight/i2c.py` to this PR.** Nothing here uses it yet (no BME280 driver exists). It belongs with whichever branch first ships a driver.

---

## 1. What is done and verified

| Area | State |
|---|---|
| `environment/density.py` | Done. 45 tests passing, anchored to published psychrometric values |
| `environment/config.py` | Done, **no committed tests** |
| `environment/provider.py` | Done, **no committed tests** |
| `ballistics.py` — `CARRY_DENSITY_EXPONENT`, `density_carry_factor` | Done, **no committed tests** |
| `launch_monitor.py` — `Shot` fields, density-aware `estimated_carry_yards` | Done, **no committed tests** |
| `server.py` — provider wiring, both carry paths, socket handlers, CLI | Done, **no committed tests** |
| UI — store, types, `WeatherSettings`, `SettingsView`, nav entry, units | Done, **no committed tests** |
| Open-Meteo fetch | **Not started.** `refresh_weather` emits a placeholder error |

**Nothing has been run through `uv run pytest`, `pylint`, `ruff`, or `npm run build`.** The author had no access to the venv. Expect ruff formatting complaints on long lines. **Run the gates first, before writing anything new.**

---

## 2. Verified behaviour (reproduce these — they are the acceptance criteria)

Air density from the repo's own integrator, driver 165 mph / 12.5° / 2600 rpm:

| Condition | ρ | Driver carry |
|---|---|---|
| ISA sea level (what OpenFlight assumed forever) | 1.2250 | 256.5 y |
| Sacramento 2026-07-29, 97 °F / 1010.2 hPa / 25% RH | 1.1316 | **262.1 y** |
| Denver, 25 °C / 835 hPa / 40% | 0.9700 | 270.9 y |

Source precedence, all verified:

```
CLI override > bme280 > manual > open-meteo cache > elevation estimate > ISA default
```

- Sensor older than 60 s falls through
- `mode=off` returns ISA regardless of available data
- Indoors keeps fetched **pressure**, replaces **temperature** only
- Out-of-range values log and fall through; never raise into the shot path
- Corrupt `weather.json` returns defaults; the server must still boot

`density_carry_factor` worst residual vs the integrator across ±10% density: ~~**1.30 yd**~~ — **wrong; corrected 2026-07-30.** Measured across the whole bag the worst case is **2.14 yd on a driver**, which is **0.85% of carry**; no club exceeds **1%**. A k-sweep confirms 0.30 is the worst-case-percentage optimum, so the constant stands and only the claim was overstated. `ballistics.py` and the design doc are updated; `tests/test_ballistics.py::TestDensityCarryFactor` guards the 1% bound per club.

---

## 3. Outstanding work, in order

### 3.1 Run the gates (do this first)

```bash
cd openflight-weather
uv run pytest tests/ -v
uv run ruff format src/openflight/
uv run ruff check src/openflight/
uv run pylint src/openflight/ --fail-under=9
cd ui && npm run lint && npm run build
```

### 3.2 Tests — CI will fail without these

`CONTRIBUTING.md:135-137`: CI fails when `src/openflight/` or `ui/src/` change without accompanying test changes. `tests/test_environment_density.py` technically satisfies the crude check, but coverage is thin and the PR will be asked for changes.

Write:

- **`tests/test_environment_config.py`** — round-trip save/load; missing file → defaults; corrupt JSON → defaults (must not raise); invalid `mode` → `auto`; `elevation_looks_like_a_fudge` boundary at 2500 m
- **`tests/test_environment_provider.py`** — the full precedence chain above; stale-sensor fallthrough; `mode=off`; indoors pressure/temperature split; bad values degrade instead of raising; `sensor_present()` is independent of the active source; `standard_density()` respects config
- **`tests/test_ballistics.py` additions** — `density_carry_factor(1.225) == 1.0` exactly; raises on non-positive; **residual < 1.4 yd across ±10% density for every ClubType** (this is the guard on the whole approximation — it fails loudly if anyone retunes the Cd/Cl coefficients)
- **`tests/test_server.py` additions** — **no weather flags ⇒ shot output byte-identical to before** (the critical regression test); CLI flags populate the provider; `set_weather_settings` persists and re-emits; `_apply_standard_carry` skips below 0.5% deviation
- **`ui/src/components/WeatherSettings.test.tsx`** — renders the uncorrected warning at `source: 'default'`; sensor row appears when `sensor_present`; imperial/metric conversion round-trips; elevation warning above 2500 m
- **`ui/src/components/ShotDisplay.test.tsx` addition** — std note renders when `carry_standard_yards` is set, absent when null

Repo test conventions: pytest, class-grouped, `monkeypatch` on module globals, locally-defined `Fake*` classes (see `tests/test_kld7.py:274`, `tests/test_cloud_client.py:10`). UI: Vitest, colocated `*.test.tsx`.

### 3.3 Open-Meteo fetch — the "Detect location" button

Currently `handle_refresh_weather` emits a placeholder error. Implement `environment/openmeteo.py`:

- stdlib `urllib.request` only — **no new dependency**. Follow `cloud/client.py`.
- `GET https://api.open-meteo.com/v1/forecast?latitude=..&longitude=..&current=temperature_2m,relative_humidity_2m,surface_pressure`
- **Pass `elevation=<user's actual>`.** Open-Meteo's `surface_pressure` is station pressure at the *model's terrain elevation*, not the user's. Pressure changes ~12 Pa/m, so a 100 m mismatch is ~1.2% density ≈ 0.9 yd. Read back the response `elevation` field and warn on a large gap.
- Hard timeout; all exceptions → `None`; never called from the shot path
- IP-based location lookup for the initial guess, opt-in, always editable. `navigator.geolocation` is unreliable on this hardware (Raspberry Pi OS Chromium ships without Google API keys) — use it opportunistically at most.
- **No polling.** Fetch on setup and on user tap only.
- Attribution: Open-Meteo is CC BY 4.0 — add to `README.md`.

**The live JSON shape has not been verified** — the author's fetch tool returned empty for the API. Confirm it on the Pi before finalising the parser.

### 3.4 Remaining plumbing

- `session_logger.py:429-438` — add the env fields to the optional block
- `docs/CHANGELOG.md` — `[Unreleased]` entry (required by CONTRIBUTING step 6)
- `README.md` — user-facing feature, needs documenting
- `scripts/start-kiosk.sh` — `--weather*` flag passthrough (CLI flags currently only work when invoking `server.py` directly)

---

## 4. File manifest

One PR (see §0). The two groups below are the seam to split along **only if a maintainer asks**.

**Air density — the core:**
```
src/openflight/environment/__init__.py          new
src/openflight/environment/density.py           new
src/openflight/environment/config.py            new  (minus standard_* fields)
src/openflight/environment/provider.py          new  (minus standard_density)
src/openflight/ballistics.py                    CARRY_DENSITY_EXPONENT, density_carry_factor
src/openflight/launch_monitor.py                5 Shot env fields, _density_factor
src/openflight/server.py                        provider, carry paths, sockets, CLI
ui/src/stores/useEnvironmentStore.ts            new
ui/src/components/WeatherSettings.{tsx,css}     new  (minus the standard section)
ui/src/components/SettingsView.{tsx,css}        new
ui/src/utils/units.ts                           temp/pressure/elevation converters
ui/src/types/socket.ts                          EnvironmentReading, WeatherSettings
ui/src/services/socketService.ts                3 listeners, 2 emitters
ui/src/App.tsx                                  'settings' view + nav
tests/test_environment_density.py               new
```

**Standard carry — the display layer on top:**
```
src/openflight/environment/config.py            STANDARD_*, show_standard, standard_temp_c/elevation_m
src/openflight/environment/provider.py          standard_density()
src/openflight/launch_monitor.py                carry_standard_yards
src/openflight/server.py                        _apply_standard_carry + shot_to_dict field
ui/src/types/shot.ts                            carry_standard_yards
ui/src/components/ShotDisplay.{tsx,css}         MetricCard `note` slot, std line
ui/src/components/WeatherSettings.tsx           the weather-standard section
```

---

## 5. Design decisions — do not silently reverse these

1. **The sensor outranks fetched weather.** An API gives outdoor grid-cell averages; in a 22 °C garage on a 36 °C day that correction is actively wrong. The sensor cannot have that failure mode.
2. **A single `CARRY_DENSITY_EXPONENT`, not a per-club table.** The table path only runs when ballistics is off or no launch angle was measured — an estimate its own docstring puts at ±10-15%. Curve-fit precision on top of that is false precision, and it would go stale if the aero coefficients are retuned.
3. **No carry-calibration multiplier. Ever.** R10 users are documented setting 10,000 ft elevation in E6 to make distances look right — air 31% thinner than sea level. Running that through this repo's integrator shows it cannot even work: spin errors push drivers *long* and irons *short*, so one density fudge cannot fix both. When carry looks wrong the cause is almost always estimated spin. That is why the elevation field warns above 2500 m and points at `spin_source`.
4. **"Standard", not "normalized".** TrackMan's normalization removes wind *and* density because it tracked the real flight. OpenFlight never sees the wind — neither radar can. The OPS243 is ~6-7 m on a golf-ball RCS, the IWR6843 is range-gated tighter; both see under 3% of a driver's flight. Claiming "normalized" would overstate the capability.
5. **Values are SI on the wire**, converted only for display and entry via the existing `useUnitPreferenceStore`.
6. **Standard carry is computed lazily** — only when enabled *and* deviation ≥0.5%. It is a second ~50-85 ms RK4 integration on a Pi 5; below that threshold both figures round to the same yardage anyway.
7. **Weather never blocks a shot.** All I/O is on timers or user actions; `_apply_environment` is a pure snapshot read.

---

## 6. PR description requirements

CI fails on empty sections (`CONTRIBUTING.md:130-141`). Title: **`feat(weather): correct carry for air density`**

**Why it was required.** Every carry number OpenFlight has produced assumed ISA sea level — 15 °C, 1013.25 hPa, dry — with no way to change it. `ballistics.py:37` hardcodes `AIR_DENSITY_STD = 1.225`, `server.py` called `simulate(conditions)` with the default, and the table-estimator path had no density parameter at all. The gap is already documented in the code: `launch_monitor.py:95-96` lists "Weather conditions" and "Altitude" among the unmodeled ±10-15% error sources.

Measured against this repo's own integrator, that assumption was worth **5.5 yd on a driver in Sacramento on 2026-07-29** (97 °F, 1010 hPa — a sea-level venue, so pure temperature error) and **~14 yd in Denver**. The same swing appears within a single day: morning to afternoon at one venue moved a driver 4.8 yd, and OpenFlight reported both as identical.

The standard-conditions figure answers the follow-on question — *"am I hitting it further, or is it just hot?"* — by running the same correction against a fixed reference so sessions on different days are comparable.

**Automated tests.** List every file added. Do not open the PR with only `test_environment_density.py` — see §3.2.

**Manual testing.** "Tests pass" is explicitly rejected by CONTRIBUTING. Document at minimum:

- `--mock` in each source mode (auto / manual / off), with the observed density and carry for each
- The settings screen on the actual 7" panel — touch targets, scrolling, no clipped text
- Unit switching mid-session: values must convert, not reinterpret the digits
- A deliberately corrupted `weather.json` — the server must still boot
- `--weather-temp-c 36 --weather-elevation-m 9` against the expected ~~1.1338~~ **1.1279** kg/m³ — corrected 2026-07-30: the flag defaults humidity to 50%, and 1.1338 is the same conditions at 25% RH. Add `--weather-humidity 25` to get 1.1338.
- The standard line appearing above 0.5% deviation and hiding below it
- No weather configured: carry numbers unchanged from before the branch
