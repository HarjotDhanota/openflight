# Battery Status Implementation Plan

> **For agentic workers:** Implement task-by-task, in order. Each task is
> self-contained and ends with its own commit; do not start a task before its
> predecessor's tests pass. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> Follow strict TDD as written: the failing test comes first, you run it and
> watch it fail, then you write the minimum code to pass. The tests encode
> decisions that are not obvious from the code — several of them exist because
> a design audit caught a safety bug, and weakening a test to make an
> implementation pass will silently reintroduce it.
>
> Design rationale lives in
> `docs/superpowers/specs/2026-08-07-battery-status-design.md`. Read it before
> Task 4 (floating-pin trust), Task 6 (why thermal throttle bits are excluded)
> and Task 8 (the five shutdown interlocks).

**Goal:** Show remaining battery percentage and 5V-rail supply health in the UI, with an
opt-in automatic shutdown before the cells are deep-discharged.

**Architecture:** Three independent readers (MAX1704x fuel gauge over I²C, a GPIO
power-loss line, and the Pi 5 PMIC) feed a threaded sampler. A pure reducer turns each
snapshot plus retained state into health levels and shutdown decisions. The Flask server
emits a serialized view over the existing socket; React renders it. Every reader can be
absent independently.

**Tech Stack:** Python 3.11+, `smbus2`, `gpiozero`/`lgpio`, Flask-SocketIO, React + zustand,
pytest.

## Global Constraints

- **Always use `uv`** — `uv run pytest`, `uv run pylint`, `uv run ruff`. Never bare `python`/`pip`.
- **Lint gate:** `uv run pylint src/openflight/ --fail-under=9` must pass.
- **Format gate:** `uv run ruff check src/openflight/` and `uv run ruff format --check src/openflight/`.
- **No new dependencies.** `smbus2`, `gpiozero`, `lgpio` are already in `pyproject.toml`.
- **Zero change for builders without the hardware.** No GPIO is configured, and nothing is
  rendered, unless configuration declares it.
- **No test may power off the machine.** `shutdown.py` is stubbed in every test.
- Base branch is `feat/battery-status` off `upstream/main` (98466df).
- Spec: `docs/superpowers/specs/2026-08-07-battery-status-design.md`.

### Spec clarification resolved here

Spec §5.1 gives two ways a `hi` reading becomes `external`: a board profile declares the
line, or a `lo` has been observed. Read alongside §9.3 — where no GPIO is configured unless
declared — rule 2 looked redundant.

**Resolution:** the two declaration routes carry different trust.

| Config | `pld_trusted` | `hi` means |
|---|---|---|
| `board: "x1209"` (known profile) | `True` | `external` immediately |
| `pld_gpio: 17` alone (no known board) | `False` | `unknown` until a `lo` is seen |

A known profile is a claim we ship and can verify. A hand-set pin number is a claim we
cannot, so it must prove itself by going low once.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/openflight/power/models.py` | Frozen dataclasses; no logic |
| `src/openflight/power/config.py` | Load/validate `power.json`; per-key fallback |
| `src/openflight/power/max1704x.py` | I²C fuel gauge driver |
| `src/openflight/power/source.py` | `PowerSourceReader` Protocol + GPIO PLD reader |
| `src/openflight/power/pmic.py` | Pi 5 rail reader via `vcgencmd` |
| `src/openflight/power/policy.py` | `(PolicyState, PowerSnapshot) -> (PolicyState, Decision)` |
| `src/openflight/power/shutdown.py` | The only module that halts the machine |
| `src/openflight/power/service.py` | Threaded sampler; owns readers and policy state |
| `src/openflight/server.py` | `init_power`, socket events, config dict |
| `ui/src/stores/usePowerStore.ts` | Client state |
| `ui/src/components/BatteryStatus.tsx` | Indicator + detail + countdown |

---

## Task 1: Data model

**Files:**
- Create: `src/openflight/power/__init__.py`, `src/openflight/power/models.py`
- Test: `tests/test_power_models.py`

**Interfaces:**
- Consumes: nothing
- Produces: `ReaderStatus`, `PackReading`, `RailReading`, `SourceReading`, `PowerSnapshot`,
  `PendingShutdown`, `PowerView`, and `PowerView.to_dict() -> dict`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_power_models.py
from openflight.power.models import (
    PackReading, PowerSnapshot, PowerView, RailReading, SourceReading,
)


def _absent(cls, **kw):
    return cls(status="absent", timestamp=0.0, **kw)


def test_snapshot_holds_three_independent_readers():
    snapshot = PowerSnapshot(
        timestamp=1.0,
        pack=PackReading(status="ok", timestamp=1.0, volts=3.85, percent=62.0),
        rail=_absent(RailReading, ext5v_volts=None, throttled=None),
        source=_absent(SourceReading, state="unknown"),
    )
    assert snapshot.pack.status == "ok"
    assert snapshot.rail.status == "absent"
    assert snapshot.source.state == "unknown"


def test_readings_are_frozen():
    import dataclasses
    import pytest

    reading = PackReading(status="ok", timestamp=1.0, volts=3.85, percent=62.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        reading.volts = 4.0


def test_view_serializes_to_plain_json_types():
    view = PowerView(
        pack_volts=3.85, pack_percent=62.0, pack_level="ok",
        rail_volts=5.21, rail_level="green",
        source="battery", runtime_minutes=None,
        shutdown_eligible=False, pending_shutdown=None, warnings=[],
    )
    payload = view.to_dict()
    assert payload["pack_percent"] == 62.0
    assert payload["pending_shutdown"] is None
    assert payload["warnings"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_power_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'openflight.power'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/openflight/power/__init__.py
"""Battery level and supply-health monitoring."""
```

```python
# src/openflight/power/models.py
"""Value types for power monitoring.

Every reader carries its own status. There is deliberately no global status
field: pack gauge, power source, and rail monitor are independently absent on
real builds, and one field cannot express "gauge working, PLD missing, rail
fine" -- see the support matrix in the design doc, section 3.1.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

ReaderStatus = Literal["ok", "absent", "error"]
PackLevel = Literal["ok", "low", "critical", "unknown"]
RailLevel = Literal["green", "amber", "red", "unknown"]
SourceState = Literal["external", "battery", "unknown"]


@dataclass(frozen=True)
class PackReading:
    """One fuel-gauge sample."""

    status: ReaderStatus
    timestamp: float
    volts: float | None = None
    percent: float | None = None
    error: str | None = None


@dataclass(frozen=True)
class RailReading:
    """One 5V-rail sample. ``throttled`` is the raw get_throttled mask."""

    status: ReaderStatus
    timestamp: float
    ext5v_volts: float | None = None
    throttled: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class SourceReading:
    """Whether the machine is on external power."""

    status: ReaderStatus
    timestamp: float
    state: SourceState = "unknown"
    error: str | None = None


@dataclass(frozen=True)
class PowerSnapshot:
    """One pass over all three readers."""

    timestamp: float
    pack: PackReading
    rail: RailReading
    source: SourceReading


@dataclass(frozen=True)
class PendingShutdown:
    """An armed shutdown awaiting its deadline or a cancel.

    The deadline is absolute monotonic rather than a countdown so a client
    reconnecting mid-window computes the remaining time correctly instead of
    restarting the clock.
    """

    id: str
    deadline_monotonic: float
    reason: str


@dataclass(frozen=True)
class PowerView:
    """What the UI receives. Conclusions included so the client never re-derives."""

    pack_volts: float | None
    pack_percent: float | None
    pack_level: PackLevel
    rail_volts: float | None
    rail_level: RailLevel
    source: SourceState
    runtime_minutes: int | None
    shutdown_eligible: bool
    pending_shutdown: PendingShutdown | None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize for socket emit."""
        return asdict(self)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_power_models.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/openflight/power/__init__.py src/openflight/power/models.py tests/test_power_models.py
git commit -m "feat(power): value types with per-reader status"
```

---

## Task 2: Configuration with per-key validation

**Files:**
- Create: `src/openflight/power/config.py`
- Test: `tests/test_power_config.py`

**Interfaces:**
- Consumes: nothing
- Produces: `PowerConfig` dataclass, `load_config(path) -> PowerConfig`,
  `BOARD_PROFILES: dict[str, dict]`, `CONFIG_PATH`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_power_config.py
import json

from openflight.power.config import PowerConfig, load_config


def _write(tmp_path, payload):
    path = tmp_path / "power.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_missing_file_returns_defaults(tmp_path):
    config = load_config(tmp_path / "nope.json")
    assert config == PowerConfig()
    assert config.pld_gpio is None
    assert config.auto_shutdown_enabled is False


def test_corrupt_file_returns_defaults(tmp_path):
    path = tmp_path / "power.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_config(path) == PowerConfig()


def test_valid_json_of_wrong_shape_returns_defaults(tmp_path):
    assert load_config(_write(tmp_path, [1, 2, 3])) == PowerConfig()


def test_one_bad_key_falls_back_alone(tmp_path):
    config = load_config(_write(tmp_path, {
        "sample_interval_s": "banana",
        "dwell_samples": 4,
    }))
    assert config.sample_interval_s == 2.0     # default
    assert config.dwell_samples == 4           # kept


def test_hex_string_i2c_address_parses(tmp_path):
    assert load_config(_write(tmp_path, {"i2c_address": "0x36"})).i2c_address == 0x36


def test_threshold_ordering_is_enforced(tmp_path):
    # critical above low is incoherent; both revert together
    config = load_config(_write(tmp_path, {
        "pack_low_volts": 3.4, "pack_critical_volts": 3.6,
    }))
    assert config.pack_low_volts == 3.6
    assert config.pack_critical_volts == 3.4


def test_non_finite_rejected(tmp_path):
    config = load_config(_write(tmp_path, {"deadband_volts": float("inf")}))
    assert config.deadband_volts == 0.05


def test_known_board_sets_pld_and_trust(tmp_path):
    config = load_config(_write(tmp_path, {"board": "x1209"}))
    assert config.pld_gpio == 6
    assert config.pld_trusted is True


def test_bare_pld_gpio_is_untrusted(tmp_path):
    config = load_config(_write(tmp_path, {"pld_gpio": 17}))
    assert config.pld_gpio == 17
    assert config.pld_trusted is False


def test_unknown_board_ignored(tmp_path):
    config = load_config(_write(tmp_path, {"board": "nonesuch"}))
    assert config.board is None
    assert config.pld_gpio is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_power_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'openflight.power.config'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/openflight/power/config.py
