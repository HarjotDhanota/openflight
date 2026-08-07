# Power Status: Battery Level and Supply Health

*Design doc. Branch `feat/battery-status`, based on `upstream/main` (98466df). Written
2026-08-07, revised after design audit.*

## What this does

**Shows how much battery is left, and whether the supply is about to fail.** A battery
indicator with a percentage, plus a separate dot for the health of the 5V rail feeding
the Pi. Optionally, an unattended shutdown before the cells are deep-discharged.

### What the user does

Nothing. If a supported UPS is declared in config the indicator appears; if not, it doesn't.

### What the builder does, once

Declare the board — `"board": "x1209"` in `power.json`, or `--power-board x1209`. Everything
else has a working default. **Nothing is probed or reconfigured without that declaration**
(§9.3), because auto-configuring a GPIO on a machine whose wiring we do not know is not a
safe default.

### What this cannot do

**Predict runtime accurately on a pack it has never seen discharge.** Derived from observed
voltage slope in the current session. Reports nothing until it has enough history. See §5.4.

**Know whether the charger is actually charging.** The PLD line reports *external power
present*, which is not the same thing — a full pack on mains draws no charge current. There
is no charger-status signal on this hardware, so the UI says "external power", never
"charging". See §5.3.

**Detect rail health on anything but a Pi 5.** The rail reader is `vcgencmd pmic_read_adc`,
Pi 5 only. On a Pi 4 the pack half still works and the rail dot is absent.

**Report per-cell state.** The X12-A1 wires its four 18650s in parallel (1S4P), confirmed by
the measured 3.850 V pack voltage — a series pack would read ~15 V. Parallel cells share one
terminal voltage and are electrically indistinguishable, so the percentage covers the whole
14 Ah pack. A single degraded cell is held up by the other three: the gauge keeps reporting
healthy while real runtime shrinks. Per-cell sense wires would be required; the X12-A1 has
none.

---

## 1. Why this exists

A Pi 5 running from a Geekworm X1209 reported `Reset due to low power event. Please check
your power supply` at boot — the DA9091 PMIC latching a brownout on the previous boot.

The system had no way to show that had happened, or that it was about to happen again. Two
distinct failure modes were invisible:

| Failure | Symptom | Currently visible? |
|---|---|---|
| Pack exhausted | Session ends, cells deep-discharged | No |
| Rail sags under load | Uncommanded reset, possible SD corruption | No |

These are independent. A full pack with a sagging rail and an empty pack with a clean rail
both end the session, for different reasons, needing different responses. **One bar cannot
express both**, which is why §3 uses independent readers and §8 draws two indicators.

---

## 2. What is measurable, and what was verified

Confirmed on hardware (Pi 5, X1209 + X12-A1, 4× Samsung 35E 1S4P) before this was written.

### 2.1 Pack — MAX1704x fuel gauge, I²C `0x36`

| Register | Contents | Conversion |
|---|---|---|
| `0x02` | VCELL | byteswap, × 78.125 µV |
| `0x04` | SOC | byteswap, ÷ 256 → percent |

SMBus reads little-endian; the gauge is big-endian, so every word needs the swap. Verified:
`i2cget -y 1 0x36 0x02 w` returned `0x60cc` → `0xcc60` = 52320 → **4.088 V**.

`0x04` is **ModelGauge SOC** — a modeled state of charge that tracks continuously across
charge and discharge. It is *not* a lookup on instantaneous VCELL, and §5.3 must not treat
it as one.

Covers the Geekworm X120x/X12xx family and UPS-Lite. Other gauges are out of scope (§11).

### 2.2 External power — PLD line, GPIO 6 on the X1209

Verified in **both** directions:

| State | `pinctrl get 6` |
|---|---|
| Wall power connected | `hi` |
| On battery | `lo` |

The pin needs configuring as an input with pull-up first (`pinctrl set 6 ip pu`); until then
it reads `no ... --`, which is "unconfigured", not "low".

