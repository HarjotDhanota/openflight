"""Refit the flight model's aerodynamic constants to TM4 exports, and evaluate it.

E8a. TrackMan 4 reports carry, apex, landing angle and hang time normalized to a
standard reference (77 °F, sea level, premium ball). This script flies every shot
from TM4's *own* launch numbers (ball speed, vertical and horizontal launch, total
spin, spin axis) through OpenFlight's flight model at the normalized reference
density, and fits the seven constants of Ferguson, McNally & McPhee's (2022) form,

    Eq. (2)  Cd  = a + b Sp + c Sp^2
    Eq. (3)  Cl  = d + e Sp + f Sp^2          (clamped at >= 0)
    Eq. (4)  C_M = g Sp                       (g held to their 0.010-0.015)

by least squares on all four outputs at once, each residual divided by its scale
(carry 1 yd, apex 1 ft, landing angle 0.5 deg, hang time 0.1 s). It then reports
held-out error for the fit and for the published Ferguson constants on the same
splits: leave-one-source-out (when there are two or more exports), K-fold over
the pooled shots, optionally leave-one-shot-out, and the published TrackMan 2023
Tour averages as an out-of-range sanity check.

One command, any number of exports, either format (TrackMan's "Normalized" CSV or
a SwingSync export; detected from the header)::

    uv run python scripts/analysis/fit_flight_model.py
    uv run python scripts/analysis/fit_flight_model.py more/driver.csv more/woods.csv
    uv run python scripts/analysis/fit_flight_model.py --loo --plots --output-dir out/

With no arguments it uses the exports flight-fit-v1 was fitted to: the in-repo
``session_logs/OpenFlight-Test.Normalized.csv`` and, when present beside the
checkout, ``../tm-exports/tm4-public-swingsync-2026-10-12.csv`` (someone else's
public data: read from there, never copied into the repo). A missing external
export is skipped with a message, never an error. ``--no-defaults`` uses only the
exports named on the command line.

The fit's domain is decided on inputs alone, before any residual is seen: ball
speed >= 50 mph, vertical launch >= 5 deg, no topspin. Mishits outside it (tops,
grounders) are flown and reported separately so they neither steer the fit nor
flatter its RMSE.

The script does not edit ``ballistics.py``: it prints the constants and the
metrics block for ``FLIGHT_FIT_V1`` so the change can be reviewed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import least_squares

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (_REPO_ROOT / "src", _REPO_ROOT / "scripts" / "analysis"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from validate_ballistics import (  # noqa: E402
    TMShot,
    _normalize_club,
    load_tm_export,
)

from openflight import ballistics as bl  # noqa: E402
from openflight.ballistics import (  # noqa: E402
    FERGUSON_2022,
    SPIN_DECAY_G_BOUNDS,
    AeroModel,
    LaunchConditions,
    simulate,
)
from openflight.flight import AIR_DENSITY_NORMALIZED  # noqa: E402

IN_REPO_EXPORT = _REPO_ROOT / "session_logs" / "OpenFlight-Test.Normalized.csv"
EXTERNAL_EXPORT = _REPO_ROOT.parent / "tm-exports" / "tm4-public-swingsync-2026-10-12.csv"
DEFAULT_EXPORTS = (IN_REPO_EXPORT, EXTERNAL_EXPORT)

OUTPUTS = ("carry", "apex", "landing_angle", "hang_time")
UNITS = ("yd", "ft", "deg", "s")
# 1-sigma scales the residuals are divided by: one yard, one foot, half a degree,
# a tenth of a second, so each output weighs about what its display precision does.
SCALES = np.array([1.0, 1.0, 0.5, 0.1])

# The fit integrates at 5 ms: against the production 2 ms step it moves carry by
# 1e-4 yd, apex by 6e-5 ft and landing angle by 6e-5 deg (measured on the 60
# in-domain shots). Every evaluation flies the production step.
FIT_DT_SECONDS = 0.005

# Inputs-only domain of the fit (no residual is looked at to decide it).
MIN_BALL_SPEED_MPH = 50.0
MIN_LAUNCH_DEG = 5.0

# TrackMan's published 2023 PGA and LPGA Tour averages ("Trackman Tour Averages",
# trackman.com/blog/golf/introducing-updated-tour-averages, 2 May 2024; tables at
# trackman.media/tour-averages). (tour, club, ball speed mph, launch deg, spin rpm,
# max height yd, land angle deg, carry yd). TrackMan does not say these are
# normalized, so they are a plausibility check only, never a fit target.
TOUR_AVERAGES_2023 = (
    ("PGA", "driver", 171.0, 10.4, 2545.0, 35.0, 39.0, 282.0),
    ("PGA", "7-iron", 123.0, 16.1, 7124.0, 34.0, 51.0, 176.0),
    ("PGA", "pw", 104.0, 23.7, 9316.0, 32.0, 52.0, 142.0),
    ("LPGA", "driver", 143.0, 12.6, 2506.0, 26.0, 36.0, 223.0),
    ("LPGA", "7-iron", 106.0, 18.5, 6630.0, 26.0, 47.0, 143.0),
    ("LPGA", "pw", 88.0, 25.2, 8465.0, 27.0, 48.0, 111.0),
)

SP_TABLE = (0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.60, 0.75)


# ---------------------------------------------------------------------------
# Shots
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FitShot:
    """One TM4 shot: its launch inputs and TM4's four normalized flight outputs."""

    source: str  # export file name
    club: str
    ball_speed_mph: float
    launch_v_deg: float
    launch_h_deg: float
    spin_rpm: float
    spin_axis_deg: float
    truth: Tuple[float, float, float, float]  # carry yd, apex ft, landing deg, hang s
    in_domain: bool
    domain_reason: str