"""Persistent power-monitoring configuration.

Stored at ``~/.config/openflight/power.json``, mirroring the other config
modules. A corrupt or partly-invalid config must never stop the launch monitor
from starting, so every key validates independently and falls back to its own
default. Rejecting the whole file would turn one typo into a machine that will
not boot at the range.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, replace
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".config" / "openflight" / "power.json"

# Boards whose power-loss wiring we ship and have verified. Declaring one is
# what makes a "high" reading trustworthy -- see the trust table in the plan
# header and design doc section 5.1.
BOARD_PROFILES: dict[str, dict] = {
    "x1209": {"pld_gpio": 6, "i2c_address": 0x36},
}


@dataclass(frozen=True)
class PowerConfig:
    """Power settings persisted between sessions."""

    board: str | None = None
    enabled: bool = True
    sample_interval_s: float = 2.0
    rail_amber_volts: float = 5.0
    rail_red_volts: float = 4.9
    pack_low_volts: float = 3.6
    pack_critical_volts: float = 3.4
    shutdown_volts: float = 3.2
    auto_shutdown_enabled: bool = False
    shutdown_grace_seconds: int = 60
    dwell_samples: int = 15
    deadband_volts: float = 0.05
    pld_gpio: int | None = None
    # Not user-settable. True only when `board` names a known profile: a
    # profile is a claim we ship, a hand-set pin number is one we cannot check.
    pld_trusted: bool = False
    i2c_bus: int = 1
    i2c_address: int = 0x36


def _number(data: dict, key: str, default: float, low: float, high: float) -> float:
    raw = data.get(key, default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("[POWER] %s=%r is not a number; using %s", key, raw, default)
        return default
    if not math.isfinite(value) or not low <= value <= high:
        logger.warning("[POWER] %s=%r out of range; using %s", key, raw, default)
        return default
    return value


def _integer(data: dict, key: str, default: int, low: int, high: int) -> int:
    raw = data.get(key, default)
    if isinstance(raw, bool):  # bool is an int subclass; almost never intended
        logger.warning("[POWER] %s=%r is not an integer; using %s", key, raw, default)
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("[POWER] %s=%r is not an integer; using %s", key, raw, default)
        return default
    if not low <= value <= high:
        logger.warning("[POWER] %s=%r out of range; using %s", key, raw, default)
        return default
    return value


def _address(data: dict, key: str, default: int) -> int:
    """Parse an I2C address. JSON has no hex literals, so "0x36" is accepted."""
    raw = data.get(key, default)
    if isinstance(raw, str):
        try:
            raw = int(raw, 16 if raw.lower().startswith("0x") else 10)
        except ValueError:
            logger.warning("[POWER] %s=%r unparseable; using 0x%02x", key, raw, default)
            return default
    return _integer({key: raw}, key, default, 0x08, 0x77)


def _boolean(data: dict, key: str, default: bool) -> bool:
    raw = data.get(key, default)
    if not isinstance(raw, bool):
        logger.warning("[POWER] %s=%r is not a boolean; using %s", key, raw, default)
        return default
    return raw


def load_config(path: Path = CONFIG_PATH) -> PowerConfig:
    """Load config from ``path``, falling back per key on anything invalid."""
    path = Path(path)
    if not path.exists():
        return PowerConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        logger.warning("[POWER] %s unreadable; using defaults", path)
        return PowerConfig()
    # Valid JSON of the wrong shape still cannot be indexed.
    if not isinstance(data, dict):
        logger.warning("[POWER] %s is not a JSON object; using defaults", path)
        return PowerConfig()

    for key in data:
        if key not in PowerConfig.__dataclass_fields__ or key == "pld_trusted":
            logger.warning("[POWER] ignoring unknown config key %r", key)

    defaults = PowerConfig()
    config = PowerConfig(
        enabled=_boolean(data, "enabled", defaults.enabled),
        sample_interval_s=_number(data, "sample_interval_s", defaults.sample_interval_s, 0.5, 60.0),
        rail_amber_volts=_number(data, "rail_amber_volts", defaults.rail_amber_volts, 0.0, 6.0),
        rail_red_volts=_number(data, "rail_red_volts", defaults.rail_red_volts, 0.0, 6.0),
        pack_low_volts=_number(data, "pack_low_volts", defaults.pack_low_volts, 2.5, 4.3),
        pack_critical_volts=_number(
            data, "pack_critical_volts", defaults.pack_critical_volts, 2.5, 4.3
        ),
        shutdown_volts=_number(data, "shutdown_volts", defaults.shutdown_volts, 2.5, 4.3),
        auto_shutdown_enabled=_boolean(
            data, "auto_shutdown_enabled", defaults.auto_shutdown_enabled
        ),
        shutdown_grace_seconds=_integer(
            data, "shutdown_grace_seconds", defaults.shutdown_grace_seconds, 10, 600
        ),
        dwell_samples=_integer(data, "dwell_samples", defaults.dwell_samples, 1, 600),
        deadband_volts=_number(data, "deadband_volts", defaults.deadband_volts, 0.0, 1.0),
        i2c_bus=_integer(data, "i2c_bus", defaults.i2c_bus, 0, 20),
        i2c_address=_address(data, "i2c_address", defaults.i2c_address),
    )

    # Ordering constraints. A pair that disagrees is incoherent rather than
    # merely out of range, so both revert together instead of silently
    # producing a band where no level applies.
    if config.rail_red_volts >= config.rail_amber_volts:
        logger.warning("[POWER] rail_red must be below rail_amber; using defaults for both")
        config = replace(
            config,
            rail_amber_volts=defaults.rail_amber_volts,
            rail_red_volts=defaults.rail_red_volts,
        )
    if config.pack_critical_volts >= config.pack_low_volts:
        logger.warning("[POWER] pack_critical must be below pack_low; using defaults for both")
        config = replace(
            config,
            pack_low_volts=defaults.pack_low_volts,
            pack_critical_volts=defaults.pack_critical_volts,
        )
    if config.shutdown_volts > config.pack_critical_volts:
        logger.warning("[POWER] shutdown_volts above pack_critical; using default")
        config = replace(config, shutdown_volts=defaults.shutdown_volts)

    # Board profile last: it supplies a trusted pin, and an explicit pld_gpio
    # without a known board stays untrusted until it proves itself (see the
    # trust table in the plan header).
    board = data.get("board")
    if isinstance(board, str) and board in BOARD_PROFILES:
        profile = BOARD_PROFILES[board]
        config = replace(
            config,
            board=board,
            pld_gpio=profile["pld_gpio"],
            pld_trusted=True,
        )
    else:
        if board is not None:
            logger.warning("[POWER] unknown board %r; ignoring", board)
        pin = data.get("pld_gpio")
        if pin is not None:
            config = replace(
                config,
                pld_gpio=_integer(data, "pld_gpio", -1, 0, 27) or None,
                pld_trusted=False,
            )
            if config.pld_gpio == -1:
                config = replace(config, pld_gpio=None)
    return config
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_power_config.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/openflight/power/config.py tests/test_power_config.py
git commit -m "feat(power): config with per-key validation and board profiles"
```

---

## Task 3: MAX1704x fuel gauge driver

**Files:**
- Create: `src/openflight/power/max1704x.py`, `src/openflight/power/gauge.py`
- Test: `tests/test_power_max1704x.py`

**Interfaces:**
- Consumes: `PackReading` (Task 1)
- Produces: `BatteryGauge` Protocol (`initialize()`, `read() -> PackReading`, `close()`),
  `MAX1704X` class, `swap16(raw: int) -> int`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_power_max1704x.py
import pytest

from openflight.power.max1704x import MAX1704X, swap16


class FakeBus:
    def __init__(self, words=None, raises=None):
        self.words = words or {}
        self.raises = raises
        self.closed = False

    def read_word_data(self, address, register):
        if self.raises:
            raise self.raises
        return self.words[(address, register)]

    def close(self):
        self.closed = True


def test_swap16_matches_bench_reading():
    # Verified on hardware: i2cget -y 1 0x36 0x02 w -> 0x60cc
    assert swap16(0x60CC) == 0xCC60 == 52320


def test_voltage_conversion_matches_bench_reading():
    bus = FakeBus({(0x36, 0x02): 0x60CC, (0x36, 0x04): 0x003E})
    gauge = MAX1704X(bus=bus)
    gauge.initialize()
    reading = gauge.read(timestamp=1.0)
    assert reading.status == "ok"
    assert reading.volts == pytest.approx(4.0875, abs=1e-4)


def test_percent_conversion():
    # 0x3E00 byte-swapped from 0x003E -> 15872 / 256 = 62.0
    bus = FakeBus({(0x36, 0x02): 0x60CC, (0x36, 0x04): 0x003E})
    gauge = MAX1704X(bus=bus)
    gauge.initialize()
    assert gauge.read(timestamp=1.0).percent == pytest.approx(62.0)


def test_percent_is_clamped_to_100():
    # ModelGauge can report slightly over 100 immediately after a full charge.
    bus = FakeBus({(0x36, 0x02): 0x60CC, (0x36, 0x04): 0x0069})
    gauge = MAX1704X(bus=bus)
    gauge.initialize()
    assert gauge.read(timestamp=1.0).percent == 100.0


def test_initialize_raises_when_the_gauge_does_not_ack():
    # The caller uses this to decide "no gauge fitted" and carry on with the
    # other readers, so it must propagate rather than return a status.
    gauge = MAX1704X(bus=FakeBus(raises=OSError("no ACK")))
    with pytest.raises(OSError):
        gauge.initialize()


def test_bus_error_after_init_becomes_error_status_not_exception():
    # Initialize against a healthy bus, then break it. A gauge that answered
    # once and later glitches must degrade to a status: an exception here
    # would kill the sampling thread and take the indicator down for good.
    bus = FakeBus({(0x36, 0x02): 0x60CC, (0x36, 0x04): 0x003E})
    gauge = MAX1704X(bus=bus)
    gauge.initialize()
    bus.raises = OSError("no ACK")
    reading = gauge.read(timestamp=1.0)
    assert reading.status == "error"
    assert reading.volts is None
    assert "no ACK" in reading.error


def test_close_is_idempotent():
    bus = FakeBus({(0x36, 0x02): 0x60CC, (0x36, 0x04): 0x003E})
    gauge = MAX1704X(bus=bus)
    gauge.close()
    gauge.close()
    assert bus.closed is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_power_max1704x.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'openflight.power.max1704x'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/openflight/power/gauge.py
"""Contract a fuel gauge must satisfy.

Deliberately narrow, and deliberately free of any power-source concept: which
boards can tell you whether mains is connected is unrelated to which boards
have a fuel gauge, and folding the two together would couple capabilities that
go missing independently. See source.py.
"""

from __future__ import annotations

from typing import Protocol

from .models import PackReading


class BatteryGauge(Protocol):  # pylint: disable=unnecessary-ellipsis
    """Sensor contract required by the power service."""

    def initialize(self) -> None:
        """Configure and verify the gauge."""
        ...  # pylint: disable=unnecessary-ellipsis

    def read(self, *, timestamp: float) -> PackReading:
        """Read pack voltage and state of charge. Never raises."""
        ...  # pylint: disable=unnecessary-ellipsis

    def close(self) -> None:
        """Release the bus. Idempotent."""
        ...  # pylint: disable=unnecessary-ellipsis
```

```python
# src/openflight/power/max1704x.py
"""MAX1704x fuel-gauge driver (Geekworm X120x/X12xx family, UPS-Lite).

