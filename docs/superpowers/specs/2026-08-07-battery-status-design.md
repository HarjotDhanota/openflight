# Power Status: Battery Level and Supply Health

*Design doc. Branch `feat/battery-status`, based on `upstream/main` (98466df). Written 2026-08-07.*

## What this does

**Shows how much battery is left, and whether the supply is about to fail.** A battery
indicator with a percentage, plus a separate dot for the health of the 5V rail feeding
the Pi. Optionally, an unattended shutdown before the cells are deep-discharged.

### What the user does

Nothing. If a supported UPS is fitted the indicator appears; if not, it doesn't.

### What the builder does, once

Nothing required. Everything below has a working default. Thresholds and the opt-in
shutdown live in `~/.config/openflight/power.json` and matching CLI flags for anyone
who wants to change them.

### What this cannot do

**Predict runtime accurately on a pack it has never seen discharge.** The estimate is
derived from observed voltage slope during the current session. Early in a session, or
after a hot-swap, it has nothing to extrapolate from and reports nothing rather than
guessing. See §5.4.

**Report state of charge while charging.** Charge current inflates terminal voltage —
measured on the bench at 3.850 V resting versus 4.088 V minutes into a charge on the same
pack. On wall power the indicator shows a charging state, not a percentage it cannot
justify. See §5.3.

**Detect rail health on anything but a Pi 5.** The rail reader is `vcgencmd pmic_read_adc`,
which is Pi 5 only. On a Pi 4 the pack half still works and the rail dot is absent.

---

## 1. Why this exists

A Pi 5 running from a Geekworm X1209 reported `Reset due to low power event. Please check
your power supply` at boot — the DA9091 PMIC latching a brownout on the previous boot.

The system had no way to show that had happened, and no way to show it was about to happen
again. Two distinct failure modes were invisible:

| Failure | Symptom | Currently visible? |
|---|---|---|
| Pack exhausted | Session ends, cells deep-discharged | No |
| Rail sags under load | Uncommanded reset, possible SD corruption | No |

These are independent. A full pack with a sagging rail and an empty pack with a clean rail
both end the session, for different reasons, requiring different responses. **One bar cannot
express both**, which is why §3 uses two readers and §8 draws two indicators.

The USB current budget in `docs/hardware-integration.md` §5.3 is the mechanism behind the
second row: a Pi 5 fed over the 40-pin 5V rail cannot negotiate USB-C PD, so the firmware
assumes a weak supply and caps total USB current at 600 mA.

---

## 2. What is measurable, and what was verified

All of the following was confirmed on hardware (Pi 5, X1209 + X12-A1, 4× Samsung 35E 1S4P)
before this document was written.

### 2.1 Pack — MAX1704x fuel gauge, I²C `0x36`

| Register | Contents | Conversion |
|---|---|---|
| `0x02` | VCELL | byteswap, × 78.125 µV |
| `0x04` | SOC | byteswap, ÷ 256 → percent |

SMBus reads little-endian; the gauge is big-endian, so every word needs the swap. Verified:
`i2cget -y 1 0x36 0x02 w` returned `0x60cc` → `0xcc60` = 52320 → **4.088 V**.

Covers the Geekworm X120x/X12xx family and UPS-Lite. Support for other gauges is out of
scope — see §11.

### 2.2 Source — power-loss detection, GPIO 6

Verified in **both** directions, which matters because a pin that never changes is
indistinguishable from a pin that isn't connected:

| State | `pinctrl get 6` |
|---|---|
| Wall power connected | `hi` |
| On battery | `lo` |

The pin must be configured as an input with a pull-up first (`pinctrl set 6 ip pu`); it reads
`no ... --` until then, which is "unconfigured", not "low". With the pull-up enabled, `lo`
means something is actively driving it down — a floating pin reads `hi`. That is the evidence
the line is genuinely connected.

### 2.3 Rail — Pi 5 PMIC

