"""Refit the spin fallback (spin-refit-v1) to TM exports, cross-validate it, write its data.

The spin chain's kinematic route (``spin_estimate.refit_spin``) is a fitted model
(D38). This script fits each of its tiers to TM exports' measured shots, using only
TM's outputs per shot (club, ball speed, club speed, vertical launch, total spin;
attack angle and path for the comparators), and reports the held-out error:

- tiers, one power law each, ``spin = coefficient * speed * sin(angle)^exponent``:
  ``club_speed`` (full club speed, the club's nominal loft), ``club`` (ball speed,
  nominal loft), ``launch`` (ball speed, vertical launch; exponent kept at 1.2 from
  the first fit, coefficient refitted). The launch is not a tier input once the
  club is known: ``--explore`` shows its fitted exponent is ~0 there;
- cross-validation grouped by session (one export = one session = one player):
  leave-one-session-out is the headline, leave-one-club-out shows the nominal loft
  carrying the model to clubs it never saw. Folds never split a session;
- comparators on the same held-out shots: the first fit ``170 v sin(LA)^1.2``, and,
  with TM's attack angle and path (inputs the fallback does not have), the collision
  model's forward spin and Penner 2001;
- each tier's 68 % range: the 16th and 84th percentiles of its leave-one-session-out
  ratio truth / prediction over every TM-measured shot it would run on, mishits
  included, per club family where the family has 15 shots or more;
- the OPS radial ball speed's shortfall against the true speed (fit on true speeds):
  the 95th percentile of ``1 - speed_correction.radial_speed_factor`` over the fitted
  shots, at a ball 2 m in front of the OPS and 0.15 m below it.

The fit's domain is decided on inputs alone, before any residual is seen: a spin TM
measured (not estimated), ball speed >= 50 mph, vertical launch >= 5 deg, no topspin.

With no arguments it reads the in-repo ``session_logs/OpenFlight-Test.Normalized.csv``
and, when present beside the checkout, the two SwingSync exports below (someone
else's data: read from there, never copied into the repo). An export whose bytes
match one already read is skipped (the same session under a second name).

    uv run python scripts/analysis/fit_spin_refit.py            # report only
    uv run python scripts/analysis/fit_spin_refit.py --write    # also write the data file
    uv run python scripts/analysis/fit_spin_refit.py --pinned   # rows for the held-out test
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from openflight import collision_forward as cf  # noqa: E402, I001
from openflight import metric_definitions as md  # noqa: E402
from openflight.clubs.physics import get_club_physics  # noqa: E402
from openflight.clubs.types import ClubType  # noqa: E402
from openflight.collision_model import penner_impact  # noqa: E402
from openflight.speed_correction import radial_speed_factor  # noqa: E402
from openflight.spin_estimate import (  # noqa: E402
    MAX_SPIN_RPM,
    SPIN_LA_EXPONENT,
    SPIN_REFIT_PATH,
    TIER_CLUB,
    TIER_CLUB_SPEED,
    TIER_LAUNCH,
    calculated_spin_rpm,
    club_category,
    fit_power_law,
)

VERSION = "spin-refit-v1"
FITTED_ON = "2026-10-25"
MPH_TO_M_S = 0.44704

IN_REPO = [_REPO_ROOT / "session_logs" / "OpenFlight-Test.Normalized.csv"]
BESIDE = _REPO_ROOT.parent
EXTERNAL = [
    BESIDE / "tm-exports" / "tm4-public-swingsync-2026-10-12.csv",
    BESIDE
    / "openflight-test-bench"
    / "archive"
    / "analysis-archive"
    / "2026-10-04-session-f86a2951"
    / "trackman-1004"
    / "swingsync-2026-10-04.csv",
]
ORIGIN = {
    "OpenFlight-Test.Normalized.csv": "a TM4 normalized export, 6 May 2026 (one player)",
    "tm4-public-swingsync-2026-10-12.csv": "a public TM4 SwingSync session, 12 Oct 2026",
    "swingsync-2026-10-04.csv": "a TM4 SwingSync session, 4 Oct 2026 (test-bench archive)",
}

CLUBS = {
    "driver": ClubType.DRIVER,
    "3 wood": ClubType.WOOD_3,
    "5 wood": ClubType.WOOD_5,
    "7 wood": ClubType.WOOD_7,
    "3 hybrid": ClubType.HYBRID_3,
    "4 hybrid": ClubType.HYBRID_3,
    "5 hybrid": ClubType.HYBRID_5,
    "3 iron": ClubType.IRON_3,
    "4 iron": ClubType.IRON_4,
    "5 iron": ClubType.IRON_5,
    "6 iron": ClubType.IRON_6,
    "7 iron": ClubType.IRON_7,
    "8 iron": ClubType.IRON_8,
    "9 iron": ClubType.IRON_9,
    "pitching wedge": ClubType.PW,
    "pw": ClubType.PW,
    "gap wedge": ClubType.GW,
    "sand wedge": ClubType.SW,
    "lob wedge": ClubType.LW,
}

FIT_MIN_BALL_SPEED_MPH = 50.0
FIT_MIN_LAUNCH_DEG = 5.0
MIN_FAMILY_SHOTS = 15
RADIAL_GEOMETRY = {"ball_distance_ft": 2.0 / 0.3048, "ball_above_radar_ft": -0.15 / 0.3048}
FAMILIES = ("wedge", "iron", "wood")


@dataclass
class Row:
    """One TM shot: its outputs only."""

    session: str
    export: str
    shot: str
    club: ClubType
    ball_speed: Optional[float]
    club_speed: Optional[float]
    launch: Optional[float]
    launch_h: Optional[float]
    attack: Optional[float]
    path: Optional[float]
    spin: Optional[float]
    backspin: Optional[float]
    spin_measured: bool
    extra: dict = field(default_factory=dict)

    @property
    def loft(self) -> float:
        return get_club_physics(self.club).nominal_loft_deg

    @property
    def family(self) -> Optional[str]:
        return club_category(self.club)

    @property
    def in_fit_domain(self) -> bool:
        return (
            self.spin_measured
            and self.spin is not None
            and self.ball_speed is not None
            and self.ball_speed >= FIT_MIN_BALL_SPEED_MPH
            and self.launch is not None
            and self.launch >= FIT_MIN_LAUNCH_DEG
            and self.backspin is not None
            and self.backspin > 0
        )


def _number(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _rows(path: Path) -> tuple[list[str], list[list[str]]]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    while lines and lines[0].lstrip("﻿").startswith("sep="):
        lines = lines[1:]
    table = list(csv.reader(lines))
    return table[0], [row for row in table[2:] if any(cell.strip() for cell in row)]


def load_export(path: Path) -> list[Row]:
    """A TM normalized export or a SwingSync export, detected from the header."""
    header, body = _rows(path)
    out = []
    swingsync = "Backspin" in header
    for index, cells in enumerate(body, start=1):
        d = dict(zip(header, cells))
        club = CLUBS[(d.get("Club type") or d.get("Club") or "").strip().lower()]
        if swingsync:
            back, side = _number(d["Backspin"]), _number(d["Sidespin"])
            spin = math.hypot(back, side) if back is not None and side is not None else None
            launch = _number(d["Vertical Launch angle"])
            session = d["Date"][:10]
            row = Row(
                session=session,
                export=path.name,
                shot=d["Shot number"],
                club=club,
                ball_speed=_number(d["Ball speed"]),
                club_speed=_number(d["Club speed"]),
                launch=launch,
                launch_h=_number(d["Horizontal Launch Angle"]),
                attack=_number(d["Angle of attack"]),
                path=_number(d["Club path"]),
                spin=spin,
                backspin=back,
                spin_measured=True,
            )
        else:
            spin, axis = _number(d["Spin Rate"]), _number(d["Spin Axis"])
            month, day, year = d["Date"].split()[0].split("/")
            row = Row(
                session=f"{year}-{int(month):02d}-{int(day):02d}",
                export=path.name,
                shot=str(index),
                club=club,
                ball_speed=_number(d["Ball Speed"]),
                club_speed=_number(d["Club Speed"]),
                launch=_number(d["Launch Angle"]),
                launch_h=_number(d["Launch Direction"]),
                attack=_number(d["Attack Angle"]),
                path=_number(d["Club Path"]),
                spin=spin,
                backspin=(
                    spin * math.cos(math.radians(axis))
                    if spin is not None and axis is not None
                    else spin
                ),
                spin_measured=d.get("Spin Rate Type", "Measured") == "Measured",
            )
        out.append(row)
    return out


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------- tiers


@dataclass(frozen=True)
class TierSpec:
    name: str
    speed: str  # "club_speed" | "ball_speed"
    angle: str  # "loft" | "launch"
    exponent: Optional[float]  # fixed exponent, or None to fit it
    formula: str

    def speed_of(self, row: Row) -> Optional[float]:
        return row.club_speed if self.speed == "club_speed" else row.ball_speed

    def angle_of(self, row: Row) -> Optional[float]:
        return row.loft if self.angle == "loft" else row.launch

    def usable(self, row: Row) -> bool:
        return self.speed_of(row) is not None and self.angle_of(row) is not None


SPECS = {
    TIER_CLUB_SPEED: TierSpec(
        TIER_CLUB_SPEED, "club_speed", "loft", None, "{a:.1f} x club speed x sin(loft)^{b:.2f}"
    ),
    TIER_CLUB: TierSpec(
        TIER_CLUB, "ball_speed", "loft", None, "{a:.1f} x ball speed x sin(loft)^{b:.2f}"
    ),
    TIER_LAUNCH: TierSpec(
        TIER_LAUNCH,
        "ball_speed",
        "launch",
        SPIN_LA_EXPONENT,
        "{a:.1f} x ball speed x sin(launch)^{b:.1f}",
    ),
}


def fit(spec: TierSpec, rows: Sequence[Row]) -> tuple[float, float]:
    rows = [r for r in rows if r.in_fit_domain and spec.usable(r)]
    return fit_power_law(
        [spec.speed_of(r) for r in rows],
        [math.sin(math.radians(spec.angle_of(r))) for r in rows],
        [r.spin for r in rows],
        exponent=spec.exponent,
    )


def predict(spec: TierSpec, coefficients: tuple[float, float], row: Row) -> float:
    a, b = coefficients
    value = a * spec.speed_of(row) * math.sin(math.radians(spec.angle_of(row))) ** b
    return min(value, MAX_SPIN_RPM)


def runtime_ranges(spec: TierSpec, rows: Sequence[Row]):
    fitted = [r for r in rows if r.in_fit_domain and spec.usable(r)]
    speeds = [spec.speed_of(r) for r in fitted]
    speed_range = [math.floor(0.9 * min(speeds)), math.ceil(1.1 * max(speeds))]
    if spec.angle == "loft":
        lofts = [r.loft for r in fitted]
        angle_range = [min(lofts), max(lofts)]
    else:
        top = max(r.launch for r in fitted)
        angle_range = [FIT_MIN_LAUNCH_DEG, 5.0 * math.ceil(top / 5.0) + 5.0]
    return speed_range, angle_range


def evaluation_rows(spec: TierSpec, rows: Sequence[Row], angle_range) -> list[Row]:
    """Every TM-measured shot the tier would run on, mishits included."""
    out = []
    for r in rows:
        if not (r.spin_measured and r.spin and r.spin > 0 and spec.usable(r)):
            continue
        if spec.angle == "launch" and not angle_range[0] <= r.launch <= angle_range[1]:
            continue
        out.append(r)
    return out


def grouped_cv(spec, rows, evaluated, key: Callable[[Row], str]):
    """Held-out predictions: each group predicted by a fit that never saw it."""
    preds, folds = {}, []
    for group in sorted({key(r) for r in evaluated}):
        coefficients = fit(spec, [r for r in rows if key(r) != group])
        folds.append(
            {"held_out": group, "coefficient": coefficients[0], "exponent": coefficients[1]}
        )
        for r in evaluated:
            if key(r) == group:
                preds[id(r)] = predict(spec, coefficients, r)
    return preds, folds


# ---------------------------------------------------------------- comparators


def old_formula(row: Row) -> Optional[float]:
    return calculated_spin_rpm(row.ball_speed, row.launch)


def _face_and_loft(row: Row):
    if None in (row.launch, row.launch_h, row.path, row.attack, row.club_speed):
        return None
    return cf.face_and_loft_from_launch(
        club=row.club,
        launch_vertical_deg=row.launch,
        launch_horizontal_deg=row.launch_h,
        club_path_deg=row.path,
        attack_angle_deg=row.attack,
    )


def collision_forward(row: Row) -> Optional[float]:
    face_loft = _face_and_loft(row)
    if face_loft is None:
        return None
    impact = cf.forward(
        club=row.club,
        club_speed_mph=row.club_speed,
        attack_angle_deg=row.attack,
        club_path_deg=row.path,
        face_angle_deg=face_loft[0],
        dynamic_loft_deg=face_loft[1],
    )
    return impact.spin_rpm if impact else None


def penner_2001(row: Row) -> Optional[float]:
    face_loft = _face_and_loft(row)
    if face_loft is None:
        return None
    spin_loft = md.spin_loft_deg(face_loft[1], face_loft[0], row.attack, row.path)
    if spin_loft is None or not 0.0 <= spin_loft < 90.0:
        return None
    return penner_impact(row.club_speed * MPH_TO_M_S, spin_loft).spin_rpm


COMPARATORS = {
    "first fit 170 v sin(LA)^1.2": old_formula,
    "collision model (TM attack + path)": collision_forward,
    "Penner 2001 (TM attack + path)": penner_2001,
}


# ---------------------------------------------------------------- statistics


def stats(pairs: Iterable[tuple[float, float]]) -> dict:
    """(prediction, truth) pairs: n, MAE, median |error| %, mean and median error %, sd %."""
    pairs = [(p, t) for p, t in pairs if p is not None and t]
    if not pairs:
        return {"n": 0}
    pred = np.array([p for p, _ in pairs])
    truth = np.array([t for _, t in pairs])
    rel = 100.0 * (pred - truth) / truth
    return {
        "n": len(pairs),
        "mae_rpm": round(float(np.mean(np.abs(pred - truth))), 0),
        "median_abs_pct": round(float(np.median(np.abs(rel))), 1),
        "bias_pct": round(float(np.mean(rel)), 1),
        "median_pct": round(float(np.median(rel)), 1),
        "sd_pct": round(float(np.std(rel, ddof=1)), 1) if len(pairs) > 1 else None,
    }


def range_factors(pairs) -> list[float]:
    ratio = np.log([t / p for p, t in pairs])
    return [round(float(np.exp(np.percentile(ratio, q))), 3) for q in (16, 84)]


def radial_shortfall(rows: Sequence[Row]) -> float:
    shortfalls = [
        1.0 - radial_speed_factor(r.launch, r.ball_speed, **RADIAL_GEOMETRY)
        for r in rows
        if r.in_fit_domain
    ]
    return round(float(np.percentile(shortfalls, 95)), 3)


# ---------------------------------------------------------------- main


def load_all(paths: Sequence[Path]):
    rows, data, seen = [], [], {}
    for path in paths:
        if not path.is_file():
            print(f"[skip] {path.name}: not found beside the checkout")
            continue
        digest = sha256(path)
        if digest in seen:
            print(f"[skip] {path.name}: the same bytes as {seen[digest]} (read once)")
            continue
        seen[digest] = path.name
        export_rows = load_export(path)
        rows.extend(export_rows)
        in_repo = _REPO_ROOT in path.resolve().parents
        rel = (
            path.resolve().relative_to(_REPO_ROOT).as_posix()
            if in_repo
            else "../"
            + path.resolve().relative_to(BESIDE.resolve()).as_posix()
            + " (beside the checkout)"
        )
        data.append(
            {
                "name": path.name,
                "path": rel,
                "sha256": digest,
                "shots": len(export_rows),
                "fit_shots": sum(1 for r in export_rows if r.in_fit_domain),
                "in_repo": in_repo,
                "origin": ORIGIN.get(path.name, "a TM export"),
            }
        )
    return rows, data


def evaluate_tier(spec: TierSpec, rows: Sequence[Row]):
    coefficients = fit(spec, rows)
    speed_range, angle_range = runtime_ranges(spec, rows)
    evaluated = evaluation_rows(spec, rows, angle_range)
    by_session, folds = grouped_cv(spec, rows, evaluated, key=lambda r: r.session)
    by_club, _ = grouped_cv(spec, rows, evaluated, key=lambda r: r.club.value)
    families = (None,) + FAMILIES
    cv, loco, comparators, factors = {}, {}, {}, {}
    for family in families:
        members = [r for r in evaluated if family is None or r.family == family]
        name = family or "all"
        if not members:
            continue
        pairs = [(by_session[id(r)], r.spin) for r in members]
        cv[name] = stats(pairs)
        loco[name] = stats((by_club[id(r)], r.spin) for r in members)
        if family is None or len(members) >= MIN_FAMILY_SHOTS:
            factors[name] = range_factors(pairs)
        # the same held-out shots: those every comparator can predict
        common = [r for r in members if all(fn(r) is not None for fn in COMPARATORS.values())]
        comparators[name] = {
            "n": len(common),
            spec.name + " tier (held out by session)": stats(
                (by_session[id(r)], r.spin) for r in common
            ),
            **{label: stats((fn(r), r.spin) for r in common) for label, fn in COMPARATORS.items()},
        }
    entry = {
        "speed": spec.speed,
        "angle": spec.angle,
        "coefficient": round(coefficients[0], 3),
        "exponent": round(coefficients[1], 4),
        "formula": spec.formula.format(a=coefficients[0], b=coefficients[1]),
        "speed_range_mph": speed_range,
        "angle_range_deg": angle_range,
        "range_factors": factors,
        "cv": {
            **cv,
            "grouping": "leave one session out (one export = one session = one player)",
            "evaluated_on": "every TM-measured shot the tier runs on, mishits included",
            "folds": [
                {
                    **f,
                    "coefficient": round(f["coefficient"], 3),
                    "exponent": round(f["exponent"], 4),
                }
                for f in folds
            ],
            "leave_one_club_out": loco,
            "comparators_same_shots": comparators,
        },
    }
    return entry, by_session


def explore(rows: Sequence[Row]) -> None:
    """The launch's weight once the club is known: log spin on speed, launch and loft."""
    fitted = [r for r in rows if r.in_fit_domain]
    y = np.log([r.spin / r.ball_speed for r in fitted])
    x = np.column_stack(
        [
            np.ones(len(fitted)),
            np.log([math.sin(math.radians(r.launch)) for r in fitted]),
            np.log([math.sin(math.radians(r.loft)) for r in fitted]),
        ]
    )
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    print(
        f"log(spin/v) = {beta[0]:.3f} + {beta[1]:.3f} log sin(launch) + {beta[2]:.3f} log sin(loft)"
    )