Register 0x04 is ModelGauge state of charge -- a modeled value that tracks
across charge and discharge, not a lookup on instantaneous VCELL. It stays
meaningful while the pack is charging, which is why the UI keeps showing a
percentage on external power.
"""

from __future__ import annotations

from typing import Protocol

from .models import PackReading

VCELL_REGISTER = 0x02
SOC_REGISTER = 0x04
# MAX17048 datasheet: VCELL LSB is 78.125 uV.
VCELL_MICROVOLTS_PER_LSB = 78.125


class SMBusLike(Protocol):  # pylint: disable=unnecessary-ellipsis
    """Subset of smbus2 used by the driver, allowing deterministic tests."""

    def read_word_data(self, address: int, register: int) -> int:
        """Read one register word."""
        ...  # pylint: disable=unnecessary-ellipsis

    def close(self) -> None:
        """Close the bus."""
        ...  # pylint: disable=unnecessary-ellipsis


def swap16(raw: int) -> int:
    """Byte-swap a 16-bit word.

    SMBus reads little-endian and the gauge is big-endian, so every word needs
    this. Verified on hardware: 0x60cc -> 0xcc60 -> 52320 -> 4.088 V.
    """
    return ((raw & 0xFF) << 8) | ((raw >> 8) & 0xFF)


class MAX1704X:
    """Read pack voltage and modeled state of charge over I2C."""

    DEFAULT_ADDRESS = 0x36

    def __init__(
        self,
        *,
        bus_number: int = 1,
        address: int = DEFAULT_ADDRESS,
        bus: SMBusLike | None = None,
    ):
        if bus is None:
            from smbus2 import SMBus  # pylint: disable=import-outside-toplevel,import-error

            bus = SMBus(bus_number)
        self.bus = bus
        self.address = address
        self._closed = False

    def initialize(self) -> None:
        """Verify the gauge answers.

        Raises:
            OSError: if the address does not ACK. The caller treats this as
                "no gauge fitted" and carries on with the other readers.
        """
        self.bus.read_word_data(self.address, VCELL_REGISTER)

    def read(self, *, timestamp: float) -> PackReading:
        """Read the gauge. Never raises: bus faults become an error status."""
        try:
            volts = swap16(self.bus.read_word_data(self.address, VCELL_REGISTER))
            soc = swap16(self.bus.read_word_data(self.address, SOC_REGISTER))
        except Exception as error:  # pylint: disable=broad-exception-caught
            # A sampling thread that dies on a transient bus glitch takes the
            # indicator down permanently; a status does not.
            return PackReading(status="error", timestamp=timestamp, error=str(error))
        return PackReading(
            status="ok",
            timestamp=timestamp,
            volts=volts * VCELL_MICROVOLTS_PER_LSB / 1_000_000,
            # ModelGauge reports slightly over 100% just after a full charge.
            percent=min(100.0, soc / 256),
        )

    def close(self) -> None:
        """Release the bus. Idempotent."""
        if self._closed:
            return
        self._closed = True
        self.bus.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_power_max1704x.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/openflight/power/gauge.py src/openflight/power/max1704x.py tests/test_power_max1704x.py
git commit -m "feat(power): MAX1704x gauge driver with verified byte-swap"
```

---

## Task 4: Power-source reader and the floating-pin trust rule

**Files:**
- Create: `src/openflight/power/source.py`
- Test: `tests/test_power_source.py`

**Interfaces:**
- Consumes: `SourceReading` (Task 1)
- Produces: `PowerSourceReader` Protocol, `GpioPldSource(pin, trusted, level_reader)`,
  `NullSource()`

**Why this task is safety-relevant:** a pulled-up pin with nothing wired to it reads high.
Mapping high straight to "external power" would report battery operation as mains, suppress
warnings, and disable shutdown on exactly the boards least able to detect a power problem.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_power_source.py
from openflight.power.source import GpioPldSource, NullSource


class FakePin:
    """Stand-in for a gpiozero input. True is high."""

    def __init__(self, level=True):
        self.level = level
        self.closed = False

    def read(self):
        return self.level

    def close(self):
        self.closed = True


def test_low_is_always_battery():
    # A pulled-up floating pin cannot read low, so low is unambiguous.
    source = GpioPldSource(pin=6, trusted=False, pin_reader=FakePin(level=False))
    assert source.read(timestamp=1.0).state == "battery"


def test_untrusted_high_is_unknown_not_external():
    source = GpioPldSource(pin=17, trusted=False, pin_reader=FakePin(level=True))
    reading = source.read(timestamp=1.0)
    assert reading.state == "unknown"
    assert reading.status == "ok"


def test_trusted_high_is_external():
    source = GpioPldSource(pin=6, trusted=True, pin_reader=FakePin(level=True))
    assert source.read(timestamp=1.0).state == "external"


def test_untrusted_pin_latches_after_proving_itself():
    pin = FakePin(level=False)
    source = GpioPldSource(pin=17, trusted=False, pin_reader=pin)
    assert source.read(timestamp=1.0).state == "battery"
    pin.level = True
    # Having gone low once, the line is proven driven; high now means external.
    assert source.read(timestamp=2.0).state == "external"


def test_null_source_is_always_unknown_and_absent():
    reading = NullSource().read(timestamp=1.0)
    assert reading.state == "unknown"
    assert reading.status == "absent"


def test_read_error_becomes_error_status():
    class Boom:
        def read(self):
            raise OSError("gpio gone")

        def close(self):
            pass

    reading = GpioPldSource(pin=6, trusted=True, pin_reader=Boom()).read(timestamp=1.0)
    assert reading.status == "error"
    assert reading.state == "unknown"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_power_source.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'openflight.power.source'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/openflight/power/source.py
"""Whether the machine is running on external power.

Separate from the fuel gauge because the two capabilities go missing
independently: plenty of boards have a gauge and no power-loss line.

The central subtlety is that a pulled-up input with nothing wired to it reads
HIGH. On a board with no PLD line, "high" is indistinguishable from "mains
connected" -- so a naive mapping would report battery operation as external,
suppress low-battery warnings, and disable automatic shutdown on precisely the
builds least able to notice. High is therefore only believed when the line has
been declared by a board profile we ship, or has proven itself by reading low
at least once. Low is always trustworthy: a pulled-up floating pin cannot
produce it.
"""

from __future__ import annotations

import logging
from typing import Protocol

from .models import SourceReading

logger = logging.getLogger(__name__)


class PinReader(Protocol):  # pylint: disable=unnecessary-ellipsis
    """Minimal input-pin surface, so tests need no GPIO hardware."""

    def read(self) -> bool:
        """True when the pin is high."""
        ...  # pylint: disable=unnecessary-ellipsis

    def close(self) -> None:
        """Release the line."""
        ...  # pylint: disable=unnecessary-ellipsis


class PowerSourceReader(Protocol):  # pylint: disable=unnecessary-ellipsis
    """Contract the power service requires."""

    def read(self, *, timestamp: float) -> SourceReading:
        """Read the source state. Never raises."""
        ...  # pylint: disable=unnecessary-ellipsis

    def close(self) -> None:
        """Release resources. Idempotent."""
        ...  # pylint: disable=unnecessary-ellipsis


class NullSource:
    """Used when no PLD line is configured. Always absent, always unknown."""

    def read(self, *, timestamp: float) -> SourceReading:
        """Report that nothing is known about the power source."""
        return SourceReading(status="absent", timestamp=timestamp, state="unknown")

    def close(self) -> None:
        """No resources to release."""


class GpioPldSource:
    """Read a power-loss-detect line. Active low: low means running on battery."""

    def __init__(self, *, pin: int, trusted: bool, pin_reader: PinReader):
        self.pin = pin
        self._trusted = trusted
        self._pin_reader = pin_reader
        self._closed = False

    def read(self, *, timestamp: float) -> SourceReading:
        """Read the line, applying the trust rule above."""
        try:
            high = self._pin_reader.read()
        except Exception as error:  # pylint: disable=broad-exception-caught
            return SourceReading(
                status="error", timestamp=timestamp, state="unknown", error=str(error)
            )

        if not high:
            if not self._trusted:
                logger.info(
                    "[POWER] GPIO %d read low; treating the PLD line as wired from now on",
                    self.pin,
                )
            # Latch: a line that has gone low is driven, so its highs mean
            # something from here on.
            self._trusted = True
            return SourceReading(status="ok", timestamp=timestamp, state="battery")

        state = "external" if self._trusted else "unknown"
        return SourceReading(status="ok", timestamp=timestamp, state=state)

    def close(self) -> None:
        """Release the line. Idempotent."""
        if self._closed:
            return
        self._closed = True
        self._pin_reader.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_power_source.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/openflight/power/source.py tests/test_power_source.py
git commit -m "feat(power): PLD source reader that will not mistake a floating pin for mains"
```

---

## Task 5: Pi 5 PMIC rail reader

**Files:**
- Create: `src/openflight/power/pmic.py`
- Test: `tests/test_power_pmic.py`

**Interfaces:**
- Consumes: `RailReading` (Task 1)
- Produces: `PmicRail(runner=None, timeout_s=2.0)` with `.read(timestamp)`, `.close()`;
  `parse_ext5v(text) -> float | None`; `parse_throttled(text) -> int | None`;
  `UNDERVOLTAGE_MASK = 0x10001`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_power_pmic.py
import subprocess

import pytest

from openflight.power.pmic import (
    UNDERVOLTAGE_MASK, PmicRail, parse_ext5v, parse_throttled,
)

ADC_OUTPUT = """\
 3V7_WL_SW_A current(0)=0.09856894A
   VDD_CORE_V volt(15)=0.75052430V
      EXT5V_V volt(24)=5.21528000V
"""


def test_parse_ext5v_from_real_output():
    assert parse_ext5v(ADC_OUTPUT) == pytest.approx(5.21528)


def test_parse_ext5v_missing_field_returns_none():
    assert parse_ext5v("3V3_SYS_V volt(9)=3.31838500V\n") is None


def test_parse_throttled_hex():
    assert parse_throttled("throttled=0x50005") == 0x50005


def test_parse_throttled_garbage_returns_none():
    assert parse_throttled("not a thing") is None


def test_undervoltage_mask_excludes_thermal_bits():
    # get_throttled layout:
    #   bit 0  0x00001  undervoltage now          <- supply
    #   bit 1  0x00002  ARM frequency capped
    #   bit 2  0x00004  currently throttled
    #   bit 3  0x00008  soft temperature limit
    #   bit 16 0x10000  undervoltage has occurred <- supply
    #   bit 17 0x20000  frequency capping has occurred
    #   bit 18 0x40000  throttling has occurred
    #   bit 19 0x80000  soft temp limit has occurred
    assert UNDERVOLTAGE_MASK == 0x10001
    # 0x60006 = bits 1, 2, 17, 18. Everything thermal and frequency, nothing
    # supply. Note 0x5xxxx would include bit 16 and is NOT thermal-only.
    assert 0x60006 & UNDERVOLTAGE_MASK == 0
    assert 0x10000 & UNDERVOLTAGE_MASK != 0      # sticky undervoltage -> caught
    assert 0x00001 & UNDERVOLTAGE_MASK != 0      # live undervoltage -> caught


def test_read_returns_ok_with_parsed_values():
    def runner(args, timeout):
        return ADC_OUTPUT if "pmic_read_adc" in args else "throttled=0x0"

    reading = PmicRail(runner=runner).read(timestamp=1.0)
    assert reading.status == "ok"
    assert reading.ext5v_volts == pytest.approx(5.21528)
    assert reading.throttled == 0


def test_timeout_becomes_error_status_not_a_hang():
    def runner(args, timeout):
        raise subprocess.TimeoutExpired(cmd=args, timeout=timeout)

    reading = PmicRail(runner=runner).read(timestamp=1.0)
    assert reading.status == "error"
    assert reading.ext5v_volts is None


def test_missing_vcgencmd_is_absent_not_error():
    def runner(args, timeout):
        raise FileNotFoundError("vcgencmd")

    # A Pi 4 has no PMIC ADC. That is a build without the capability, not a fault.
    assert PmicRail(runner=runner).read(timestamp=1.0).status == "absent"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_power_pmic.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'openflight.power.pmic'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/openflight/power/pmic.py
"""5V-rail health from the Raspberry Pi 5 PMIC.

Two facts shape this module.

First, ``get_throttled`` reports far more than supply problems: bits 1-3 and
17-19 are ARM frequency capping and thermal limiting. This launch monitor lives
in a sealed IP54 enclosure on a summer range, where thermal throttling is
expected behaviour -- treating it as a supply fault would show a red rail on a
perfectly healthy machine. Only bits 0 and 16 are undervoltage.

Second, ``vcgencmd`` forks a process, so it gets a hard timeout. A hang must
degrade this one reader, never stall the sampling loop.
"""

from __future__ import annotations

import subprocess
from typing import Callable

from .models import RailReading

# Bit 0: undervoltage now. Bit 16: undervoltage has occurred since boot.
UNDERVOLTAGE_MASK = 0x10001

_EXT5V_FIELD = "EXT5V_V"


def _default_runner(args: list[str], timeout: float) -> str:
    return subprocess.run(  # pylint: disable=subprocess-run-check
        args, capture_output=True, text=True, timeout=timeout
    ).stdout


def parse_ext5v(text: str) -> float | None:
    """Extract EXT5V_V volts from ``vcgencmd pmic_read_adc`` output.

    The field looks like ``EXT5V_V volt(24)=5.21528000V`` -- splitting on '='
    rather than whitespace, because the value is glued to its label.
    """
    for line in text.splitlines():
        if _EXT5V_FIELD not in line or "=" not in line:
            continue
        try:
            return float(line.rsplit("=", 1)[1].strip().rstrip("V"))
        except ValueError:
            return None
    return None


def parse_throttled(text: str) -> int | None:
    """Extract the mask from ``vcgencmd get_throttled`` output."""
    if "=" not in text:
        return None
    try:
        return int(text.split("=", 1)[1].strip(), 16)
    except ValueError:
        return None


class PmicRail:
    """Read 5V-rail voltage and undervoltage flags. Pi 5 only."""

    def __init__(
        self,
        *,
        runner: Callable[[list[str], float], str] | None = None,
        timeout_s: float = 2.0,
    ):
        self._runner = runner or _default_runner
        self._timeout_s = timeout_s

    def read(self, *, timestamp: float) -> RailReading:
        """Read the rail. Never raises."""
        try:
            adc = self._runner(["vcgencmd", "pmic_read_adc"], self._timeout_s)
            throttled_text = self._runner(["vcgencmd", "get_throttled"], self._timeout_s)
        except FileNotFoundError:
            # No vcgencmd at all: not a Pi, or a Pi without it. The capability
            # is missing rather than broken.
            return RailReading(status="absent", timestamp=timestamp)
        except Exception as error:  # pylint: disable=broad-exception-caught
            return RailReading(status="error", timestamp=timestamp, error=str(error))

        volts = parse_ext5v(adc)
        throttled = parse_throttled(throttled_text)
        if volts is None and throttled is None:
            # vcgencmd ran but said nothing we understand -- e.g. a Pi 4, whose
            # pmic_read_adc has no EXT5V_V field.
            return RailReading(status="absent", timestamp=timestamp)
        return RailReading(
            status="ok", timestamp=timestamp, ext5v_volts=volts, throttled=throttled
        )

    def close(self) -> None:
        """No resources to release."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_power_pmic.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/openflight/power/pmic.py tests/test_power_pmic.py
