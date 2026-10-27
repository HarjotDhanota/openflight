"""The in-repo TM4 normalized export, read in place for the regression checks."""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Optional

EXPORT = Path(__file__).resolve().parents[1] / "session_logs" / "OpenFlight-Test.Normalized.csv"


def owner_shots() -> list[dict[str, str]]:
    """One dict per shot, keyed by the export's column names (units row skipped)."""
    lines = EXPORT.read_text(encoding="utf-8-sig").splitlines()
    while lines and lines[0].lstrip("﻿").startswith("sep="):
        lines = lines[1:]
    rows = list(csv.reader(lines))
    header = rows[0]
    return [dict(zip(header, row)) for row in rows[2:] if any(cell.strip() for cell in row)]


def number(shot: dict[str, str], column: str) -> Optional[float]:
    try:
        value = float(shot.get(column, ""))
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def rms(values) -> float:
    values = list(values)
    return math.sqrt(sum(v * v for v in values) / len(values))
