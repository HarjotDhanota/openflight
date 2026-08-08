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
    heartbeat_seconds: float = 10.0
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
        heartbeat_seconds=_number(
            data, "heartbeat_seconds", defaults.heartbeat_seconds, 1.0, 300.0
        ),
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
            parsed_pin = _integer(data, "pld_gpio", -1, 0, 27)
            config = replace(
                config,
                pld_gpio=None if parsed_pin == -1 else parsed_pin,
                pld_trusted=False,
            )
    return config