git commit -m "feat(power): Pi 5 rail reader masking thermal bits out of supply health"
```

---

## Task 6: Policy — health levels

**Files:**
- Create: `src/openflight/power/policy.py`
- Test: `tests/test_power_policy_levels.py`

**Interfaces:**
- Consumes: `PowerSnapshot`, `PackLevel`, `RailLevel` (Task 1); `PowerConfig` (Task 2);
  `UNDERVOLTAGE_MASK` (Task 5)
- Produces: `rail_level(reading, config) -> RailLevel`,
  `pack_level(reading, source_state, config) -> PackLevel`,
  `shutdown_eligible(reading, config) -> bool`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_power_policy_levels.py
import pytest

from openflight.power.config import PowerConfig
from openflight.power.models import PackReading, RailReading
from openflight.power.policy import pack_level, rail_level, shutdown_eligible

CONFIG = PowerConfig()


def rail(volts, throttled=0):
    return RailReading(status="ok", timestamp=1.0, ext5v_volts=volts, throttled=throttled)


def pack(volts):
    return PackReading(status="ok", timestamp=1.0, volts=volts, percent=50.0)


@pytest.mark.parametrize("volts,expected", [
    (5.211, "green"),   # measured idle baseline
    (5.000, "green"),   # exactly at amber threshold is still green
    (4.999, "amber"),
    (4.900, "amber"),   # exactly at red threshold is still amber
    (4.899, "red"),
])
def test_rail_level_boundaries(volts, expected):
    assert rail_level(rail(volts), CONFIG) == expected


def test_sticky_undervoltage_forces_amber_even_at_good_voltage():
    assert rail_level(rail(5.21, throttled=0x10000), CONFIG) == "amber"


def test_live_undervoltage_forces_red():
    assert rail_level(rail(5.21, throttled=0x1), CONFIG) == "red"


def test_thermal_throttling_does_not_affect_rail_health():
    # 0x60006 = bits 1, 2, 17, 18: frequency capped and throttled, plus their
    # sticky twins. No undervoltage bit, so the rail is healthy -- which is the
    # normal state for a sealed enclosure on a hot range.
    assert rail_level(rail(5.21, throttled=0x60006), CONFIG) == "green"


def test_rail_absent_is_unknown():
    assert rail_level(RailReading(status="absent", timestamp=1.0), CONFIG) == "unknown"


@pytest.mark.parametrize("volts,expected", [
    (4.10, "ok"),
    (3.60, "ok"),        # exactly at low threshold is still ok
    (3.59, "low"),
    (3.40, "low"),       # exactly at critical threshold is still low
    (3.39, "critical"),
    (3.10, "critical"),  # below shutdown volts is still just "critical"
])
def test_pack_level_boundaries_are_non_overlapping(volts, expected):
    assert pack_level(pack(volts), "battery", CONFIG) == expected


def test_pack_on_external_power_is_ok_regardless_of_voltage():
    # Charge current inflates terminal voltage, so voltage levels do not apply.
    assert pack_level(pack(3.10), "external", CONFIG) == "ok"


def test_pack_on_unknown_source_is_evaluated_as_battery():
    # A spurious warning costs a glance; a missed one costs the session.
    assert pack_level(pack(3.39), "unknown", CONFIG) == "critical"


def test_pack_absent_is_unknown():
    assert pack_level(PackReading(status="absent", timestamp=1.0), "battery", CONFIG) == "unknown"


def test_shutdown_eligible_is_separate_from_level():
    # A pack below 3.2 is eligible AND still visibly critical.
    assert shutdown_eligible(pack(3.19), CONFIG) is True
    assert pack_level(pack(3.19), "battery", CONFIG) == "critical"
    assert shutdown_eligible(pack(3.21), CONFIG) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_power_policy_levels.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'openflight.power.policy'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/openflight/power/policy.py
"""Turning readings into health levels and decisions.

Level functions are pure. The reducer added in a later task wraps them with the
retained state that dwell, hysteresis and shutdown latching require.
"""

from __future__ import annotations

from .config import PowerConfig
from .models import PackLevel, PackReading, RailLevel, RailReading, SourceState
from .pmic import UNDERVOLTAGE_MASK


def rail_level(reading: RailReading, config: PowerConfig) -> RailLevel:
    """Classify 5V-rail health.

    Undervoltage flags outrank voltage: the firmware detecting a droop is a
    stronger signal than a spot reading taken between droops.
    """
    if reading.status != "ok":
        return "unknown"

    flags = reading.throttled or 0
    if flags & 0x1:
        return "red"

    volts = reading.ext5v_volts
    if volts is not None:
        if volts < config.rail_red_volts:
            return "red"
        if volts < config.rail_amber_volts:
            return "amber"

    if flags & UNDERVOLTAGE_MASK:
        # Sticky bit only: healthy now, but it has happened this boot.
        return "amber"
    return "green" if volts is not None else "unknown"


def pack_level(
    reading: PackReading, source_state: SourceState, config: PowerConfig
) -> PackLevel:
    """Classify pack health from voltage.

    Bands are non-overlapping and exhaustive below ``pack_low_volts`` -- an
    earlier draft left 3.2-3.3 V with no level, so a nearly-dead pack fell
    through the table.

    On external power the reading is inflated by charge current, so voltage
    bands do not apply and mains is not a low-battery condition. On an unknown
    source it is evaluated as if on battery: a spurious warning costs a glance,
    a missed one costs the session.
    """
    if reading.status != "ok" or reading.volts is None:
        return "unknown"
    if source_state == "external":
        return "ok"
    if reading.volts >= config.pack_low_volts:
        return "ok"
    if reading.volts >= config.pack_critical_volts:
        return "low"
    return "critical"


def shutdown_eligible(reading: PackReading, config: PowerConfig) -> bool:
    """Whether voltage alone would permit a shutdown.

    Separate from the level so a pack below the shutdown threshold stays
    visibly critical whether or not automatic shutdown is enabled. This is one
    of five conditions; see the reducer.
    """
    if reading.status != "ok" or reading.volts is None:
        return False
    return reading.volts <= config.shutdown_volts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_power_policy_levels.py -v`
Expected: 19 passed (5 + 6 from the two parametrized cases, plus 8 others)

- [ ] **Step 5: Commit**

```bash
git add src/openflight/power/policy.py tests/test_power_policy_levels.py
git commit -m "feat(power): non-overlapping health levels with external-power handling"
```

---

## Task 7: Policy — dwell, hysteresis, and runtime history

**Files:**
- Modify: `src/openflight/power/policy.py`
- Test: `tests/test_power_policy_dwell.py`

**Interfaces:**
- Consumes: everything from Task 6
- Produces: `PolicyState` dataclass, `Decision` dataclass,
  `step(state, snapshot, config, now_monotonic) -> tuple[PolicyState, Decision]`,
  `initial_state() -> PolicyState`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_power_policy_dwell.py
from openflight.power.config import PowerConfig
from openflight.power.models import PackReading, PowerSnapshot, RailReading, SourceReading
from openflight.power.policy import initial_state, step

CONFIG = PowerConfig(dwell_samples=3, deadband_volts=0.05)


def snap(volts, t=0.0, source="battery", pack_status="ok"):
    return PowerSnapshot(
        timestamp=t,
        pack=PackReading(status=pack_status, timestamp=t, volts=volts, percent=50.0),
        rail=RailReading(status="ok", timestamp=t, ext5v_volts=5.21, throttled=0),
        source=SourceReading(status="ok", timestamp=t, state=source),
    )


def run(voltages, config=CONFIG, **kw):
    state, decisions = initial_state(), []
    for index, volts in enumerate(voltages):
        state, decision = step(state, snap(volts, t=float(index), **kw), config, float(index))
        decisions.append(decision)
    return decisions


def test_first_reading_establishes_the_level_with_no_dwell():
    # Startup must show the truth straight away, not 30 seconds of "unknown".
    # Every other test in this file has to seed a baseline because of this.
    assert run([3.5])[0].pack_level == "low"
    assert run([3.9])[0].pack_level == "ok"


def test_level_change_requires_dwell():
    levels = [d.pack_level for d in run([3.9, 3.5, 3.5, 3.5])]
    assert levels == ["ok", "ok", "ok", "low"]   # changes only on the 3rd low read


def test_transient_sag_does_not_change_level():
    levels = [d.pack_level for d in run([3.9, 3.5, 3.9, 3.9])]
    assert levels == ["ok", "ok", "ok", "ok"]


def test_flapping_across_a_threshold_does_not_oscillate():
    levels = [d.pack_level for d in run([3.9, 3.59, 3.61, 3.59, 3.61, 3.59])]
    assert set(levels) == {"ok"}


def test_leaving_a_level_requires_the_deadband():
    # Settle into low, then recover. 3.61 is above 3.6 but inside the 0.05
    # deadband, so it must not clear; 3.66 must.
    decisions = run([3.5, 3.5, 3.5, 3.61, 3.61, 3.61, 3.66, 3.66, 3.66])
    levels = [d.pack_level for d in decisions]
    assert levels[2] == "low"
    assert levels[5] == "low"     # inside deadband, still low
    assert levels[8] == "ok"


def test_reader_error_resets_dwell():
    # The first valid reading establishes a level with no dwell, so the
    # indicator is right at startup rather than 30 seconds later. That means
    # this test must seed a healthy baseline first -- starting at 3.5 V would
    # make "low" the baseline and there would be no transition to observe.
    state, config = initial_state(), CONFIG        # dwell_samples=3
    voltages = [
        (3.9, "ok"),      # 0: baseline established immediately -> "ok"
        (3.5, "ok"),      # 1: dwell 1 toward "low"
        (3.5, "ok"),      # 2: dwell 2
        (None, "error"),  # 3: gauge drops out -> dwell resets to 0
        (3.5, "ok"),      # 4: dwell 1 again (would have been 3 without the reset)
        (3.5, "ok"),      # 5: dwell 2 -- still short of 3
    ]
    last = None
    for index, (volts, status) in enumerate(voltages):
        state, last = step(
            state, snap(volts, t=float(index), pack_status=status), config, float(index)
        )
    # A disconnected gauge must not accumulate its way toward a level change,
    # because at the bottom of the scale that path ends in a shutdown.
    assert last.pack_level == "ok"


def test_runtime_none_before_enough_history():
    assert run([3.9, 3.88, 3.86])[-1].runtime_minutes is None


def test_runtime_history_resets_on_source_change():
    state, config = initial_state(), PowerConfig(dwell_samples=1)
    state, _ = step(state, snap(3.9, t=0.0, source="battery"), config, 0.0)
    state, _ = step(state, snap(3.88, t=1.0, source="battery"), config, 1.0)
    state, _ = step(state, snap(3.86, t=2.0, source="external"), config, 2.0)
    assert state.runtime_history == []


def test_runtime_history_resets_on_voltage_discontinuity():
    state, config = initial_state(), PowerConfig(dwell_samples=1)
    state, _ = step(state, snap(3.90, t=0.0), config, 0.0)
    state, _ = step(state, snap(3.88, t=1.0), config, 1.0)
    state, _ = step(state, snap(4.15, t=2.0), config, 2.0)  # hot-swapped pack
    assert len(state.runtime_history) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_power_policy_dwell.py -v`
Expected: FAIL with `ImportError: cannot import name 'initial_state'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/openflight/power/policy.py`:

```python
# --- reducer ---------------------------------------------------------------
#
# An earlier draft of the design claimed this module was "pure functions over a
# dataclass". That was wrong: dwell counting, hysteresis, runtime history and
# shutdown latching are all retained state. Making the state an explicit
# argument keeps determinism -- feed a list of snapshots, assert the decision
# sequence -- without a thread, a bus or a wall clock.

from dataclasses import dataclass, field, replace  # noqa: E402  (grouped with reducer)

from .models import PendingShutdown  # noqa: E402