**This is exactly why `hi` cannot be trusted on its own.** With a pull-up, an *unwired* pin
also reads `hi`. On a board with no PLD line, "hi" is indistinguishable from "wall power
connected" — so a naive mapping would report battery operation as mains, suppress warnings,
and disable shutdown precisely on the builds least able to detect the problem. §5.1 handles
this; §9.3 keeps us from touching the pin at all unless a board declares it.

### 2.3 Rail — Pi 5 PMIC

`vcgencmd pmic_read_adc` exposes `EXT5V_V` as `volt(24)`. `vcgencmd get_throttled` returns a
bitmask; only two bits concern supply health:

| Bit | Meaning | Used for rail health? |
|---|---|---|
| 0 (`0x1`) | Undervoltage now | **Yes** |
| 1 (`0x2`) | ARM frequency capped | No |
| 2 (`0x4`) | Currently throttled | No |
| 3 (`0x8`) | Soft temperature limit | No |
| 16 (`0x10000`) | Undervoltage has occurred | **Yes** |
| 17–19 | Capping / throttling / soft-limit have occurred | No |

Bits 1–3 and 17–19 are thermal and frequency management. Treating them as supply faults
would paint a hot-but-electrically-healthy Pi amber — and this build lives in a sealed IP54
enclosure on a summer range, so thermal throttling is *expected*, not exceptional. Rail
health masks `0x10001` and nothing else.

Bench baseline, idle on battery:

```
rail       min 5.211 V   mean 5.215 V
pack       3.850 V
throttled  0 non-zero
```

### 2.4 External power raises terminal voltage

Pack read 3.850 V on battery, then 4.088 V minutes after wall power was reconnected. That
rise is charge current, not stored energy.

**This does not invalidate the gauge's SOC**, which is modeled precisely to handle it (§2.1).
It does mean any *voltage-derived* percentage over-reads while charging — which matters
because §5.3's health levels are voltage-based.

---

## 3. Architecture

New package `src/openflight/power/`, following the shape of `inclinometer/`.

```
src/openflight/power/
  __init__.py
  models.py      PackReading · RailReading · SourceReading · PowerSnapshot · PowerView
  gauge.py       BatteryGauge Protocol
  source.py      PowerSourceReader Protocol + GpioPldSource
  max1704x.py    MAX1704x over I²C — the only gauge shipped
  pmic.py        Pi 5 rail health via vcgencmd
  service.py     threaded sampler, snapshot retention
  policy.py      (PolicyState, PowerSnapshot) -> (PolicyState, Decision)
  shutdown.py    the only module that can halt the machine
  config.py      ~/.config/openflight/power.json
```

### 3.1 Three independent readers

Pack gauge, power source, and rail monitor answer different questions on different buses.
**Each can be absent independently**, so each carries its own status (§4) — a single global
status cannot express "gauge working, PLD missing, rail fine".

| Build | Fuel bar | Source | Rail dot |
|---|---|---|---|
| Pi 5 + X1209, board declared | yes | yes | yes |
| Pi 5 + UPS, no board declared | yes | unknown | yes |
| Pi 5, wall power, no UPS | — | — | yes |
| Pi 4 + UPS, board declared | yes | yes | — |
| Pi 4, wall power | — | — | — |

The last row is the upstream guarantee: no hardware, no config, nothing rendered, **zero
change for every existing builder**. `init_power()` returns `True` if *any* reader
initialised — returning `False` on any single failure would contradict this matrix.

### 3.2 Why `source.py` is separate from `gauge.py`

Power-source detection is not a property of the fuel gauge. The X1209 puts it on a GPIO; a
PiJuice reports it over I²C; many boards have nothing. Folding it into `BatteryGauge` as an
optional method would couple two independently-absent capabilities and make the floating-pin
problem in §2.2 a gauge concern, which it is not.

### 3.3 Why `policy.py` is a reducer, not pure functions

An earlier draft claimed policy was "pure functions over a dataclass". That was wrong: dwell
counting, hysteresis, runtime history, and cancellation latching all require retained state
across samples.