def domain_reason(shot: TMShot) -> str:
    """'' when the shot is inside the fit's domain, else why not (inputs only)."""
    if shot.ball_speed_mph < MIN_BALL_SPEED_MPH:
        return f"ball speed under {MIN_BALL_SPEED_MPH:g} mph"
    if shot.launch_v_deg < MIN_LAUNCH_DEG:
        return f"launch under {MIN_LAUNCH_DEG:g} deg"
    if shot.backspin_rpm is not None and shot.backspin_rpm <= 0:
        return "topspin"
    return ""


def to_fit_shots(tm_shots: Iterable[TMShot], source: str) -> List[FitShot]:
    """FitShots from loaded TM4 rows; rows missing any input or output are dropped."""
    out: List[FitShot] = []
    for s in tm_shots:
        values = (
            s.ball_speed_mph,
            s.launch_v_deg,
            s.spin_rpm,
            s.spin_axis_deg,
            s.carry_yards,
            s.apex_feet,
            s.landing_angle_deg,
            s.hang_time_s,
        )
        if any(v is None or not math.isfinite(v) for v in values):
            continue
        reason = domain_reason(s)
        out.append(
            FitShot(
                source=source,
                club=_normalize_club(s.club_raw),
                ball_speed_mph=s.ball_speed_mph,
                launch_v_deg=s.launch_v_deg,
                launch_h_deg=s.launch_h_deg or 0.0,
                spin_rpm=s.spin_rpm,
                spin_axis_deg=s.spin_axis_deg,
                truth=(s.carry_yards, s.apex_feet, s.landing_angle_deg, s.hang_time_s),
                in_domain=not reason,
                domain_reason=reason,
            )
        )
    return out