`vcgencmd pmic_read_adc` exposes `EXT5V_V` as `volt(24)`. `vcgencmd get_throttled` reports
undervoltage the firmware has **already detected** — bit 0 live, bit 16 since boot.

This is the important one: it needs no calibration, because the firmware is doing the
detection. Bench baseline, idle on battery:

```
rail       min 5.211 V   mean 5.215 V
pack       3.850 V
throttled  0 non-zero
```

Comfortably healthy, which is the sanity check that §5.2's thresholds sit in the right place.

### 2.4 Charging distorts voltage

Pack read 3.850 V on battery, then 4.088 V a few minutes after wall power was reconnected.
That rise is charge current, not stored energy. Any voltage-derived percentage over-reads
while charging. §5.3 handles it.

---

## 3. Architecture

New package `src/openflight/power/`, following the shape of `inclinometer/`.

```
src/openflight/power/
  __init__.py
  models.py      PackReading · RailReading · PowerSnapshot   (frozen dataclasses)
  gauge.py       BatteryGauge Protocol
  max1704x.py    MAX1704x over I²C + PLD on GPIO — the only gauge shipped
  pmic.py        Pi 5 rail health via vcgencmd
  service.py     threaded sampler, smoothing, snapshot retention
  policy.py      snapshot -> health level -> warning / shutdown decision
  shutdown.py    the only module that can halt the machine
  config.py      ~/.config/openflight/power.json
```

### 3.1 Two independent readers

The pack gauge and the rail monitor answer different questions on different buses, and
either can be absent:

| Build | Fuel bar | Rail dot |
|---|---|---|
| Pi 5 + supported UPS | yes | yes |
| Pi 5, wall power, no UPS | — | yes |
| Pi 4 + supported UPS | yes | — |
| Pi 4, wall power | — | — |

`PowerSnapshot` holds both as `Optional`. A missing half degrades to a missing indicator,
never to a broken subsystem. The last row is the upstream guarantee: no hardware, no config,
status `unavailable`, component renders nothing, **zero change for every existing builder**.

### 3.2 Why `policy.py` is separate from `service.py`

The service samples and smooths. It holds no opinion and cannot halt the machine.

`policy.py` turns a snapshot into `ok` / `warn` / `critical` and is the only thing that can
call for shutdown. It is pure functions over a dataclass — no thread, no bus, no clock — so
the part with real consequences is table-driven-testable without mocking anything.

`shutdown.py` is separated again for the same reason: exactly one function that runs
`systemctl poweroff`, trivially stubbed in tests, so no test run can power off a dev machine.

### 3.3 Why `gauge.py` is a Protocol with one implementation

Deliberately narrow — `initialize()`, `read() -> PackReading`, `close()`, plus optional
`on_battery() -> bool | None`. Nothing MAX-specific leaks into it.

This is not speculative generality. It is the same `Protocol` boundary `inclinometer/service.py`
already uses for `Accelerometer`, and it is what makes the service testable with a fake instead
of an I²C bus. That a second backend becomes a new file rather than a refactor is a side
benefit, not the justification.

---

## 4. Data model

```python
@dataclass(frozen=True)
class PackReading:
    timestamp: float
    volts: float
    percent: float | None      # None when the gauge reports SOC we do not trust
    on_battery: bool | None    # None when no PLD line is available

@dataclass(frozen=True)
class RailReading:
    timestamp: float
    ext5v_volts: float
    throttled: int             # raw get_throttled bitmask

@dataclass(frozen=True)
class PowerSnapshot:
    timestamp: float
    pack: PackReading | None
    rail: RailReading | None
    status: str                # "ok" | "unavailable" | "sensor_error"
```

`status` mirrors `SnapshotSelection.status` in `inclinometer/models.py` — the same vocabulary
for the same idea, so a reader who knows one knows the other.

---

## 5. States and thresholds

### 5.1 Source state

From GPIO 6: `wall` (hi) / `battery` (lo) / `unknown` (no PLD line, or unreadable).
Everything downstream must tolerate `unknown` — that is the state for every UPS whose
power-loss line is not on GPIO 6.