```python
def step(state: PolicyState, snapshot: PowerSnapshot, now_monotonic: float
        ) -> tuple[PolicyState, Decision]:
```

Deterministic and fully testable — feed a list of snapshots, assert the decision sequence —
without a thread, a bus, or a wall clock. `now_monotonic` is a parameter, never read inside,
so durations are exact in tests and immune to NTP steps at boot.

`shutdown.py` is separated again: one function running `systemctl poweroff`, trivially
stubbed, so no test run can power off a dev machine.

### 3.4 Why `gauge.py` is a Protocol with one implementation

Narrow — `initialize()`, `read() -> PackReading`, `close()`. It is the same `Protocol`
boundary `inclinometer/service.py` already uses for `Accelerometer`, and it is what makes the
service testable with a fake instead of an I²C bus. A second backend becoming a new file is a
side benefit, not the justification.

---

## 4. Data model

Every reader carries its own status. There is no global one.

```python
ReaderStatus = Literal["ok", "absent", "error"]

@dataclass(frozen=True)
class PackReading:
    status: ReaderStatus
    timestamp: float
    volts: float | None
    percent: float | None          # ModelGauge SOC
    error: str | None = None

@dataclass(frozen=True)
class RailReading:
    status: ReaderStatus
    timestamp: float
    ext5v_volts: float | None
    throttled: int | None          # raw mask; rail health uses 0x10001 (§2.3)
    error: str | None = None

@dataclass(frozen=True)
class SourceReading:
    status: ReaderStatus
    timestamp: float
    state: Literal["external", "battery", "unknown"]
    error: str | None = None

@dataclass(frozen=True)
class PowerSnapshot:
    timestamp: float
    pack: PackReading
    rail: RailReading
    source: SourceReading
```

`PowerView` is the serialized shape sent to the UI — snapshot plus the policy's conclusions,
so the client renders and never re-derives:

```python
@dataclass(frozen=True)
class PowerView:
    pack_volts: float | None
    pack_percent: float | None
    pack_level: Literal["ok", "low", "critical", "unknown"]
    rail_volts: float | None
    rail_level: Literal["green", "amber", "red", "unknown"]
    source: Literal["external", "battery", "unknown"]
    runtime_minutes: int | None
    shutdown_eligible: bool
    pending_shutdown: PendingShutdown | None
    warnings: list[str]
```

---

## 5. States and thresholds

### 5.1 Source state, and the floating-pin rule

`external` / `battery` / `unknown`.

A pin reading `hi` is only reported as `external` when **either**:

1. the configured board profile declares a PLD line (§9.3), **or**
2. this process has previously observed that pin read `lo` — proving it is driven

Until one holds, `hi` yields `unknown`. A `lo` reading always yields `battery` immediately;
it is unambiguous, since a pulled-up floating pin cannot read low.

Rule two exists for a builder who wires PLD to a non-default pin and configures it: the first
transition to battery latches the line as real for the rest of the session.

`unknown` is a first-class state. Everything downstream must handle it (§5.3, §6).

### 5.2 Rail health

| Level | Condition |
|---|---|
| green | `EXT5V_V ≥ rail_amber_volts` and `throttled & 0x10001 == 0` |
| amber | `rail_red_volts ≤ EXT5V_V < rail_amber_volts`, or bit 16 set |
| red | `EXT5V_V < rail_red_volts`, or bit 0 set |
| unknown | reader `absent` or `error` |

Defaults 5.0 / 4.9 V. **These are OpenFlight margins, not a published Raspberry Pi
threshold.** An earlier draft attributed 4.9 V to official documentation; that attribution
could not be substantiated and has been removed. The firmware's own undervoltage detection
trips lower than this, so amber is intended as early warning *before* the firmware complains,
and red should normally coincide with bit 0 rather than precede it. Both are configurable,
and §12 item 1 tracks calibrating them against a real loaded session.

### 5.3 Pack health

Non-overlapping, evaluated on **voltage**, not on SOC:

| Level | Condition |
|---|---|
| ok | `volts ≥ pack_low_volts` (3.6) |
| low | `pack_critical_volts ≤ volts < pack_low_volts` (3.4–3.6) |
| critical | `volts < pack_critical_volts` (< 3.4) |
| unknown | reader `absent` or `error` |

`shutdown_eligible` is a **separate boolean** — `volts ≤ shutdown_volts` (3.2) — not a level.
A pack below 3.2 V is therefore `critical` and visibly so whether or not auto-shutdown is
enabled. 3.2 V is Geekworm's figure from their reference script.

**On external power**, voltage is inflated (§2.4), so voltage-based levels would read
optimistically. Health is reported as `ok` and warnings suppressed while `source == external`
— running on mains is not a low-battery condition.

**The percentage is still shown on external power.** ModelGauge tracks across charge (§2.1),
so suppressing it would be discarding the one reading that stays valid. The UI labels the
state "external power" rather than "charging", since charger status is not measurable (see
"What this cannot do").

**On `unknown` source**, pack levels are evaluated as if on battery — a warning that turns
out to be spurious costs a glance, while a missed one costs the session. Shutdown never fires
(§6 condition 2).

### 5.4 Runtime estimate

Least trustworthy output, so the most conservative. Derived from voltage slope over the
current session, reported as `None` — rendered as nothing — until:

- ≥ 10 minutes of continuous `battery`-state samples, and
- slope is negative and monotonic within tolerance

History resets to empty on: source state change, a voltage discontinuity > 0.15 V between
consecutive samples (hot-swapped pack), or any pack reader error. A wrong estimate is worse
than no estimate.

### 5.5 Hysteresis and dwell

The pack moved 3.850 → 3.853 V across two seconds at idle; under load it will dip hundreds of
millivolts and recover. Without debouncing, a transient sag triggers shutdown mid-session.

- Entering a level requires the condition to hold `dwell_samples` consecutive reads
  (default 15 ≈ 30 s at 2 s interval)
- Leaving a level requires rising `deadband_volts` above the threshold (default 0.05)
- **A reader error resets the dwell counter**, and never counts toward a transition — a
  disconnected gauge must not accumulate its way into a shutdown

---

## 6. Shutdown protocol

Opt-in, default **off**. The server owns the state; the UI renders it.

### 6.1 Conditions to arm

All must hold:

1. `auto_shutdown_enabled`
2. `source.state == "battery"` — never on `external`, never on `unknown`
3. `pack.volts ≤ shutdown_volts`
4. sustained past `dwell_samples`
5. not already cancelled this session

Condition 2 subsumes the earlier draft's separate "not charging" clause, which was
unevaluable — there is no charger-status signal (§2.2).

### 6.2 State and events

```python
@dataclass(frozen=True)
class PendingShutdown:
    id: str                    # uuid4, new per arming
    deadline_monotonic: float  # absolute, not a countdown
    reason: str
```

| Event | Direction | Payload |
|---|---|---|
| `power` | server → all | `PowerView` (includes `pending_shutdown`) |
| `power_shutdown_pending` | server → all | `PendingShutdown` |
| `get_power` | client → server | — |
| `power_shutdown_cancel` | client → server | `{id}` |
| `power_shutdown_cancelled` | server → all | `{id}` |

**Absolute deadline, not a countdown**, so a client reconnecting mid-window computes the
remaining seconds correctly instead of restarting the clock.

`get_power` on connect follows the pattern `socketService.ts` already uses for `get_session`,
`get_trigger_status`, `get_radar_config` — so a newly connected UI has state immediately
rather than waiting up to the 10 s heartbeat.

### 6.3 Cancellation

An explicit **"Keep running"** button, not incidental interaction. A stray tap must not
silently defeat a protection the user enabled; equally, a user who wants to cancel should not
have to guess what counts as interaction.

- Any connected client may cancel. Cancellation is broadcast to all.
- A cancel carrying a stale `id` is ignored — this is the reconnect and double-click race.
- Cancelling **latches for the remainder of the process**: no re-arming, at any voltage.
  Re-arming a user who has said no, once a minute, until the pack dies, is worse than
  respecting the decision. The warning stays visible.