def pinned(rows, tiers, by_session) -> None:
    """Rows for tests/test_spin_refit_tm_holdout.py: every 6th fitted shot per session."""
    for session in sorted({r.session for r in rows}):
        fitted = [r for r in rows if r.session == session and r.in_fit_domain]
        for r in fitted[::6]:
            print(
                f'    ("{r.session}", "{r.club.value}", {r.ball_speed:.1f}, {r.club_speed:.1f}, '
                f"{r.launch:.1f}, {r.spin:.0f}),"
            )


def print_table(tiers) -> None:
    for name, entry in tiers.items():
        print(f"\n## {name} tier: {entry['formula']}")
        print(
            "| family | n | MAE rpm | median abs % | median % | mean % | sd % | 68 % range "
            "| LOCO MAE |"
        )
        print("|---|---|---|---|---|---|---|---|---|")
        for family in ("all",) + FAMILIES:
            s = entry["cv"].get(family)
            if not s:
                continue
            f = entry["range_factors"].get(family)
            loco = entry["cv"]["leave_one_club_out"][family]
            print(
                f"| {family} | {s['n']} | {s['mae_rpm']:.0f} | {s['median_abs_pct']} | "
                f"{s['median_pct']:+} | {s['bias_pct']:+} | {s['sd_pct']} | {f or '-'} | "
                f"{loco['mae_rpm']:.0f} |"
            )
        print("\nsame held-out shots (those every comparator predicts):")
        for family, block in entry["cv"]["comparators_same_shots"].items():
            print(f"  {family} (n={block['n']})")
            for label, s in block.items():
                if label == "n" or not s.get("n"):
                    continue
                print(
                    f"    {label:44s} MAE {s['mae_rpm']:5.0f}  median|e| {s['median_abs_pct']:5.1f}%"
                    f"  median {s['median_pct']:+6.1f}%  mean {s['bias_pct']:+6.1f}%  sd {s['sd_pct']}%"
                )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("exports", nargs="*", type=Path)
    parser.add_argument("--no-defaults", action="store_true")
    parser.add_argument("--write", action="store_true", help=f"write {SPIN_REFIT_PATH.name}")
    parser.add_argument("--pinned", action="store_true")
    parser.add_argument("--explore", action="store_true")
    args = parser.parse_args(argv)
    paths = list(args.exports) + ([] if args.no_defaults else IN_REPO + EXTERNAL)
    rows, data = load_all(paths)
    print(f"{len(rows)} shots, {sum(r.in_fit_domain for r in rows)} in the fit domain")
    if args.explore:
        explore(rows)
    tiers, held_out = {}, {}
    for name in (TIER_CLUB_SPEED, TIER_CLUB, TIER_LAUNCH):
        tiers[name], held_out[name] = evaluate_tier(SPECS[name], rows)
    print_table(tiers)
    shortfall = radial_shortfall(rows)
    print(f"\nradial ball-speed shortfall, 95th percentile: {shortfall:.3f}")
    if args.pinned:
        pinned(rows, tiers, held_out)
    if args.write:
        record = {
            "schema": "openflight.spin_refit",
            "version": VERSION,
            "name": "spin refit v1",
            "form": "spin_rpm = coefficient x speed_mph x sin(angle)^exponent, "
            "least squares on log(spin / speed)",
            "source": (
                "fitted to TM exports' measured shots (club, ball speed, club speed, vertical "
                "launch in; total spin out), cross-validated leaving one session out; "
                "fit domain: TM-measured spin, ball speed >= 50 mph, vertical launch >= 5 deg, "
                "no topspin"
            ),
            "fitted_on": FITTED_ON,
            "script": "scripts/analysis/fit_spin_refit.py",
            "data": data,
            "radial_speed_shortfall": shortfall,
            "radial_speed_shortfall_basis": (
                "95th percentile of 1 - speed_correction.radial_speed_factor over the fitted "
                "shots, ball 2 m in front of the OPS and 0.15 m below it; the OPS radial ball "
                "speed is never corrected, only the range's top widened by it"
            ),
            "tiers": tiers,
        }
        SPIN_REFIT_PATH.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {SPIN_REFIT_PATH.relative_to(_REPO_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