# Minimum on-battery history before a runtime estimate is offered.
RUNTIME_MIN_SECONDS = 600.0
# A jump larger than this between consecutive samples is a pack change, not
# discharge. Resets the slope history rather than producing a wild estimate.
RUNTIME_DISCONTINUITY_VOLTS = 0.15


@dataclass(frozen=True)
class PolicyState:
    """Everything the reducer remembers between samples."""

    pack_level: PackLevel = "unknown"
    rail_level: RailLevel = "unknown"
    pending_pack_level: PackLevel | None = None
    pack_dwell: int = 0
    pending_rail_level: RailLevel | None = None
    rail_dwell: int = 0
    shutdown_dwell: int = 0
    pending_shutdown: PendingShutdown | None = None
    shutdown_cancelled: bool = False
    last_source: SourceState = "unknown"
    last_pack_volts: float | None = None
    runtime_history: list[tuple[float, float]] = field(default_factory=list)


@dataclass(frozen=True)
class Decision:
    """What the service should do with this sample."""

    pack_level: PackLevel
    rail_level: RailLevel
    source: SourceState
    shutdown_eligible: bool
    runtime_minutes: int | None
    warnings: list[str]


def initial_state() -> PolicyState:
    """Fresh state for a newly started service."""
    return PolicyState()


def _debounce(current, proposed, pending, dwell, dwell_samples):
    """Advance a dwell counter, returning (level, pending, dwell)."""
    if proposed == current:
        return current, None, 0
    if proposed != pending:
        return current, proposed, 1
    dwell += 1
    if dwell >= dwell_samples:
        return proposed, None, 0
    return current, proposed, dwell


def _pack_level_with_deadband(reading, source_state, state, config):
    """Pack level, biased against leaving the current level too eagerly.

    Recovering from ``low`` needs voltage above the threshold *plus* the
    deadband; without it, a pack hovering on a boundary flaps every sample.
    """
    raw = pack_level(reading, source_state, config)
    if raw == "unknown" or state.pack_level == "unknown":
        return raw
    order = ["critical", "low", "ok"]
    if order.index(raw) <= order.index(state.pack_level):
        return raw
    # Improving: require the deadband before believing it.
    volts = reading.volts
    threshold = (
        config.pack_low_volts if state.pack_level == "low" else config.pack_critical_volts
    )
    if volts is not None and volts < threshold + config.deadband_volts:
        return state.pack_level
    return raw


def _update_runtime_history(state, snapshot, now_monotonic):
    """Append to the discharge history, resetting when it stops being valid."""
    pack, source = snapshot.pack, snapshot.source.state
    if source != "battery" or pack.status != "ok" or pack.volts is None:
        return []
    history = state.runtime_history
    if source != state.last_source:
        history = []
    elif (
        state.last_pack_volts is not None
        and abs(pack.volts - state.last_pack_volts) > RUNTIME_DISCONTINUITY_VOLTS
    ):
        history = []
    return [*history, (now_monotonic, pack.volts)]


def _runtime_minutes(history, config) -> int | None:
    """Minutes remaining, or None when the history cannot support an estimate."""
    if len(history) < 2:
        return None
    span = history[-1][0] - history[0][0]
    if span < RUNTIME_MIN_SECONDS:
        return None
    drop = history[0][1] - history[-1][1]
    if drop <= 0:
        return None  # flat or rising: not discharging in a way we can extrapolate
    volts_per_second = drop / span
    remaining = history[-1][1] - config.shutdown_volts
    if remaining <= 0:
        return 0
    return int(remaining / volts_per_second / 60)


def step(
    state: PolicyState,
    snapshot: PowerSnapshot,
    config: PowerConfig,
    now_monotonic: float,
) -> tuple[PolicyState, Decision]:
    """Fold one snapshot into the retained state and emit a decision.

    ``now_monotonic`` is a parameter rather than read internally so durations
    are exact under test and immune to the clock stepping at boot.
    """
    source = snapshot.source.state

    # A reader error must never accumulate toward a level change -- a
    # disconnected gauge could otherwise dwell its way into a shutdown.
    if snapshot.pack.status != "ok":
        pack_target, pack_pending, pack_dwell = state.pack_level, None, 0
    else:
        pack_target, pack_pending, pack_dwell = _debounce(
            state.pack_level,
            _pack_level_with_deadband(snapshot.pack, source, state, config),
            state.pending_pack_level,
            state.pack_dwell,
            config.dwell_samples,
        )
        if state.pack_level == "unknown":
            pack_target, pack_pending, pack_dwell = (
                pack_level(snapshot.pack, source, config),
                None,
                0,
            )

    rail_target, rail_pending, rail_dwell = _debounce(
        state.rail_level,
        rail_level(snapshot.rail, config),
        state.pending_rail_level,
        state.rail_dwell,
        config.dwell_samples,
    )
    if state.rail_level == "unknown":
        rail_target, rail_pending, rail_dwell = rail_level(snapshot.rail, config), None, 0

    history = _update_runtime_history(state, snapshot, now_monotonic)
    eligible = shutdown_eligible(snapshot.pack, config)

    warnings: list[str] = []
    if pack_target == "low":
        warnings.append("Battery low")
    elif pack_target == "critical":
        warnings.append("Battery critically low")
    if rail_target == "amber":
        warnings.append("Supply voltage marginal")
    elif rail_target == "red":
        warnings.append("Supply voltage too low - brownout risk")

    new_state = replace(
        state,
        pack_level=pack_target,
        rail_level=rail_target,
        pending_pack_level=pack_pending,
        pack_dwell=pack_dwell,
        pending_rail_level=rail_pending,
        rail_dwell=rail_dwell,
        last_source=source,
        last_pack_volts=snapshot.pack.volts,
        runtime_history=history,
    )
    decision = Decision(
        pack_level=pack_target,
        rail_level=rail_target,
        source=source,
        shutdown_eligible=eligible,
        runtime_minutes=_runtime_minutes(history, config),
        warnings=warnings,
    )
    return new_state, decision
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_power_policy_dwell.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/openflight/power/policy.py tests/test_power_policy_dwell.py
git commit -m "feat(power): reducer with dwell, deadband and runtime history"
```

---

## Task 8: Policy — shutdown arming, cancellation, and the session latch

**Files:**
- Modify: `src/openflight/power/policy.py`
- Test: `tests/test_power_policy_shutdown.py`

**Interfaces:**
- Consumes: Tasks 6–7
- Produces: `Decision.shutdown_action: Literal["none", "arm", "execute"]`,
  `PolicyState.pending_shutdown`, `cancel_shutdown(state, shutdown_id) -> PolicyState`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_power_policy_shutdown.py
from openflight.power.config import PowerConfig
from openflight.power.models import PackReading, PowerSnapshot, RailReading, SourceReading
from openflight.power.policy import cancel_shutdown, initial_state, step

ARMED = PowerConfig(dwell_samples=2, auto_shutdown_enabled=True, shutdown_grace_seconds=60)


def snap(volts, t=0.0, source="battery"):
    return PowerSnapshot(
        timestamp=t,
        pack=PackReading(status="ok", timestamp=t, volts=volts, percent=5.0),
        rail=RailReading(status="ok", timestamp=t, ext5v_volts=5.2, throttled=0),
        source=SourceReading(status="ok", timestamp=t, state=source),
    )


def drive(voltages, config=ARMED, source="battery", state=None):
    state = state or initial_state()
    decision = None
    for index, volts in enumerate(voltages):
        state, decision = step(
            state, snap(volts, float(index), source), config, float(index)
        )
    return state, decision


def test_arms_after_dwell_when_all_conditions_hold():
    # dwell_samples=2, so the 2nd consecutive eligible sample arms it. Drive
    # exactly that many: a 3rd sample would leave the last decision as "none",
    # since arming happens once and the pending shutdown then just persists.
    state, decision = drive([3.1, 3.1])
    assert decision.shutdown_action == "arm"
    assert state.pending_shutdown is not None
    # Armed at the 2nd sample (now=1.0), not the last.
    assert state.pending_shutdown.deadline_monotonic == 1.0 + 60


def test_arming_is_not_repeated_while_pending():
    state, decision = drive([3.1, 3.1, 3.1, 3.1])
    assert decision.shutdown_action == "none"
    assert state.pending_shutdown is not None
    assert state.pending_shutdown.deadline_monotonic == 1.0 + 60   # unchanged


def test_disabled_never_arms():
    config = PowerConfig(dwell_samples=2, auto_shutdown_enabled=False)
    state, decision = drive([3.1, 3.1, 3.1], config=config)
    assert decision.shutdown_action == "none"
    assert state.pending_shutdown is None
    # ...but the pack is still visibly critical.
    assert decision.pack_level == "critical"


def test_external_power_never_arms():
    state, _ = drive([3.1, 3.1, 3.1], source="external")
    assert state.pending_shutdown is None


def test_unknown_source_never_arms():
    state, _ = drive([3.1, 3.1, 3.1], source="unknown")
    assert state.pending_shutdown is None


def test_above_threshold_never_arms():
    state, _ = drive([3.25, 3.25, 3.25])
    assert state.pending_shutdown is None


def test_single_low_sample_does_not_arm():
    state, _ = drive([3.9, 3.1, 3.9])
    assert state.pending_shutdown is None


def test_executes_at_deadline():
    state, _ = drive([3.1, 3.1, 3.1])
    state, decision = step(state, snap(3.1, 99.0), ARMED, 99.0)
    assert decision.shutdown_action == "execute"


def test_cancel_clears_pending_and_latches_for_the_process():
    state, _ = drive([3.1, 3.1, 3.1])
    state = cancel_shutdown(state, state.pending_shutdown.id)
    assert state.pending_shutdown is None
    assert state.shutdown_cancelled is True
    # No re-arming afterwards, at any voltage.
    state, decision = drive([3.0, 3.0, 3.0, 3.0], state=state)
    assert state.pending_shutdown is None
    assert decision.shutdown_action == "none"
    # The warning stays visible.
    assert decision.pack_level == "critical"


def test_stale_cancel_id_is_ignored():
    state, _ = drive([3.1, 3.1, 3.1])
    unchanged = cancel_shutdown(state, "not-the-current-id")
    assert unchanged.pending_shutdown is not None
    assert unchanged.shutdown_cancelled is False


def test_recovering_above_threshold_disarms_without_latching():
    state, _ = drive([3.1, 3.1, 3.1])
    assert state.pending_shutdown is not None
    state, _ = drive([3.9, 3.9, 3.9], state=state)
    assert state.pending_shutdown is None
    assert state.shutdown_cancelled is False   # not a user decision, so no latch
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_power_policy_shutdown.py -v`
Expected: FAIL with `ImportError: cannot import name 'cancel_shutdown'`

- [ ] **Step 3: Write minimal implementation**

In `policy.py`, add `import uuid` to the reducer imports, add
`shutdown_action: str = "none"` to `Decision`, then add:

```python
def cancel_shutdown(state: PolicyState, shutdown_id: str) -> PolicyState:
    """Cancel a pending shutdown and latch it off for the process lifetime.

    A stale id is ignored: that is the reconnect and double-click race, where
    a client's cancel arrives after a different shutdown has been armed.

    The latch is deliberate. Re-arming a user who has already declined, once a
    minute until the pack dies, is worse than respecting the decision -- the
    warning stays on screen either way.
    """
    if state.pending_shutdown is None or state.pending_shutdown.id != shutdown_id:
        return state
    return replace(state, pending_shutdown=None, shutdown_cancelled=True, shutdown_dwell=0)


def _shutdown_step(state, snapshot, config, now_monotonic, eligible, source):
    """Decide arming/execution. Returns (pending, dwell, action)."""
    pending = state.pending_shutdown

    if pending is not None:
        if now_monotonic >= pending.deadline_monotonic:
            return pending, 0, "execute"
        # Recovering disarms, but does not latch: nobody made a decision.
        if not eligible or source != "battery":
            return None, 0, "none"
        return pending, state.shutdown_dwell, "none"

    conditions = (
        config.auto_shutdown_enabled
        and source == "battery"
        and eligible
        and not state.shutdown_cancelled
    )
    if not conditions:
        return None, 0, "none"

    dwell = state.shutdown_dwell + 1
    if dwell < config.dwell_samples:
        return None, dwell, "none"
    armed = PendingShutdown(
        id=str(uuid.uuid4()),
        deadline_monotonic=now_monotonic + config.shutdown_grace_seconds,
        reason=f"Pack at {snapshot.pack.volts:.2f} V",
    )
    return armed, 0, "arm"
```

