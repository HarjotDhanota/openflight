# Title

feat(carry): correct carry for air density — sensor / API / manual scope, and which "standard conditions" reference

# Description

Every carry number OpenFlight produces assumes ISA sea-level air (`AIR_DENSITY_STD = 1.225` in `ballistics.py` — 15 °C, 1013.25 hPa, dry). Air density is the largest *systematic* error left in the carry pipeline, and it is currently unmeasured.

Measured with this repo's own RK4 integrator (driver 165 mph / 12.5° / 2600 rpm):

| Condition | ρ (kg/m³) | Driver carry | Δ vs assumed |
|---|---|---|---|
| ISA standard | 1.2250 | 256.5 yd | — |
| 97 °F afternoon, sea level | 1.126 | 262.3 yd | **+5.8 yd** |
| Denver, 25 °C | 0.970 | 270.9 yd | **+14.3 yd** |

The same swing drifts several yards across a single session as the day heats or cools, and OpenFlight reports all of it as identical conditions.

I have this working on a fork and would like guidance on **scope** and **one design decision** before opening PRs, rather than after.

## Question 1 — which sources, and in what order?

The prior art splits three ways: Foresight (GCQuad/GC3) has an **onboard barometer applied automatically**; TrackMan has the user **type** altitude/temperature into TPS; Garmin R10 uses phone location. I have three source tiers built and tested, and they layer cleanly:

1. **BME280/BMP280 sensor on I²C** (~$15) — measures the air the ball actually flies through. Forced-mode driver (self-heating is the dominant error term), fail-soft everywhere, chip-ID detection because boards are widely mislabelled. Opt-in via `--air-sensor`; with no sensor fitted every carry number is byte-identical to today.
2. **Open-Meteo fetch + typed manual entry** — no hardware required, but an API reports *outdoor grid-cell* conditions, so in a 22 °C garage on a 36 °C day its "correction" is worse than none. That asymmetry is why the sensor outranks it.
3. **ISA default** — exactly current behaviour.

Options, smallest first:
- **(a) Sensor only** (~2.8k lines incl. tests: driver + provider + shot/session fields + a read-only Conditions tab)
- **(b) API + manual only** (no new hardware dependency, but inherits the indoor-wrongness caveat)
- **(c) Both, sensor-first precedence** (the Foresight model; largest total surface, splits into a reviewable stack)

Which scope would you actually want to maintain?

## Question 2 — what should "standard conditions" mean?

A second, optional carry figure re-flown in fixed reference air makes sessions on different days comparable (TrackMan calls this Normalization). But two reference conventions exist and they disagree:

| Reference | Air | Same driver strike reads |
|---|---|---|
| **ISA** (aviation/physics standard; what this repo's tables already encode) | 15 °C, sea level, dry — 1.2250 | 256.5 yd |
| **TrackMan** (de facto golf standard) | 77 °F, sea level, ~50% RH — 1.1769 | 259.4 yd (**+2.9**) |

Whole-bag gap is +2.2 to +2.9 yd (~1.1–1.4%) under the TrackMan reference. Options:

- **ISA default** — physically standard, and the uncorrected table numbers already *are* ISA-normalized, so historical sessions stay comparable.
- **TrackMan default** — directly comparable to the sim/fitting numbers most golfers already know.
- **Both as labelled presets** (my current build defaults to ISA) — costs a little UI.

Happy to open whichever slice of this as PRs (each layer is independently tested and each commit passes the full suite standalone); mainly I don't want to land a 3k-line surprise, and the reference choice affects every user-visible number, so it should be the project's call, not mine.

# Motivation

- Removes a 5–14 yd systematic bias for a ~$15 part or a free API call.
- Recording per-shot air density (with provenance, like `spin_source`) makes session logs interpretable across days — without it a hot afternoon is silently compared against a cold morning.
- Deliberately does **not** touch simulator output: GSPro re-flies launch data in the virtual course's own air, so OpenConnect `CarryDistance` stays the uncorrected figure (correcting it would apply density twice).
- No carry-calibration multiplier anywhere — if numbers look wrong the cause is nearly always `spin_source == "club_typical"`, and a density knob would just let users fudge it (the documented R10-in-E6 failure mode).

# Area

Carry / distance model

# Additional context

Working branches on my fork (all rebased on current main, full test suite + lint green per commit):
- `feat/bme280-air-density` — scope (a), standalone
- `feat/weather-engine` → `feat/weather-server` → `feat/weather-ui` — scope (c) as a reviewable stack

Design doc with the error budget, prior-art survey, and normalization research: `docs/plans/2026-07-28-weather-sensor-design.md` on the fork.

Caveat stated up front: the BME280 compensation math is cross-checked against the datasheet's floating-point formulation with mutation-tested register-level fakes, but has not yet been validated against a physical chip (mine is in the mail). I would not ask anyone to merge the sensor path before that's done.