def sha256_of(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass(frozen=True)
class LoadedExport:
    path: Path
    sha256: str
    rows: int
    shots: List[FitShot]


def load_exports(paths: Sequence[Path]) -> List[LoadedExport]:
    """Load each export (format detected from its header); absent files are skipped."""
    out: List[LoadedExport] = []
    for path in paths:
        path = Path(path)
        if not path.exists():
            print(f"[skip] export not found: {path}")
            continue
        rows = load_tm_export(path)
        out.append(LoadedExport(path, sha256_of(path), len(rows), to_fit_shots(rows, path.name)))
    return out


def truth_matrix(shots: Sequence[FitShot]) -> np.ndarray:
    return np.array([s.truth for s in shots], dtype=float).T


# ---------------------------------------------------------------------------
# The flight model, vectorized over shots
# ---------------------------------------------------------------------------


def simulate_batch(
    shots: Sequence[FitShot],
    model: AeroModel,
    air_density: float = AIR_DENSITY_NORMALIZED,
    dt: float = bl.DT_SECONDS,
) -> np.ndarray:
    """(4, n) carry yd, apex ft, landing deg, hang s for every shot at once.

    The same equations, integrator, frames and landing interpolation as
    ``ballistics.simulate`` (checked shot by shot in
    tests/test_fit_flight_model.py), on numpy arrays so a fit takes seconds.
    """
    n = len(shots)
    r, area, mass = bl.BALL_RADIUS_M, bl.BALL_AREA_M2, bl.BALL_MASS_KG
    inertia = 0.4 * mass * r * r
    v0 = np.array([s.ball_speed_mph for s in shots]) * bl.MPH_TO_MPS
    la = np.radians([s.launch_v_deg for s in shots])
    lh = np.radians([s.launch_h_deg for s in shots])
    axis_deg = np.radians([s.spin_axis_deg for s in shots])
    state = np.zeros((7, n))
    state[3] = v0 * np.cos(la) * np.cos(lh)
    state[4] = v0 * np.cos(la) * np.sin(lh)
    state[5] = v0 * np.sin(la)
    state[6] = np.array([s.spin_rpm for s in shots]) * 2 * np.pi / 60.0
    c, s_ = np.cos(axis_deg), np.sin(axis_deg)
    axis = np.stack(
        [
            c * np.sin(lh) - s_ * np.sin(la) * np.cos(lh),
            -c * np.cos(lh) - s_ * np.sin(la) * np.sin(lh),
            s_ * np.cos(la),
        ]
    )
    cd_coeffs = np.asarray(model.cd_poly[::-1], dtype=float)
    cl_coeffs = np.asarray(model.cl_poly[::-1], dtype=float)
    descent = bool(model.descent_drag or model.descent_lift)
    spin_k = model.cm_per_sp * 0.5 * air_density * area * 2.0 * r * r / inertia

    def derivatives(s: np.ndarray) -> np.ndarray:
        vx, vy, vz, om = s[3], s[4], s[5], s[6]
        v = np.sqrt(vx * vx + vy * vy + vz * vz)
        sp = r * om / v
        spc = np.minimum(sp, model.sp_fit_max)
        cd = np.polyval(cd_coeffs, spc)
        cl = np.where(sp > 0, np.maximum(0.0, np.polyval(cl_coeffs, spc)), 0.0)
        if descent:
            x = np.maximum(-vz / v, 0.0) ** model.descent_power
            cd = cd * np.exp(-model.descent_drag * x)
            cl = cl * np.exp(-model.descent_lift * x)
        q = 0.5 * air_density * v * v * area / mass
        cx = axis[1] * vz - axis[2] * vy
        cy = axis[2] * vx - axis[0] * vz
        cz = axis[0] * vy - axis[1] * vx
        cmag = np.sqrt(cx * cx + cy * cy + cz * cz)
        lift = np.where(cmag > 1e-6, cl * q / np.where(cmag > 1e-6, cmag, 1.0), 0.0)
        out = np.empty_like(s)
        out[0], out[1], out[2] = vx, vy, vz
        out[3] = -cd * q * vx / v + lift * cx
        out[4] = -cd * q * vy / v + lift * cy
        out[5] = -cd * q * vz / v + lift * cz - bl.GRAVITY
        # spin_acceleration: -C_M (1/2) rho v^2 A d / I with C_M = g r omega / v
        out[6] = -spin_k * om * v
        return out

    alive = np.ones(n, dtype=bool)
    result = np.full((4, n), np.nan)
    max_z = np.zeros(n)
    t = 0.0
    while t < bl.MAX_FLIGHT_SECONDS and alive.any():
        k1 = derivatives(state)
        k2 = derivatives(state + 0.5 * dt * k1)
        k3 = derivatives(state + 0.5 * dt * k2)
        k4 = derivatives(state + dt * k3)
        new = state + (dt / 6.0) * (k1 + 2 * k2 + 2 * k3 + k4)
        t += dt
        max_z = np.where(alive, np.maximum(max_z, new[2]), max_z)
        hit = alive & (new[2] <= 0.0) & (t > dt)
        if hit.any():
            prev_z, z = state[2, hit], new[2, hit]
            denom = prev_z - z
            frac = np.where(np.abs(denom) > 1e-9, prev_z / np.where(denom == 0, 1, denom), 0.0)
            final = state[:, hit] + frac * (new[:, hit] - state[:, hit])
            horizontal = np.hypot(final[3], final[4])
            result[0, hit] = np.hypot(final[0], final[1]) * bl.M_TO_YD
            result[1, hit] = max_z[hit] * bl.M_TO_YD * 3.0
            result[2, hit] = np.degrees(np.arctan2(-final[5], horizontal))
            result[3, hit] = (t - dt) + frac * dt
            alive &= ~hit
        state = np.where(alive, new, state)
    return result


def simulate_production(
    shots: Sequence[FitShot], model: AeroModel, air_density: float = AIR_DENSITY_NORMALIZED
) -> np.ndarray:
    """The same (4, n) array from ``ballistics.simulate``, one shot at a time."""
    out = np.empty((4, len(shots)))
    for i, s in enumerate(shots):
        traj = simulate(
            LaunchConditions(
                s.ball_speed_mph,
                s.launch_v_deg,
                s.launch_h_deg,
                s.spin_rpm,
                s.spin_axis_deg,
                "measured",
            ),
            air_density=air_density,
            aero=model,
        )
        out[:, i] = (
            traj.carry_yards,
            traj.apex_yards * 3.0,
            traj.landing_angle_deg,
            traj.flight_time_s,
        )
    return out


# ---------------------------------------------------------------------------
# Fit
# ---------------------------------------------------------------------------


def model_from_params(p: Sequence[float], version: str = "refit", label: str = "") -> AeroModel:
    return AeroModel(
        version=version,
        label=label or version,
        cd_poly=(float(p[0]), float(p[1]), float(p[2])),
        cl_poly=(float(p[3]), float(p[4]), float(p[5])),
        cm_per_sp=float(p[6]),
        sp_fit_max=FERGUSON_2022.sp_fit_max,
    )


def params_of(model: AeroModel) -> np.ndarray:
    return np.array([*model.cd_poly, *model.cl_poly, model.cm_per_sp], dtype=float)


def standardized_residuals(pred: np.ndarray, truth: np.ndarray) -> np.ndarray:
    r = (pred - truth) / SCALES[:, None]
    # a shot that never lands inside the time cap is an extreme miss, not a NaN
    return np.nan_to_num(r, nan=1e3)


def fit_aero(
    shots: Sequence[FitShot],
    start: AeroModel = FERGUSON_2022,
    air_density: float = AIR_DENSITY_NORMALIZED,
) -> AeroModel:
    """Least-squares fit of Ferguson's seven constants on the in-domain shots."""
    fit_set = [s for s in shots if s.in_domain]
    if len(fit_set) < 8:
        raise ValueError(f"only {len(fit_set)} in-domain shots: too few to fit 7 constants")
    truth = truth_matrix(fit_set)
    low = [-2.0, -10.0, -10.0, -2.0, -10.0, -10.0, SPIN_DECAY_G_BOUNDS[0]]
    high = [2.0, 10.0, 10.0, 2.0, 10.0, 10.0, SPIN_DECAY_G_BOUNDS[1]]
    p0 = np.clip(params_of(start), low, high)

    def residuals(p):
        pred = simulate_batch(fit_set, model_from_params(p), air_density, FIT_DT_SECONDS)
        return standardized_residuals(pred, truth).ravel()

    sol = least_squares(
        residuals,
        p0,
        bounds=(low, high),
        diff_step=1e-3,
        x_scale="jac",
        max_nfev=200,
    )
    model = model_from_params(sol.x)
    problems = physical_problems(model)
    if problems:
        raise ValueError("fitted coefficients are unphysical: " + "; ".join(problems))
    return model


def physical_problems(model: AeroModel) -> List[str]:
    """Cd > 0 and Cl >= 0 at every Sp, Cl > 0 over 0.05-0.45 (the stated domain)."""
    problems = []
    for i in range(0, 301):
        sp = i / 100.0
        if model.cd(sp) <= 0:
            problems.append(f"Cd({sp:.2f}) = {model.cd(sp):.4f}")
        if model.cl(sp) < 0:
            problems.append(f"Cl({sp:.2f}) = {model.cl(sp):.4f}")
    for i in range(5, 46):
        if model.cl(i / 100.0) <= 0:
            problems.append(f"Cl({i / 100.0:.2f}) not positive")
    return problems


def rounded(model: AeroModel, digits: int = 4) -> AeroModel:
    """The model with its constants rounded as they are written into ballistics.py."""
    return model_from_params([round(float(x), digits) for x in params_of(model)], model.version)


# ---------------------------------------------------------------------------
# Splits and statistics
# ---------------------------------------------------------------------------


def _fit_and_predict(shots: Sequence[FitShot], fold: Sequence[int]) -> np.ndarray:
    held = set(fold)
    model = rounded(fit_aero([s for j, s in enumerate(shots) if j not in held]))
    return simulate_batch([shots[j] for j in fold], model)


def predict_held_out(
    shots: Sequence[FitShot],
    folds: Sequence[Sequence[int]],
    label: str = "",
    jobs: int = 1,
) -> np.ndarray:
    """(4, n) predictions, each fold flown by a model fitted without it.

    ``jobs`` > 1 fits the folds in that many worker processes.
    """
    pred = np.full((4, len(shots)), np.nan)
    if jobs > 1 and len(folds) > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=min(jobs, len(folds))) as pool:
            futures = [pool.submit(_fit_and_predict, list(shots), list(f)) for f in folds]
            for i, (fold, fut) in enumerate(zip(folds, futures), start=1):
                pred[:, list(fold)] = fut.result()
                if label:
                    print(f"  {label} fold {i}/{len(folds)} done", flush=True)
        return pred
    for i, fold in enumerate(folds, start=1):
        pred[:, list(fold)] = _fit_and_predict(shots, fold)
        if label:
            print(f"  {label} fold {i}/{len(folds)} done", flush=True)
    return pred