### 5.2 Rail health

No calibration required; two of the three inputs are the firmware's own verdict.

| Level | Condition |
|---|---|
| green | `EXT5V_V ≥ 5.0` and `throttled == 0x0` |
| amber | `4.9 ≤ EXT5V_V < 5.0`, or any since-boot throttle bit |
| red | `EXT5V_V < 4.9`, or live undervoltage bit 0 |

4.9 V is Raspberry Pi's documented floor for a GPIO-powered Pi 5, not a local invention.
The measured idle baseline of 5.211 V sits mid-green.

### 5.3 Pack health

**Evaluated only when the source state is `battery`.** On `wall`, §2.4 applies: the reading
is inflated by charge current, so the indicator shows charging and no percentage is claimed.

On `unknown` — a UPS whose power-loss line is not on GPIO 6 — pack health is evaluated as if
on battery, **except that shutdown never fires** (§6 condition 2 requires `battery`
explicitly). The reasoning: a percentage that is occasionally pessimistic while charging is
tolerable, but halting a machine that might be on mains is not. Warnings still show, so the
degradation is visible rather than silent.

Defaults for 1S 18650, all overridable:

| Level | Pack V | Effect |
|---|---|---|
| ok | ≥ 3.6 | — |
| low | 3.4 – 3.6 | warning |
| critical | 3.3 – 3.4 | persistent warning |
| shutdown | ≤ 3.2 | halt, only if enabled |

3.2 V is Geekworm's own figure from their reference script, not a guess.

### 5.4 Runtime estimate

Least trustworthy output here, so it is the most conservative. Derived from the observed
voltage slope over the current session only. Reported as `None` — and rendered as nothing —
until at least 10 minutes of on-battery samples exist and the slope is monotonic within
tolerance. A wrong estimate is worse than no estimate.

### 5.5 Hysteresis and dwell are not optional

The pack was measured moving 3.850 → 3.853 V across two seconds at idle. Under load it will
dip several hundred millivolts and recover. Without debouncing, a transient sag triggers a
shutdown mid-session.

- Entering a level requires the condition to hold for `dwell_samples` consecutive reads
  (default 15 ≈ 30 s at the 2 s sample interval)
- Leaving a level requires rising `deadband_volts` above the threshold (default 0.05)

Both configurable. Both are the reason §10 tests flapping explicitly.

---

## 6. Shutdown policy

Opt-in, default **off**.

Fires only when *all* hold:

1. `auto_shutdown_enabled` is true
2. source state is `battery` — never on wall power, under any circumstance
3. pack volts ≤ `shutdown_volts`
4. condition sustained past `dwell_samples`
5. not charging

The UI is notified first and shows a countdown (`shutdown_grace_seconds`, default 60) which
any UI interaction cancels for the remainder of the session. Then `shutdown.py` runs
`systemctl poweroff`.

Default-off is deliberate. Software that turns the machine off is software a user must
opt into, and the failure mode of a wrong threshold — a session ended mid-round — is more
visible than the failure mode it prevents.

---

## 7. Server integration

Mirrors `init_inclinometer` at `server.py:1136`:

- module-level `power_service` and `power_runtime_config: dict = {"enabled": False}`
- `init_power(...) -> bool`, returning False on any hardware failure, never raising
- registered in the shutdown sequence alongside `inclinometer stop` (`server.py:202`)
- included in the config dict at `server.py:855`

Transport: a `power` socket event emitted on health-level change and on a 10 s heartbeat.
Battery state moves slowly; the shot path is latency-critical and must not carry this.

**The sampling thread never blocks the shot path.** I²C reads take ~1 ms and `vcgencmd`
forks a process — both are done on the power thread, and the emit reads only the retained
snapshot.

---

## 8. UI

Two indicators, per §1:

```
  ▮▮▮▮▮▮▯▯▯▯  62%   ●
                    └─ rail health
```

Tapping the indicator expands a detail panel:

```
Pack   3.81 V · 62% · on battery
Rail   5.09 V · no throttling
Est.   ~48 min           (omitted entirely until §5.4 is satisfied)
```