Then wire it into `step()` before building `new_state`:

```python
    pending, shutdown_dwell, action = _shutdown_step(
        state, snapshot, config, now_monotonic, eligible, source
    )
```

adding `pending_shutdown=pending, shutdown_dwell=shutdown_dwell` to the `replace(...)` call
and `shutdown_action=action` to the `Decision(...)` construction.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_power_policy_shutdown.py -v`
Expected: 11 passed

- [ ] **Step 5: Run the whole policy suite for regressions, then commit**

```bash
uv run pytest tests/test_power_policy_levels.py tests/test_power_policy_dwell.py tests/test_power_policy_shutdown.py -v
git add src/openflight/power/policy.py tests/test_power_policy_shutdown.py
git commit -m "feat(power): shutdown arming with five interlocks and a session latch"
```

---

## Task 9: Shutdown executor and the sampling service

**Files:**
- Create: `src/openflight/power/shutdown.py`, `src/openflight/power/service.py`
- Test: `tests/test_power_service.py`

**Interfaces:**
- Consumes: Tasks 1–8
- Produces: `halt() -> bool`; `PowerService(gauge, source, rail, config, on_view=None)` with
  `.start()`, `.stop()`, `.latest_view() -> PowerView`, `.cancel_shutdown(id) -> bool`,
  `.sample_once(now_monotonic)` (test seam)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_power_service.py
from openflight.power.config import PowerConfig
from openflight.power.models import PackReading, RailReading, SourceReading
from openflight.power.service import PowerService


class FakeGauge:
    def __init__(self, volts=3.9, status="ok"):
        self.volts, self.status, self.closed = volts, status, False

    def initialize(self):
        pass

    def read(self, *, timestamp):
        return PackReading(
            status=self.status, timestamp=timestamp,
            volts=self.volts if self.status == "ok" else None, percent=62.0,
        )

    def close(self):
        self.closed = True


class FakeSource:
    def __init__(self, state="battery"):
        self.state = state

    def read(self, *, timestamp):
        return SourceReading(status="ok", timestamp=timestamp, state=self.state)

    def close(self):
        pass


class FakeRail:
    def __init__(self, volts=5.21, status="ok"):
        self.volts, self.status = volts, status

    def read(self, *, timestamp):
        return RailReading(
            status=self.status, timestamp=timestamp,
            ext5v_volts=self.volts if self.status == "ok" else None, throttled=0,
        )

    def close(self):
        pass


def build(config=None, **kw):
    return PowerService(
        gauge=kw.get("gauge", FakeGauge()),
        source=kw.get("source", FakeSource()),
        rail=kw.get("rail", FakeRail()),
        config=config or PowerConfig(dwell_samples=1),
        halt=kw.get("halt", lambda: True),
    )


def test_view_carries_both_halves():
    service = build()
    service.sample_once(0.0)
    view = service.latest_view()
    assert view.pack_percent == 62.0
    assert view.rail_level == "green"
    assert view.source == "battery"


def test_partial_failure_keeps_the_other_reader_working():
    service = build(gauge=FakeGauge(status="error"))
    service.sample_once(0.0)
    view = service.latest_view()
    assert view.pack_level == "unknown"
    assert view.rail_level == "green"     # rail unaffected


def test_reader_recovers_after_a_failure():
    gauge = FakeGauge(status="error")
    service = build(gauge=gauge)
    service.sample_once(0.0)
    gauge.status = "ok"
    service.sample_once(1.0)
    assert service.latest_view().pack_level == "ok"


def test_halt_called_only_at_the_deadline():
    calls = []
    config = PowerConfig(
        dwell_samples=1, auto_shutdown_enabled=True, shutdown_grace_seconds=60
    )
    service = build(config=config, gauge=FakeGauge(volts=3.1), halt=lambda: calls.append(1))
    service.sample_once(0.0)
    assert service.latest_view().pending_shutdown is not None
    assert calls == []
    service.sample_once(30.0)
    assert calls == []
    service.sample_once(61.0)
    assert calls == [1]


def test_cancel_prevents_halt():
    calls = []
    config = PowerConfig(
        dwell_samples=1, auto_shutdown_enabled=True, shutdown_grace_seconds=60
    )
    service = build(config=config, gauge=FakeGauge(volts=3.1), halt=lambda: calls.append(1))
    service.sample_once(0.0)
    assert service.cancel_shutdown(service.latest_view().pending_shutdown.id) is True
    service.sample_once(61.0)
    assert calls == []


def test_cancel_with_stale_id_returns_false():
    config = PowerConfig(
        dwell_samples=1, auto_shutdown_enabled=True, shutdown_grace_seconds=60
    )
    service = build(config=config, gauge=FakeGauge(volts=3.1))
    service.sample_once(0.0)
    assert service.cancel_shutdown("stale") is False


def test_on_view_fires_on_level_change_not_every_sample():
    seen = []
    gauge = FakeGauge(volts=3.9)
    service = PowerService(
        gauge=gauge, source=FakeSource(), rail=FakeRail(),
        config=PowerConfig(dwell_samples=1), on_view=seen.append, halt=lambda: True,
    )
    service.sample_once(0.0)
    service.sample_once(1.0)          # unchanged
    gauge.volts = 3.5
    service.sample_once(2.0)          # now "low"
    assert len(seen) == 2


def test_stop_is_idempotent_and_closes_readers():
    gauge = FakeGauge()
    service = build(gauge=gauge)
    service.stop()
    service.stop()
    assert gauge.closed is True


def test_hardware_cleanup_runs_before_the_halt():
    order = []
    config = PowerConfig(
        dwell_samples=1, auto_shutdown_enabled=True, shutdown_grace_seconds=0
    )
    service = PowerService(
        gauge=FakeGauge(volts=3.1), source=FakeSource(), rail=FakeRail(),
        config=config,
        pre_halt=lambda: order.append("cleanup"),
        halt=lambda: order.append("halt") or True,
    )
    service.sample_once(0.0)
    service.sample_once(1.0)
    assert order == ["cleanup", "halt"]


def test_failing_halt_is_reported_and_not_retried():
    calls = []

    def failing_halt():
        calls.append(1)
        return False

    config = PowerConfig(
        dwell_samples=1, auto_shutdown_enabled=True, shutdown_grace_seconds=0
    )
    service = build(config=config, gauge=FakeGauge(volts=3.1), halt=failing_halt)
    service.sample_once(0.0)
    service.sample_once(1.0)
    service.sample_once(2.0)
    service.sample_once(3.0)
    # A failed halt is a visible degraded state, not a loop.
    assert calls == [1]
    assert "shut down manually" in " ".join(service.latest_view().warnings).lower()


def test_cancel_before_any_sample_does_not_crash():
    assert build().cancel_shutdown("anything") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_power_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'openflight.power.service'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/openflight/power/shutdown.py
"""The only module that can power off the machine.

Isolated to exactly one function so tests can stub it without touching the
service, and so no test run can halt a development machine. The existing
"shutdown" path in server.py is os._exit(0), which stops the server process --
halting the machine is a different capability and does not inherit from it.
"""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)


def halt() -> bool:
    """Power the machine off. Returns False on failure; never raises.

    A failed halt is a visible degraded state, not something to retry: looping
    on a permissions error would spam the log and never succeed.
    """
    try:
        subprocess.run(["systemctl", "poweroff"], check=True, timeout=10)
        return True
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.error(
            "[POWER] Automatic shutdown failed (%s). The pack will continue to "
            "discharge; shut down manually.",
            error,
        )
        return False
```

```python
# src/openflight/power/service.py
"""Sampling thread that owns the readers and the policy state."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace
from typing import Callable

from .config import PowerConfig
from .models import PowerSnapshot, PowerView
from .policy import Decision, cancel_shutdown, initial_state, step
from .shutdown import halt as default_halt

logger = logging.getLogger(__name__)


class PowerService:
    """Sample all three readers, fold them through the policy, publish a view."""

    def __init__(
        self,
        *,
        gauge,
        source,
        rail,
        config: PowerConfig,
        on_view: Callable[[PowerView], None] | None = None,
        halt: Callable[[], bool] | None = None,
        pre_halt: Callable[[], None] | None = None,
    ):
        self.gauge = gauge
        self.source = source
        self.rail = rail
        self.config = config
        self._on_view = on_view
        self._halt = halt or default_halt
        # Radars and other hardware are stopped before the machine goes down.
        # Injected rather than imported so this module stays ignorant of what
        # else the server owns.
        self._pre_halt = pre_halt
        self._state = initial_state()
        self._view = _empty_view()
        self._last_decision = None
        self._last_snapshot = None
        self._halt_failed = False
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False

    def start(self) -> None:
        """Start the sampling thread."""
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="openflight-power", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop sampling and release the readers. Idempotent."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._closed:
            return
        self._closed = True
        for reader in (self.gauge, self.source, self.rail):
            try:
                reader.close()
            except Exception as error:  # pylint: disable=broad-exception-caught
                logger.warning("[POWER] closing a reader failed: %s", error)

    def latest_view(self) -> PowerView:
        """Most recent published view."""
        with self._lock:
            return self._view

    def cancel_shutdown(self, shutdown_id: str) -> bool:
        """Cancel a pending shutdown. False if the id is stale."""
        with self._lock:
            before = self._state.pending_shutdown
            self._state = cancel_shutdown(self._state, shutdown_id)
            cancelled = before is not None and self._state.pending_shutdown is None
            # A cancel can only be genuine after a sample armed something, but
            # a stray client message before the first sample must not crash.
            if cancelled and self._last_decision is not None:
                self._view = _view_from(self._state, self._last_decision, self._last_snapshot)
        if cancelled and self._on_view:
            self._on_view(self.latest_view())
        return cancelled

    def sample_once(self, now_monotonic: float) -> PowerView:
        """Read every source once and fold it in. The loop's unit of work."""
        timestamp = time.time()
        snapshot = PowerSnapshot(
            timestamp=timestamp,
            pack=self.gauge.read(timestamp=timestamp),
            rail=self.rail.read(timestamp=timestamp),
            source=self.source.read(timestamp=timestamp),
        )
        with self._lock:
            previous = self._view
            self._state, decision = step(
                self._state, snapshot, self.config, now_monotonic
            )
            self._last_decision, self._last_snapshot = decision, snapshot
            self._view = _view_from(self._state, decision, snapshot)
            view, changed = self._view, _materially_changed(previous, self._view)

        # Halt last, and only once. Hardware is stopped first so radars and the
        # sampling thread are down before the machine is; a failed halt is a
        # visible degraded state rather than something to retry every sample.
        if decision.shutdown_action == "execute" and not self._halt_failed:
            logger.warning("[POWER] Automatic shutdown: %s", self._state.pending_shutdown.reason)
            if self._pre_halt is not None:
                try:
                    self._pre_halt()
                except Exception as error:  # pylint: disable=broad-exception-caught
                    logger.warning("[POWER] pre-shutdown cleanup failed: %s", error)
            if not self._halt():
                self._halt_failed = True
                with self._lock:
                    self._view = replace(
                        self._view,
                        warnings=[
                            *self._view.warnings,
                            "Automatic shutdown failed - shut down manually",
                        ],
                    )
                view, changed = self.latest_view(), True

        if changed and self._on_view:
            self._on_view(view)
        return view

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            started = time.monotonic()
            try:
                self.sample_once(started)
            except Exception as error:  # pylint: disable=broad-exception-caught
                # The loop outliving a surprise matters more than the sample.
                logger.warning("[POWER] sample failed: %s", error)
            delay = max(0.0, self.config.sample_interval_s - (time.monotonic() - started))
            self._stop_event.wait(delay)


def _empty_view() -> PowerView:
    return PowerView(
        pack_volts=None, pack_percent=None, pack_level="unknown",
        rail_volts=None, rail_level="unknown", source="unknown",
        runtime_minutes=None, shutdown_eligible=False, pending_shutdown=None, warnings=[],
    )


def _view_from(state, decision: Decision, snapshot: PowerSnapshot) -> PowerView:
    return PowerView(
        pack_volts=snapshot.pack.volts,
        pack_percent=snapshot.pack.percent,
        pack_level=decision.pack_level,
        rail_volts=snapshot.rail.ext5v_volts,
        rail_level=decision.rail_level,
        source=decision.source,
        runtime_minutes=decision.runtime_minutes,
        shutdown_eligible=decision.shutdown_eligible,
        pending_shutdown=state.pending_shutdown,
        warnings=decision.warnings,
    )


def _materially_changed(before: PowerView, after: PowerView) -> bool:
    """True when something worth pushing to clients changed.

    Voltage drifts every sample; levels and shutdown state do not. Emitting on
    every sample would put a needless message on the socket every 2 seconds.
    """
    return (
        before.pack_level != after.pack_level
        or before.rail_level != after.rail_level
        or before.source != after.source
        or before.pending_shutdown != after.pending_shutdown
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_power_service.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add src/openflight/power/shutdown.py src/openflight/power/service.py tests/test_power_service.py
git commit -m "feat(power): sampling service with isolated halt and change-driven emit"
```