def source_folds(shots: Sequence[FitShot]) -> List[List[int]]:
    sources = sorted({s.source for s in shots})
    return [[i for i, s in enumerate(shots) if s.source == src] for src in sources]


def k_folds(n: int, k: int, seed: int = 0) -> List[List[int]]:
    idx = np.random.default_rng(seed).permutation(n)
    return [sorted(int(i) for i in f) for f in np.array_split(idx, k) if len(f)]


def stats(deltas: np.ndarray) -> Dict[str, float]:
    d = np.asarray(deltas, dtype=float)
    if d.size == 0:
        return {"n": 0, "bias": math.nan, "rmse": math.nan, "max": math.nan}
    return {
        "n": int(d.size),
        "bias": float(d.mean()),
        "rmse": float(np.sqrt((d * d).mean())),
        "max": float(np.abs(d).max()),
    }


def groups(shots: Sequence[FitShot]) -> List[Tuple[str, np.ndarray]]:
    """(label, mask) per source x club, per source, and overall."""
    out = []
    sources = sorted({s.source for s in shots})
    for src in sources:
        for club in sorted({s.club for s in shots if s.source == src}):
            out.append(
                (f"{src} / {club}", np.array([s.source == src and s.club == club for s in shots])),
            )
    if len(sources) > 1:
        for src in sources:
            out.append((f"{src} / all", np.array([s.source == src for s in shots])))
    out.append(("ALL", np.ones(len(shots), dtype=bool)))
    return out