- `ui/src/components/BatteryStatus.tsx` — bar, percentage, rail dot, expandable detail
- `ui/src/stores/usePowerStore.ts` — zustand, matching `useEnvironmentStore`
- warning banner on `low` / `critical`, and the shutdown countdown

Rail dot colours map directly to §5.2 — green / amber / red, no separate scale.

Renders `null` when status is `unavailable`, which is what makes the no-hardware guarantee
real rather than aspirational.

---

## 9. Configuration

`~/.config/openflight/power.json`, following `environment/config.py` — load returns defaults
on a missing, unreadable, or malformed file, because a corrupt config must never stop the
launch monitor from starting.

| Key | Default |
|---|---|
| `enabled` | `"auto"` — probe, use it if present |
| `sample_interval_s` | `2.0` |
| `rail_amber_volts` / `rail_red_volts` | `5.0` / `4.9` |
| `pack_low_volts` / `pack_critical_volts` | `3.6` / `3.4` |
| `shutdown_volts` | `3.2` |
| `auto_shutdown_enabled` | `false` |
| `shutdown_grace_seconds` | `60` |
| `dwell_samples` | `15` |
| `deadband_volts` | `0.05` |
| `pld_gpio` | `6` |
| `i2c_address` | `0x36` |

CLI flags follow the `--inclinometer` convention: `--power`, `--power-shutdown`,
`--power-shutdown-volts`, and `scripts/start-kiosk.sh` passthrough.

The package is `power/` rather than `battery/` because it monitors the rail too, which is
not a battery. The user-facing indicator is still a battery.

---

## 10. Testing

`policy.py` being pure functions is what makes this cheap. Table-driven over `PowerSnapshot`:

- every rail and pack level boundary, both directions
- **flapping** — a value oscillating across a threshold must not oscillate the level
- **transient sag** — a single low read inside a healthy window must not trigger shutdown
- **charging inflation** — high volts on `wall` must not report a percentage
- **shutdown interlocks** — each of §6's five conditions independently blocks the halt
- `unknown` source state at every decision point

Driver tests: byte-swap math against the verified `0x60cc → 4.088 V`, missing-hardware
fallback, and I²C errors surfacing as `sensor_error` rather than exceptions.

`shutdown.py` is stubbed throughout. No test may power off the machine running it.

---

## 11. Scope boundaries

**In this PR:** backend, socket event, indicator, config file and CLI flags.

**Not in this PR:**

- *Settings UI.* `SettingsView.tsx` does not exist on `upstream/main`; it lives on
  `test/pi-full`. Toggles live in `power.json` and CLI flags until then. This mirrors how the
  weather subsystem shipped — see `environment/config.py:6-9`.
- *Settings subtabs (PR 2).* Weather / Interface tabbed shell, hosting `BatterySettings`.
  Depends on the weather work landing upstream first.
- *Bubble level UI (PR 3).* The inclinometer backend is upstream as of #199 with no UI
  anywhere. The Interface tab from PR 2 is its natural home.
- *Other gauges.* INA219, PiSugar, PiJuice. The `Protocol` accommodates them; nothing ships.

---

## 12. Open items

1. **Loaded-on-battery rail margin is unmeasured.** The 5.211 V baseline is idle. The
   thresholds in §5.2 come from Raspberry Pi's documented floor and the firmware's own
   flags, so they do not depend on this — but the real margin under load is unknown, and
   the system that would be measured today lacks the IWR6843 the power budget assumes.
   The service collects exactly this data once running.
2. **MAX1704x SOC accuracy on a 4P pack is unquantified.** ModelGauge is fitted to a single
   LiPo cell. `percent` may need to come from a voltage curve instead. Deciding this needs
   the discharge data from item 1.
3. **PLD line on non-Geekworm HATs.** GPIO 6 is verified for the X1209 only. Other boards
   report `unknown` and lose on-battery detection, which degrades §5.3 to "no pack health".