### 6.4 Execution

On deadline with no cancel:

1. emit a final `power` view marking execution
2. run the existing hardware cleanup used by `_cleanup_hardware_for_shutdown()` — radars and
   the sampling thread stopped before the machine goes down
3. `systemctl poweroff`

The existing shutdown path at `server.py:218` is `os._exit(0)`; it stops the *server process*.
Halting the *machine* is a new capability and must not be assumed to inherit its behaviour.

If `systemctl poweroff` fails — no privileges, no systemd — log it, emit a warning to the UI,
and **do not retry**. A failed halt is a visible degraded state, not a loop.

Default-off is deliberate. The failure mode of a wrong threshold, a session ended mid-round,
is more visible than the failure it prevents.

---

## 7. Server integration

Mirrors `init_inclinometer` at `server.py:1136`:

- module-level `power_service` and `power_runtime_config: dict = {"enabled": False}`
- `init_power(...) -> bool` — `True` if **any** reader initialised (§3.1); never raises
- registered in the shutdown sequence alongside `inclinometer stop` (`server.py:202`)
- included in the config dict at `server.py:855`

Transport: `power` emitted on any level change, on arming or cancelling a shutdown, and on a
10 s heartbeat. Battery state moves slowly; the shot path is latency-critical and must not
carry this.

**The sampling thread never blocks the shot path.** I²C reads take ~1 ms and `vcgencmd` forks
a process — both happen on the power thread. `vcgencmd` invocations get a hard timeout
(2 s); a hang marks the rail reader `error`, never stalls the loop. Emits read only the
retained snapshot. `stop()` is idempotent.

---

## 8. UI

```
  ▮▮▮▮▮▮▯▯▯▯  62%   ●
                    └─ rail health, colours per §5.2
```

Tapping expands:

```
Pack   3.81 V · 62% · on battery
Rail   5.09 V · no throttling
Est.   ~48 min                    (omitted until §5.4 is satisfied)
```

- `ui/src/components/BatteryStatus.tsx` — bar, percentage, rail dot, expandable detail
- `ui/src/stores/usePowerStore.ts` — zustand, matching the existing store conventions
- warning banner on `low` / `critical`, and the shutdown countdown with **"Keep running"**

Each element renders independently: an absent gauge hides the bar while the rail dot remains,
and vice versa. The whole component renders `null` only when all three readers are absent.

---

## 9. Configuration

`~/.config/openflight/power.json`, following the existing config-module conventions — load
returns defaults on a missing, unreadable, or malformed file, because a corrupt config must
never stop the launch monitor from starting.

### 9.1 Keys

| Key | Default | Validation |
|---|---|---|
| `board` | `null` | `null` or a known profile id |
| `enabled` | `true` | bool |
| `sample_interval_s` | `2.0` | finite, `0.5 ≤ x ≤ 60` |
| `rail_amber_volts` / `rail_red_volts` | `5.0` / `4.9` | finite, `red < amber` |
| `pack_low_volts` / `pack_critical_volts` | `3.6` / `3.4` | finite, `critical < low` |
| `shutdown_volts` | `3.2` | finite, `≤ pack_critical_volts` |
| `auto_shutdown_enabled` | `false` | bool |
| `shutdown_grace_seconds` | `60` | int, `10 ≤ x ≤ 600` |
| `dwell_samples` | `15` | int, `≥ 1` |
| `deadband_volts` | `0.05` | finite, `≥ 0` |
| `pld_gpio` | `null` | `null` or int `0 ≤ x ≤ 27` |
| `i2c_bus` / `i2c_address` | `1` / `54` | ints; address `0x08–0x77` |

`i2c_address` is **decimal 54**, not `0x36` — JSON has no hex literals. A string `"0x36"` is
also accepted and parsed, since that is what people will type.

Any key failing validation falls back to its default and logs a warning naming the key.
Unknown keys are ignored and logged. A whole-file rejection would turn one typo into a
non-booting launch monitor.