def table(title: str, shots: Sequence[FitShot], pred: np.ndarray) -> str:
    """Markdown table: bias / RMSE / max per output, per source x club."""
    delta = pred - truth_matrix(shots)
    head = (
        "| group | n | "
        + " | ".join(f"{o} ({u}) bias / rmse / max" for o, u in zip(OUTPUTS, UNITS))
        + " |"
    )
    lines = [f"#### {title}", "", head, "|" + "---|" * (2 + len(OUTPUTS))]
    for label, mask in groups(shots):
        cells = []
        for k in range(len(OUTPUTS)):
            st = stats(delta[k, mask])
            cells.append(f"{st['bias']:+.2f} / {st['rmse']:.2f} / {st['max']:.2f}")
        lines.append(f"| {label} | {int(mask.sum())} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def overall(shots: Sequence[FitShot], pred: np.ndarray) -> Dict[str, Dict[str, float]]:
    delta = pred - truth_matrix(shots)
    return {o: stats(delta[k]) for k, o in enumerate(OUTPUTS)}


def tour_table(models: Sequence[AeroModel]) -> str:
    lines = [
        "#### TrackMan 2023 Tour averages (published, not a fit target)",
        "",
        "| tour | club | published carry / max height (yd) / land | "
        + " | ".join(f"{m.version} carry / height / land" for m in models)
        + " |",
        "|---|---|---|" + "---|" * len(models),
    ]
    for tour, club, speed, launch, spin, height, land, carry in TOUR_AVERAGES_2023:
        cells = []
        for m in models:
            t = simulate(
                LaunchConditions(speed, launch, 0.0, spin, 0.0, "measured"),
                air_density=AIR_DENSITY_NORMALIZED,
                aero=m,
            )
            cells.append(f"{t.carry_yards:.1f} / {t.apex_yards:.1f} / {t.landing_angle_deg:.1f}")
        lines.append(
            f"| {tour} | {club} | {carry:g} / {height:g} / {land:g} | " + " | ".join(cells) + " |"
        )
    return "\n".join(lines) + "\n"


def coefficient_table(models: Sequence[AeroModel]) -> str:
    lines = [
        "#### Cd and Cl against Sp",
        "",
        "| Sp | " + " | ".join(f"{m.version} Cd | {m.version} Cl" for m in models) + " |",
        "|---|" + "---|---|" * len(models),
    ]
    for sp in SP_TABLE:
        cells = " | ".join(f"{m.cd(sp):.3f} | {m.cl(sp):.3f}" for m in models)
        lines.append(f"| {sp:.2f} | {cells} |")
    return "\n".join(lines) + "\n"


def write_plots(models: Sequence[AeroModel], out_dir: Path) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("[warn] matplotlib not available: no plots")
        return
    sp = np.linspace(0.0, 0.8, 161)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    for m in models:
        axes[0].plot(sp, [m.cd(x) for x in sp], label=m.version)
        axes[1].plot(sp, [m.cl(x) for x in sp], label=m.version)
    for ax, name in zip(axes, ("Cd", "Cl")):
        ax.axvspan(0.05, 0.45, color="grey", alpha=0.12, label="Sp 0.05-0.45")
        ax.set_xlabel("Sp = r omega / v")
        ax.set_ylabel(name)
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8)
    fig.tight_layout()
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_dir / "cd_cl_vs_sp.png", dpi=140)
    plt.close(fig)