---

## Task 10: Server integration

**Files:**
- Modify: `src/openflight/server.py`
- Test: `tests/test_power_server.py`

**Interfaces:**
- Consumes: Tasks 1–9
- Produces: `init_power(config_path=None, **overrides) -> bool`; socket events `power`,
  `power_shutdown_pending`, `power_shutdown_cancelled`; handlers `get_power`,
  `power_shutdown_cancel`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_power_server.py
from openflight import server


def teardown_function():
    if server.power_service:
        server.power_service.stop()
    server.power_service = None
    server.power_runtime_config = {"enabled": False}


def test_init_returns_false_when_no_readers_available(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_build_power_gauge", lambda config: None)
    monkeypatch.setattr(server, "_build_power_source", lambda config: None)
    monkeypatch.setattr(server, "_build_power_rail", lambda config: None)
    assert server.init_power(config_path=tmp_path / "power.json") is False
    assert server.power_runtime_config["enabled"] is False


def test_init_returns_true_when_only_the_rail_is_present(monkeypatch, tmp_path):
    """A Pi 5 on wall power with no UPS still has useful rail health."""
    class Rail:
        def read(self, *, timestamp):
            from openflight.power.models import RailReading
            return RailReading(status="ok", timestamp=timestamp,
                               ext5v_volts=5.2, throttled=0)

        def close(self):
            pass

    monkeypatch.setattr(server, "_build_power_gauge", lambda config: None)
    monkeypatch.setattr(server, "_build_power_source", lambda config: None)
    monkeypatch.setattr(server, "_build_power_rail", lambda config: Rail())
    assert server.init_power(config_path=tmp_path / "power.json") is True
    assert server.power_runtime_config["enabled"] is True


def test_disabled_config_skips_initialization(monkeypatch, tmp_path):
    path = tmp_path / "power.json"
    path.write_text('{"enabled": false}', encoding="utf-8")
    assert server.init_power(config_path=path) is False


def test_session_start_config_reports_power_block(monkeypatch, tmp_path):
    server.power_runtime_config = {"enabled": True, "board": "x1209"}
    assert server._session_start_config()["power"]["board"] == "x1209"


def test_stop_is_safe_when_called_from_the_sampling_thread():
    """The automatic-shutdown path re-enters stop() from inside its own thread.

    _loop -> sample_once -> pre_halt -> _cleanup_hardware_for_shutdown ->
    power_service.stop(). Joining the current thread raises RuntimeError, and
    this is the one path no other test exercises.
    """
    import time

    from openflight.power.config import PowerConfig
    from openflight.power.service import PowerService
    from tests.test_power_service import FakeGauge, FakeRail, FakeSource

    calls = []
    service = None

    def cleanup():
        calls.append("cleanup")
        service.stop()          # exactly what the server's cleanup does

    service = PowerService(
        gauge=FakeGauge(volts=3.1), source=FakeSource(), rail=FakeRail(),
        config=PowerConfig(
            dwell_samples=1, auto_shutdown_enabled=True,
            shutdown_grace_seconds=0, sample_interval_s=0.01,
        ),
        pre_halt=cleanup,
        halt=lambda: calls.append("halt") or True,
    )
    service.start()
    for _ in range(200):
        if "halt" in calls:
            break
        time.sleep(0.01)
    service.stop()
    assert calls == ["cleanup", "halt"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_power_server.py -v`
Expected: FAIL with `AttributeError: module 'openflight.server' has no attribute 'init_power'`

- [ ] **Step 3: Amend `PowerService.stop()` to be current-thread-safe**

Wiring `pre_halt=_cleanup_hardware_for_shutdown` creates a re-entry Task 9 did not
have: the server's cleanup calls `power_service.stop()`, and on the automatic-shutdown
path that cleanup runs *on the sampling thread*. `Thread.join()` on the current thread
raises. In `src/openflight/power/service.py`:

```python
    def stop(self) -> None:
        """Stop sampling and release the readers.

        Idempotent, and safe to call from the sampling thread itself. The
        automatic-shutdown path arrives here from inside that thread --
        _loop -> sample_once -> pre_halt -> the server's hardware cleanup ->
        here -- and joining our own thread would raise RuntimeError. The stop
        event is already set in that case, so the loop exits once the current
        iteration returns.
        """
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
            self._thread = None
        if self._closed:
            return
        self._closed = True
        for reader in (self.gauge, self.source, self.rail):
            try:
                reader.close()
            except Exception as error:  # pylint: disable=broad-exception-caught
                logger.warning("[POWER] closing a reader failed: %s", error)
```

- [ ] **Step 4: Write minimal implementation**

Near the `inclinometer_service` declarations at `server.py:130`:

```python
power_service = None
power_runtime_config: dict = {"enabled": False}
```

Add the builder seams and `init_power` alongside `init_inclinometer` (~`server.py:1136`):

```python
def _build_power_gauge(config):
    """Return a fuel gauge, or None when none answers. Separated for tests."""
    from .power.max1704x import MAX1704X  # pylint: disable=import-outside-toplevel

    try:
        gauge = MAX1704X(bus_number=config.i2c_bus, address=config.i2c_address)
        gauge.initialize()
        return gauge
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.info("[POWER] No fuel gauge at 0x%02x (%s)", config.i2c_address, error)
        return None


def _build_power_source(config):
    """Return a PLD reader, or None when no line is declared.

    Nothing is configured without a declaration: auto-probing a GPIO would
    silently reconfigure a pin on builds using it for something else.
    """
    if config.pld_gpio is None:
        return None
    from gpiozero import Button  # pylint: disable=import-outside-toplevel,import-error

    from .gpio_factory import ensure_lgpio_pin_factory  # pylint: disable=import-outside-toplevel
    from .power.source import GpioPldSource  # pylint: disable=import-outside-toplevel

    try:
        ensure_lgpio_pin_factory()
        button = Button(config.pld_gpio, pull_up=True)

        class _Pin:
            def read(self):
                # gpiozero Button is active-low: is_pressed True means the line
                # is LOW, which for a PLD line means running on battery.
                return not button.is_pressed

            def close(self):
                button.close()

        return GpioPldSource(
            pin=config.pld_gpio, trusted=config.pld_trusted, pin_reader=_Pin()
        )
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.warning("[POWER] GPIO %d unavailable (%s)", config.pld_gpio, error)
        return None


def _build_power_rail(config):  # pylint: disable=unused-argument
    """Return a rail reader, or None on hardware without a PMIC ADC."""
    from .power.pmic import PmicRail  # pylint: disable=import-outside-toplevel

    rail = PmicRail()
    return rail if rail.read(timestamp=0.0).status != "absent" else None


def init_power(*, config_path=None, **overrides) -> bool:
    """Start power monitoring. True if any reader initialised.

    Returning False on a single reader's absence would contradict the support
    matrix: a Pi 5 on wall power with no UPS has no gauge and still benefits
    from rail health.
    """
    global power_service, power_runtime_config  # pylint: disable=global-statement

    from .power.config import CONFIG_PATH, load_config  # pylint: disable=import-outside-toplevel
    from .power.service import PowerService  # pylint: disable=import-outside-toplevel
    from .power.source import NullSource  # pylint: disable=import-outside-toplevel

    config = load_config(config_path or CONFIG_PATH)
    for key, value in overrides.items():
        if value is not None:
            config = dataclasses.replace(config, **{key: value})

    if not config.enabled:
        power_runtime_config = {"enabled": False, "reason": "disabled by configuration"}
        return False

    gauge = _build_power_gauge(config)
    source = _build_power_source(config)
    rail = _build_power_rail(config)

    if gauge is None and rail is None:
        power_runtime_config = {"enabled": False, "reason": "no power hardware detected"}
        return False

    power_service = PowerService(
        gauge=gauge or _NullGauge(),
        source=source or NullSource(),
        rail=rail or _NullRail(),
        config=config,
        on_view=_emit_power_view,
    )
    power_service.start()
    power_runtime_config = {
        "enabled": True,
        "board": config.board,
        "gauge": gauge is not None,
        "source": source is not None,
        "rail": rail is not None,
        "auto_shutdown_enabled": config.auto_shutdown_enabled,
    }
    logger.info("[POWER] Monitoring started: %s", power_runtime_config)
    return True


def _emit_power_view(view) -> None:
    """Push a view to every client, plus shutdown lifecycle events."""
    socketio.emit("power", view.to_dict())
    if view.pending_shutdown is not None:
        socketio.emit("power_shutdown_pending", dataclasses.asdict(view.pending_shutdown))


@socketio.on("get_power")
def handle_get_power():
    """Initial sync on connect, matching get_session / get_trigger_status."""
    if power_service:
        socketio.emit("power", power_service.latest_view().to_dict())


@socketio.on("power_shutdown_cancel")
def handle_power_shutdown_cancel(data):
    """Cancel a pending shutdown; broadcast so every client agrees."""
    if not power_service:
        return
    shutdown_id = (data or {}).get("id", "")
    if power_service.cancel_shutdown(shutdown_id):
        socketio.emit("power_shutdown_cancelled", {"id": shutdown_id})
```

Add the stand-ins for absent readers, so the service always has three objects to call and
never needs a `None` check in its hot loop:

```python
class _NullGauge:
    """Stands in for an absent fuel gauge."""

    def initialize(self) -> None:
        """Nothing to configure."""

    def read(self, *, timestamp):
        from .power.models import PackReading  # pylint: disable=import-outside-toplevel

        return PackReading(status="absent", timestamp=timestamp)

    def close(self) -> None:
        """No resources to release."""


class _NullRail:
    """Stands in for hardware with no PMIC ADC."""

    def read(self, *, timestamp):
        from .power.models import RailReading  # pylint: disable=import-outside-toplevel

        return RailReading(status="absent", timestamp=timestamp)

    def close(self) -> None:
        """No resources to release."""
```

Pass the existing hardware cleanup into the service so radars are stopped before the machine
halts, per spec §6.4 — in the `PowerService(...)` construction inside `init_power`:

```python
        pre_halt=_cleanup_hardware_for_shutdown,
```

Register the stop step next to the inclinometer's at `server.py:202`:

```python
    if power_service:
        _run_shutdown_step("power monitor stop", power_service.stop)
```

And inside `_session_start_config()` (`server.py:842`), beside the inclinometer line:

```python
    config["power"] = dict(power_runtime_config)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_power_server.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add src/openflight/server.py tests/test_power_server.py
git commit -m "feat(power): server wiring, socket events and initial sync"
```

---

## Task 11: UI store and indicator

**Files:**
- Create: `ui/src/stores/usePowerStore.ts`, `ui/src/components/BatteryStatus.tsx`,
  `ui/src/components/BatteryStatus.css`
- Modify: `ui/src/services/socketService.ts`
- Test: `ui/src/components/BatteryStatus.test.tsx`

**Interfaces:**
- Consumes: the `power` / `power_shutdown_pending` / `power_shutdown_cancelled` events
- Produces: `usePowerStore`, `<BatteryStatus />`

- [ ] **Step 1: Write the failing test**

```tsx
// ui/src/components/BatteryStatus.test.tsx
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { BatteryStatus } from './BatteryStatus';
import { usePowerStore } from '../stores/usePowerStore';

const base = {
  pack_volts: 3.81, pack_percent: 62, pack_level: 'ok' as const,
  rail_volts: 5.09, rail_level: 'green' as const, source: 'battery' as const,
  runtime_minutes: null, shutdown_eligible: false,
  pending_shutdown: null, warnings: [],
};

beforeEach(() => usePowerStore.setState({ view: null }));

describe('BatteryStatus', () => {
  it('renders nothing when no power data has arrived', () => {
    const { container } = render(<BatteryStatus />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when every reader is absent', () => {
    usePowerStore.setState({
      view: { ...base, pack_level: 'unknown', rail_level: 'unknown', pack_percent: null },
    });
    const { container } = render(<BatteryStatus />);
    expect(container).toBeEmptyDOMElement();
  });

  it('shows the percentage', () => {
    usePowerStore.setState({ view: base });
    render(<BatteryStatus />);
    expect(screen.getByText('62%')).toBeInTheDocument();
  });

  it('shows the percentage on external power too', () => {
    usePowerStore.setState({ view: { ...base, source: 'external' } });
    render(<BatteryStatus />);
    expect(screen.getByText('62%')).toBeInTheDocument();
    expect(screen.getByLabelText(/external power/i)).toBeInTheDocument();
  });

  it('shows the rail dot with its level as a class', () => {
    usePowerStore.setState({ view: { ...base, rail_level: 'amber' } });
    render(<BatteryStatus />);
    expect(screen.getByLabelText(/supply health/i)).toHaveClass('battery-status__dot--amber');
  });

  it('hides the fuel bar when only the rail is present', () => {
    usePowerStore.setState({ view: { ...base, pack_level: 'unknown', pack_percent: null } });
    render(<BatteryStatus />);
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
    expect(screen.getByLabelText(/supply health/i)).toBeInTheDocument();
  });

  it('shows a Keep running button while a shutdown is pending', () => {
    usePowerStore.setState({
      view: {
        ...base, pack_level: 'critical',
        pending_shutdown: { id: 'abc', deadline_monotonic: 0, reason: 'Pack at 3.18 V' },
      },
    });
    render(<BatteryStatus />);
    expect(screen.getByRole('button', { name: /keep running/i })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ui && npx vitest run src/components/BatteryStatus.test.tsx`
Expected: FAIL — cannot resolve `./BatteryStatus`

- [ ] **Step 3: Write minimal implementation**

```ts
// ui/src/stores/usePowerStore.ts
import { create } from 'zustand';

export interface PendingShutdown {
  id: string;
  deadline_monotonic: number;
  reason: string;
}

export interface PowerView {
  pack_volts: number | null;
  pack_percent: number | null;
  pack_level: 'ok' | 'low' | 'critical' | 'unknown';
  rail_volts: number | null;
  rail_level: 'green' | 'amber' | 'red' | 'unknown';
  source: 'external' | 'battery' | 'unknown';
  runtime_minutes: number | null;
  shutdown_eligible: boolean;
  pending_shutdown: PendingShutdown | null;
  warnings: string[];
}

interface PowerState {
  view: PowerView | null;
  setView: (view: PowerView) => void;
}

export const usePowerStore = create<PowerState>((set) => ({
  view: null,
  setView: (view) => set({ view }),
}));
```

```tsx
// ui/src/components/BatteryStatus.tsx
import { useState } from 'react';
import { usePowerStore } from '../stores/usePowerStore';
import { socketService } from '../services/socketService';
import './BatteryStatus.css';

/**
 * Battery level and supply health.
 *
 * Two indicators because there are two independent failure modes: an
 * exhausted pack and a sagging 5V rail end a session for different reasons.
 * Each half renders only when its reader is present, so a build with a UPS
 * and no Pi 5 PMIC shows a bar and no dot, and vice versa.
 */
export function BatteryStatus() {
  const view = usePowerStore((state) => state.view);
  const [expanded, setExpanded] = useState(false);

  if (!view) return null;

  const hasPack = view.pack_level !== 'unknown' && view.pack_percent !== null;
  const hasRail = view.rail_level !== 'unknown';
  if (!hasPack && !hasRail) return null;

  const pending = view.pending_shutdown;

  return (
    <div className="battery-status">
      <button
        type="button"
        className="battery-status__summary"
        onClick={() => setExpanded((open) => !open)}
      >
        {hasPack && (
          <>
            <span
              className={`battery-status__bar battery-status__bar--${view.pack_level}`}
              style={{ '--fill': `${view.pack_percent}%` } as React.CSSProperties}
            />
            <span className="battery-status__percent">{Math.round(view.pack_percent!)}%</span>
          </>
        )}
        {view.source === 'external' && (
          <span className="battery-status__bolt" aria-label="On external power">
            ⚡
          </span>
        )}
        {hasRail && (
          <span
            className={`battery-status__dot battery-status__dot--${view.rail_level}`}
            aria-label={`Supply health: ${view.rail_level}`}
          />
        )}
      </button>

      {expanded && (
        <dl className="battery-status__detail">
          {hasPack && (
            <>
              <dt>Pack</dt>
              <dd>
                {view.pack_volts?.toFixed(2)} V · {Math.round(view.pack_percent!)}% ·{' '}
                {view.source === 'external' ? 'external power' : 'on battery'}
              </dd>
            </>
          )}
          {hasRail && (
            <>
              <dt>Rail</dt>
              <dd>{view.rail_volts?.toFixed(2)} V</dd>
            </>
          )}
          {view.runtime_minutes !== null && (
            <>
              <dt>Est.</dt>
              <dd>~{view.runtime_minutes} min</dd>
            </>
          )}
        </dl>
      )}

      {view.warnings.length > 0 && (
        <div className="battery-status__warning" role="status">
          {view.warnings.join(' · ')}
        </div>
      )}

      {pending && (
        <div className="battery-status__shutdown" role="alert">
          <span>Shutting down to protect the battery. {pending.reason}.</span>
          <button
            type="button"
            onClick={() => socketService.cancelShutdown(pending.id)}
          >
            Keep running
          </button>
        </div>
      )}
    </div>
  );
}
```

In `socketService.ts`, add `this.socket?.emit('get_power');` to the `connect` handler
alongside the existing `get_session` / `get_trigger_status` / `get_radar_config`, plus:

```ts
    this.socket.on('power', (view) => usePowerStore.getState().setView(view));
```

and a method:

```ts
  cancelShutdown(id: string) {
    this.socket?.emit('power_shutdown_cancel', { id });
  }
```

```css
/* ui/src/components/BatteryStatus.css */
.battery-status {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  font-size: 0.85rem;
}

.battery-status__summary {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  background: none;
  border: none;
  color: inherit;
  cursor: pointer;
  padding: 0.2rem 0.4rem;
}

.battery-status__bar {
  position: relative;
  width: 2.6rem;
  height: 1rem;
  border: 1px solid currentColor;
  border-radius: 2px;
}

.battery-status__bar::after {
  content: '';
  position: absolute;
  inset: 2px;
  width: calc(var(--fill) - 4px);
  background: currentColor;
}

.battery-status__bar--low { color: #e0a800; }
.battery-status__bar--critical { color: #d9534f; }

.battery-status__dot {
  width: 0.6rem;
  height: 0.6rem;
  border-radius: 50%;
  background: #6c757d;
}

.battery-status__dot--green { background: #3fb950; }
.battery-status__dot--amber { background: #e0a800; }
.battery-status__dot--red { background: #d9534f; }

.battery-status__detail {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0.15rem 0.6rem;
  margin: 0;
  opacity: 0.85;
}

.battery-status__warning { color: #e0a800; }

.battery-status__shutdown {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.4rem 0.6rem;
  border: 1px solid #d9534f;
  border-radius: 4px;
  color: #d9534f;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ui && npx vitest run src/components/BatteryStatus.test.tsx`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add ui/src/stores/usePowerStore.ts ui/src/components/BatteryStatus.tsx \
        ui/src/components/BatteryStatus.css ui/src/components/BatteryStatus.test.tsx \
        ui/src/services/socketService.ts
git commit -m "feat(power): battery indicator with rail dot and explicit shutdown cancel"
```

---

## Task 12: CLI flags, startup script, and docs

**Files:**
- Modify: `src/openflight/server.py` (argparse), `scripts/start-kiosk.sh`
- Create: `docs/power/README.md`

**Interfaces:**
- Consumes: `init_power` (Task 10)
- Produces: `--power`, `--no-power`, `--power-board`, `--power-shutdown`,
  `--power-shutdown-volts`

- [ ] **Step 1: Add the flags**

Beside the `--inclinometer` arguments:

```python
    parser.add_argument("--power", action="store_true",
                        help="Enable battery and supply-health monitoring")
    parser.add_argument("--no-power", action="store_true",
                        help="Disable power monitoring even if power.json enables it")
    parser.add_argument("--power-board", default=None,
                        help="UPS board profile, e.g. x1209. Declares the power-loss GPIO")
    parser.add_argument("--power-shutdown", action="store_true",
                        help="Automatically shut down when the pack reaches the "
                             "critical threshold. Off unless given")
    parser.add_argument("--power-shutdown-volts", type=float, default=None,
                        help="Pack voltage at which automatic shutdown fires (default 3.2)")
```

- [ ] **Step 2: Wire precedence in `main()`**

Beside the `args.inclinometer` block (~`server.py:4092`). `--no-power` wins over everything,
including a config file that enables monitoring:

```python
    if args.no_power:
        logger.info("[POWER] Disabled by --no-power")
    elif args.power or load_config(CONFIG_PATH).enabled:
        init_power(
            board=args.power_board,
            auto_shutdown_enabled=True if args.power_shutdown else None,
            shutdown_volts=args.power_shutdown_volts,
        )
```

- [ ] **Step 3: Add start-kiosk.sh passthrough**

Beside the `--inclinometer` cases (~line 204) and the `SERVER_CMD` assembly (~line 577):

```bash
        --power) POWER=1; shift ;;
        --no-power) NO_POWER=1; shift ;;
        --power-board) POWER_BOARD="$2"; shift 2 ;;
        --power-shutdown) POWER_SHUTDOWN=1; shift ;;
```

```bash
[ -n "$POWER" ] && SERVER_CMD="$SERVER_CMD --power"
[ -n "$NO_POWER" ] && SERVER_CMD="$SERVER_CMD --no-power"
[ -n "$POWER_BOARD" ] && SERVER_CMD="$SERVER_CMD --power-board $POWER_BOARD"
[ -n "$POWER_SHUTDOWN" ] && SERVER_CMD="$SERVER_CMD --power-shutdown"
```

- [ ] **Step 4: Write `docs/power/README.md`**

Cover: supported hardware (Geekworm X120x/X12xx, UPS-Lite); the one-line
`{"board": "x1209"}` config; every key from spec §9.1 with its default and range; the trust
rule for `pld_gpio` without a known board; how to verify with `i2cdetect -y 1` and
`pinctrl set 6 ip pu; pinctrl get 6`; that rail health needs a Pi 5; that percentage is
whole-pack and cannot resolve one bad cell in a 1S4P holder; and that auto-shutdown is
opt-in.

- [ ] **Step 5: Full verification, then commit**

```bash
uv run pytest tests/ -v
uv run pylint src/openflight/ --fail-under=9
uv run ruff check src/openflight/
uv run ruff format --check src/openflight/
cd ui && npm run lint && npm run build && cd ..
```

All must pass before committing.

```bash
git add src/openflight/server.py scripts/start-kiosk.sh docs/power/README.md
git commit -m "feat(power): CLI flags, kiosk passthrough and hardware docs"
```

---

## Verification on the Pi

After Task 12, merge into the integration branch and run it on hardware:

```bash
git -C ../openflight-weather merge feat/battery-status
```

Merges go one direction only — feature into integration, never back — so PR 1 stays clean.

Manual checks that automated tests cannot cover:

1. Indicator shows a percentage matching `i2cget -y 1 0x36 0x04 w` (low byte)
2. Unplug the wall supply: bolt disappears, source flips to `on battery` within ~2 s
3. Reconnect: bolt returns, percentage keeps updating
4. Reload the browser mid-session: state appears immediately, not after 10 s
5. With `--power-shutdown` and `shutdown_volts` temporarily raised above the current pack
   voltage, confirm the countdown appears and **"Keep running"** cancels it — then confirm
   it does not re-arm
6. `--no-power` suppresses the indicator entirely

Step 5 is the one worth doing deliberately: it is the only path that can power the machine
off, and it has never run outside a stub.