### 9.2 Precedence

CLI overrides file overrides default:

| Source | Wins over |
|---|---|
| `--power-*` flags | config file, defaults |
| `power.json` | defaults |
| `--no-power` | everything — hard disable, even if config enables it |

Flags follow the `--inclinometer` convention: `--power`, `--no-power`, `--power-board`,
`--power-shutdown`, `--power-shutdown-volts`, with `scripts/start-kiosk.sh` passthrough.

### 9.3 Nothing is touched without a declaration

`pld_gpio` defaults to `null` and **no GPIO is configured unless a board profile or explicit
config declares one**. Auto-probing GPIO 6 would silently reconfigure a pin on builds using
it for something else — which is not "zero change for existing builders", it is a regression
with a friendly description.

The `x1209` profile sets `pld_gpio: 6`. The I²C gauge is safe to probe without a declaration:
a read of address `0x36` that gets no ACK is a no-op.

The package is `power/` rather than `battery/` because it monitors the rail too, which is not
a battery. The user-facing indicator is still a battery.

---

## 10. Testing

### 10.1 Policy — table-driven over the reducer

- every rail and pack level boundary, both directions, including exact-threshold values
- **flapping** — a value oscillating across a threshold must not oscillate the level
- **transient sag** — a single low read inside a healthy window must not arm shutdown
- **reader error mid-dwell** — resets the counter, never accumulates toward shutdown
- **external power** — no warnings, percentage still reported, levels forced `ok`
- **`unknown` source** — warnings active, shutdown never arms
- **shutdown interlocks** — each of §6.1's five conditions independently blocks arming
- **cancel latch** — after cancel, no re-arm at any voltage for the process lifetime
- **runtime history reset** — on source change, on a > 0.15 V discontinuity, on error

### 10.2 Integration

- partial reader failure and later recovery — gauge dies, rail survives, view stays coherent
- initial sync on connect, and reconnect mid-countdown showing correct remaining seconds
- cancel races — stale `id`, double-cancel, cancel arriving after the deadline
- multiple clients — one cancels, all see it
- `systemctl poweroff` failing on permissions — warning surfaced, no retry loop
- hardware cleanup completes before halt is invoked
- malformed and timing-out `vcgencmd` output → `error` status, loop continues
- config validation: each bad value falls back independently; hex-string address parses
- `stop()` idempotent; thread joins within timeout

`shutdown.py` is stubbed throughout. **No test may power off the machine running it.**

---

## 11. Scope boundaries

**In this PR:** backend, socket events, indicator, config file and CLI flags.

**Not in this PR:**

- *Settings UI.* `SettingsView.tsx` does not exist on `upstream/main`; it lives on
  `test/pi-full`. Toggles live in `power.json` and CLI flags until then.
- *Settings subtabs (PR 2).* Weather / Interface tabbed shell hosting `BatterySettings`.
  Depends on the weather work landing upstream first.
- *Bubble level UI (PR 3).* The inclinometer backend is upstream as of #199 with no UI
  anywhere. The Interface tab from PR 2 is its natural home.
- *Other gauges.* INA219, PiSugar, PiJuice. The Protocols accommodate them; nothing ships.

---

## 12. Open items

1. **Loaded-on-battery rail margin is unmeasured.** The 5.211 V baseline is idle. §5.2's
   5.0 / 4.9 V are OpenFlight margins, not measured ones, and the system available to measure
   today lacks the IWR6843 the power budget assumes. The service collects this data once
   running; revisit the defaults with a real session in hand.
2. **ModelGauge SOC accuracy on a 4P pack is unquantified.** It is fitted to a single LiPo
   cell. It is used for display only — no decision depends on it, since §5.3 works on voltage
   — so an inaccuracy is cosmetic rather than dangerous. Quantifying it needs item 1's data.
3. **PLD is verified for the X1209 only.** Other boards report `unknown` source, which per
   §5.3 means pack warnings still work but shutdown never arms. Adding a board profile is a
   config entry, not code.