def constants_snippet(model: AeroModel) -> str:
    return (
        f"    cd_poly=({model.cd_poly[0]:.4f}, {model.cd_poly[1]:.4f}, {model.cd_poly[2]:.4f}),\n"
        f"    cl_poly=({model.cl_poly[0]:.4f}, {model.cl_poly[1]:.4f}, {model.cl_poly[2]:.4f}),\n"
        f"    cm_per_sp={model.cm_per_sp:.4f},"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def run(
    exports: Sequence[Path],
    kfold: int = 10,
    loo: bool = False,
    seed: int = 0,
    output_dir: Optional[Path] = None,
    plots: bool = False,
    jobs: int = 1,
) -> Dict[str, object]:
    loaded = load_exports(exports)
    if not loaded:
        raise SystemExit("no exports loaded")
    shots = [s for e in loaded for s in e.shots]
    fit_set = [s for s in shots if s.in_domain]
    mishits = [s for s in shots if not s.in_domain]
    report: List[str] = ["## Flight-model refit", ""]
    for e in loaded:
        n_in = sum(s.in_domain for s in e.shots)
        report.append(
            f"- `{e.path.name}`: {e.rows} rows, {len(e.shots)} complete, {n_in} in domain, "
            f"sha256 {e.sha256}"
        )
    report.append("")

    fitted = rounded(fit_aero(fit_set))
    models = [FERGUSON_2022, fitted]
    pooled = {m.version: simulate_batch(fit_set, m) for m in models}
    report.append(
        table(
            "In-sample: ferguson-2022 (published constants)", fit_set, pooled[FERGUSON_2022.version]
        )
    )
    report.append(table("In-sample: the refit", fit_set, pooled[fitted.version]))
    metrics: Dict[str, object] = {
        "constants": params_of(fitted).tolist(),
        "in_sample": overall(fit_set, pooled[fitted.version]),
        "ferguson_2022": overall(fit_set, pooled[FERGUSON_2022.version]),
    }

    if len({s.source for s in fit_set}) > 1:
        print("leave-one-source-out ...", flush=True)
        pred = predict_held_out(fit_set, source_folds(fit_set), "LOSO", jobs)
        report.append(table("Leave-one-source-out: the refit, held out", fit_set, pred))
        metrics["leave_one_source_out"] = overall(fit_set, pred)
        for src in sorted({s.source for s in fit_set}):
            mask = np.array([s.source == src for s in fit_set])
            sub = [s for s in fit_set if s.source == src]
            metrics[f"leave_one_source_out[{src}]"] = overall(sub, pred[:, mask])

    if kfold and kfold > 1:
        print(f"{kfold}-fold ...", flush=True)
        pred = predict_held_out(fit_set, k_folds(len(fit_set), kfold, seed), f"{kfold}-fold", jobs)
        report.append(
            table(f"{kfold}-fold over the pooled shots: the refit, held out", fit_set, pred)
        )
        metrics[f"{kfold}_fold"] = overall(fit_set, pred)

    if loo:
        print("leave-one-out ...", flush=True)
        pred = predict_held_out(fit_set, [[i] for i in range(len(fit_set))], "LOO", jobs)
        report.append(table("Leave-one-shot-out: the refit, held out", fit_set, pred))
        metrics["leave_one_out"] = overall(fit_set, pred)

    if mishits:
        for m in models:
            report.append(
                table(
                    f"Mishits outside the domain ({m.version})", mishits, simulate_batch(mishits, m)
                )
            )
    report.append(tour_table(models))
    report.append(coefficient_table(models))
    report.append(
        "#### Constants for FLIGHT_FIT_V1\n\n```\n" + constants_snippet(fitted) + "\n```\n"
    )
    text = "\n".join(report)
    print(text)
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "report.md").write_text(text, encoding="utf-8")
        (output_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
        if plots:
            write_plots(models, output_dir)
    return metrics


def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "exports",
        nargs="*",
        type=Path,
        help="extra TM4 exports (TrackMan Normalized CSV or SwingSync)",
    )
    parser.add_argument(
        "--no-defaults", action="store_true", help="use only the exports named on the command line"
    )
    parser.add_argument("--kfold", type=int, default=10, help="K for K-fold (0 to skip)")
    parser.add_argument("--loo", action="store_true", help="also leave-one-shot-out (slow)")
    parser.add_argument("--seed", type=int, default=0, help="K-fold shuffle seed")
    parser.add_argument("--output-dir", type=Path, help="write report.md, metrics.json")
    parser.add_argument("--plots", action="store_true", help="also write cd_cl_vs_sp.png")
    parser.add_argument(
        "--jobs",
        type=int,
        default=max(1, (os.cpu_count() or 2) - 1),
        help="worker processes for the held-out fits",
    )
    args = parser.parse_args(argv)
    exports = list(args.exports) if args.no_defaults else [*DEFAULT_EXPORTS, *args.exports]
    run(exports, args.kfold, args.loo, args.seed, args.output_dir, args.plots, args.jobs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
