"""The broad rival search over the image box; no output is metric or confirmable.

It ranks every seed, refines up to eight candidates (two at a time in a fit
pool), and proposes a ball only when no distinct rival explains the box nearly
as well. A candidate's refinement may be reused instead of recomputed only when
ALL of these hold:

* identical camera/mode descriptor, image-box outline, frame shape/dtype and
  applied controls (exposure, gain) as when it was fitted;
* the same 240 px fit region and a candidate centre within 2 px;
* the region's current five-frame coarse picture matches the stored one within
  the watch limits (``CHANGE_FLOOR_DN`` after a median offset, noise test), and
  the current window is itself stationary there;
* the stored fit is younger than ``MAX_REUSE_AGE_S`` and the current window is
  strictly newer. Reuse never renews an entry, so every candidate is refitted
  from fresh pixels at least every ``MAX_REUSE_AGE_S``.
"""

# pylint: disable=no-member  # OpenCV's members are generated at import

from __future__ import annotations

import atexit
import math
import multiprocessing
import time
from concurrent.futures import (
    FIRST_COMPLETED,
    ProcessPoolExecutor,
    TimeoutError as FutureTimeout,
    wait,
)
from copy import deepcopy

import numpy as np

from openflight.camera import ball_model as reference, reference_ball_range as ranges
from openflight.camera.club_motion import DISK_SEARCH_WIDTH_PX, reference_ball_seeds
from openflight.camera.golf_ball_hint import controls_match
from openflight.camera.ground_patch import point_in_polygon
from openflight.camera.worker_scheduling import initialize_analysis_worker

from .outline_checks import qualify
from .rank import rank
from .sphere_fit import fit
from .stability import stable_geometry
from .watch import CHANGE_FLOOR_DN, CHANGE_NOISE_MULTIPLE, picture

FIT_WORKERS = 2
FIT_POOL_START_TIMEOUT_S = 120
MAX_REUSE_AGE_S = 2.0
CENTRE_TOLERANCE_PX = 2.0

_FIT_POOL: ProcessPoolExecutor | None = None
_CANCEL_EVENT = None
_REUSE_CACHE: dict = {}
_CLOCK = time.monotonic


def configure_cancellation(event) -> None:
    """The event that tells this worker's search to stop."""
    global _CANCEL_EVENT  # pylint: disable=global-statement
    _CANCEL_EVENT = event


def cancelled() -> bool:
    """Whether the current search has been told to stop."""
    return _CANCEL_EVENT is not None and _CANCEL_EVENT.is_set()


def parallel_fits(targets):
    """Refine the targets two at a time, yielding (index, item) as each completes."""
    if not targets or cancelled():
        return
    warm_fit_pool()
    pending = {
        _FIT_POOL.submit(refine_candidate, target): i for i, target in enumerate(targets[:2])
    }
    next_index = min(2, len(targets))
    try:
        while pending:
            if cancelled():
                break
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in sorted(done, key=lambda f: pending[f]):
                index = pending.pop(future)
                item = future.result()
                if cancelled():
                    return
                if next_index < len(targets):
                    pending[_FIT_POOL.submit(refine_candidate, targets[next_index])] = next_index
                    next_index += 1
                yield index, item
    finally:
        for future in pending:
            future.cancel()


def fit_boot() -> bool:
    """Keep OpenCV single-threaded in a fit worker."""
    import cv2  # pylint: disable=import-outside-toplevel

    cv2.setNumThreads(1)
    return True


def _initialize_fit_worker() -> None:
    initialize_analysis_worker()
    fit_boot()


def warm_fit_pool() -> bool:
    """Start this process's fit workers if they are not running."""
    global _FIT_POOL  # pylint: disable=global-statement
    if _FIT_POOL is None:
        _FIT_POOL = ProcessPoolExecutor(
            max_workers=FIT_WORKERS,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=_initialize_fit_worker,
        )
        warmed = [_FIT_POOL.submit(fit_boot) for _ in range(FIT_WORKERS)]
        try:
            ready = all(future.result(timeout=FIT_POOL_START_TIMEOUT_S) for future in warmed)
        except FutureTimeout as exc:
            raise RuntimeError(
                f"the fit workers did not start within {FIT_POOL_START_TIMEOUT_S} s"
            ) from exc
        if not ready:
            raise RuntimeError("the fit workers did not start")
    return True


def close_fit_pool() -> None:
    """End this process's fit workers, waiting for them."""
    global _FIT_POOL  # pylint: disable=global-statement
    if _FIT_POOL is not None:
        _FIT_POOL.shutdown(wait=True, cancel_futures=True)
        _FIT_POOL = None


atexit.register(close_fit_pool)


def seeds_for(row):
    """The window's median image, its noise and the box's ranked ball seeds."""
    patch = ranges.image_box_search(row["camera"], row["search"])
    bounds = patch.bounds_px(
        row["camera"].image_width_px,
        row["camera"].image_height_px,
        ranges.PATCH_SEED_MARGIN_PX + patch.diameter_px[1] / 2,
    )
    expected = np.geomspace(*patch.diameter_px, 12)
    preferred = np.geomspace(*row["search"].diameter_px, 12)
    expected = expected[(expected >= 16) & (expected <= 90)]
    preferred = preferred[(preferred >= 16) & (preferred <= 90)]
    if expected.size == 0 and preferred.size == 0:
        frames = row["frames"]
        return (
            np.median(frames, axis=0).astype(np.float32),
            float(np.median(np.std(frames, axis=0))),
            [],
        )
    return reference_ball_seeds(
        row["frames"],
        expected_diameters_px=expected,
        preferred_diameters_px=preferred,
        roi=bounds,
        seed_filter=ranges._patch_seed_filter(patch),  # pylint: disable=protected-access
    )


def _stationarity(item, frames, candidate, bounds):
    before, noise_before = picture(frames[:3], bounds, candidate.get("native_width_px"))
    after, noise_after = picture(frames[-3:], bounds, candidate.get("native_width_px"))
    delta = after - before
    change = float(np.max(np.abs(delta - np.median(delta))))
    stationary = bool(
        change <= CHANGE_FLOOR_DN
        and CHANGE_NOISE_MULTIPLE * max(noise_before, noise_after) <= CHANGE_FLOOR_DN
    )
    item["stationarity"] = {
        "passes": stationary,
        "max_change_dn": change,
        "noise_dn": max(noise_before, noise_after),
        "bounds": list(bounds),
    }
    return stationary


def refine_candidate(arguments):  # pylint: disable=too-many-locals
    """Fit one ranked candidate in a 120 px crop and qualify it; never geometry alone."""
    image, frames, index, candidate, first_warm = arguments
    old_left, old_top = candidate["origin"]
    parameters = candidate["parameters"].copy()
    left = round(old_left + parameters[0]) - 60
    top = round(old_top + parameters[1]) - 60
    left = min(max(left, 0), image.shape[1] - 120)
    top = min(max(top, 0), image.shape[0] - 120)
    parameters[0] += old_left - left
    parameters[1] += old_top - top
    crop = image[top : top + 120, left : left + 120].astype(np.uint8)
    starts = [parameters] if first_warm and index == 0 else None
    refined = qualify(crop, fit(crop, starts=starts))
    retry = None
    # A displaced first fit can put ball pixels into the side strips used to
    # estimate background. Retry once from the same current raw image with a
    # centred crop and the original three cold starts. No gate is relaxed.
    d = refined["best_diagnostic"]
    if (
        refined["circle"] is None
        and refined["raw_circle"] is not None
        and d["quality"] >= reference.MIN_DIFFUSE_NOISE_MULTIPLE
        and d["misfit_dn"] <= reference.MAX_MISFIT_FRACTION * d["diffuse_dn"]
    ):
        cx, cy, _ = refined["raw_circle"]
        rx = min(max(round(left + cx) - 60, 0), image.shape[1] - 120)
        ry = min(max(round(top + cy) - 60, 0), image.shape[0] - 120)
        if (rx, ry) != (left, top):
            retry_crop = image[ry : ry + 120, rx : rx + 120].astype(np.uint8)
            retried = qualify(retry_crop, fit(retry_crop))
            retry = {"origin": [rx, ry], "fit": retried, "accepted": retried["circle"] is not None}
            if retried["circle"] is not None:
                left, top, crop, refined = rx, ry, retry_crop, retried
    item = {"coarse": candidate, "refined": refined, "origin": [left, top], "recenter_retry": retry}
    azimuth = math.degrees(refined["best_diagnostic"]["azimuth_rad"]) % 360
    low, high = reference.LIGHT_AZIMUTH_DEG
    if azimuth < low:
        azimuth += 360
    item["passes_existing_light_direction"] = low <= azimuth <= high
    d = refined["best_diagnostic"]
    item["passes_existing_misfit_fraction"] = (
        d["misfit_dn"] <= reference.MAX_MISFIT_FRACTION * d["diffuse_dn"]
    )
    # Model discrepancy also limits preview evidence, rather than treating it as
    # perfectly explained sensor noise. This is an additional research guard.
    item["passes_preview_residual_snr"] = d["quality"] >= reference.MIN_DIFFUSE_NOISE_MULTIPLE
    if (
        refined["circle"]
        and item["passes_existing_light_direction"]
        and item["passes_existing_misfit_fraction"]
        and item["passes_preview_residual_snr"]
    ):
        cx, cy, r = refined["circle"]
        cx += left
        cy += top
        bounds = (
            max(0, int(cx - 1.5 * r)),
            max(0, int(cy - 1.5 * r)),
            min(image.shape[1], math.ceil(cx + 1.5 * r)),
            min(image.shape[0], math.ceil(cy + 1.5 * r)),
        )
        stationary = _stationarity(item, frames, candidate, bounds)
        if not stationary:
            endpoint_check = stable_geometry(frames, (left, top), refined)
            item["endpoint_stationarity"] = endpoint_check
            stationary = endpoint_check["passes"]
        item["circle"] = [cx, cy, r] if stationary else None
    else:
        item["circle"] = None
    return item


def candidate_evidence(item):
    """How much better than its background a candidate's sphere explains it."""
    # Heuristic research combination: retain residual SNR, downweight a sphere
    # explaining little beyond the contextual background. Not a probability.
    refined = item["refined"]
    return refined["best_diagnostic"]["quality"] * refined["background_improvement"]


def _context(frames, camera, patch, controls) -> str:
    outline = tuple(map(tuple, np.round(np.asarray(patch.outline_px, float), 3)))
    return repr(
        (
            camera.image_width_px,
            camera.image_height_px,
            getattr(camera, "focal_size_px", None),
            outline,
            frames.shape,
            frames.dtype.str,
            tuple(map(tuple, controls)),
        )
    )


def _changed(a, b) -> float:
    delta = a - b
    return float(np.max(np.abs(delta - np.median(delta))))


def _lookup(key, centre, frames, context, now):
    """A stored refinement this region's current pixels still verify, else None."""
    entry = _REUSE_CACHE.get(key)
    if entry is None or entry["context"] != context:
        return None
    if not 0 < now - entry["at"] < MAX_REUSE_AGE_S:
        return None
    if (
        math.hypot(centre[0] - entry["centre"][0], centre[1] - entry["centre"][1])
        > CENTRE_TOLERANCE_PX
    ):
        return None
    whole = (0, 0, frames.shape[2], frames.shape[1])
    current, noise = picture(frames, whole, entry["native_width_px"])
    if CHANGE_NOISE_MULTIPLE * max(noise, entry["noise"]) > CHANGE_FLOOR_DN:
        return None
    change = _changed(current, entry["picture"])
    if change > CHANGE_FLOOR_DN:
        return None
    early, early_noise = picture(frames[:3], whole, entry["native_width_px"])
    late, late_noise = picture(frames[-3:], whole, entry["native_width_px"])
    if (
        _changed(late, early) > CHANGE_FLOOR_DN
        or CHANGE_NOISE_MULTIPLE * max(early_noise, late_noise) > CHANGE_FLOOR_DN
    ):
        return None
    item = deepcopy(entry["item"])
    item["reused_fit"] = {
        "fitted_at": entry["at"],
        "age_s": now - entry["at"],
        "max_change_dn": change,
        "noise_dn": max(noise, entry["noise"]),
        "limit_dn": CHANGE_FLOOR_DN,
    }
    return item


def _store(key, centre, frames, context, now, item, native_width):
    whole = (0, 0, frames.shape[2], frames.shape[1])
    coarse, noise = picture(frames, whole, native_width)
    _REUSE_CACHE[key] = {
        "context": context,
        "at": now,
        "centre": centre,
        "picture": coarse,
        "noise": noise,
        "native_width_px": native_width,
        "item": deepcopy(item),
    }


def _select(result, max_refinements):
    """The best distinct candidate, unless a rival explains the box nearly as well."""
    result["pending"] = result["ranked"][max_refinements:]
    distinct = []
    for item in sorted(
        result["fitted"], key=lambda p: (-candidate_evidence(p), p["candidate_index"])
    ):
        if item["circle"] and not any(
            math.hypot(item["circle"][0] - p["circle"][0], item["circle"][1] - p["circle"][1])
            <= max(2, 0.35 * min(item["circle"][2] * 2, p["circle"][2] * 2))
            for p in distinct
        ):
            distinct.append(item)
    ratio = (
        distinct[1]["refined"]["best_diagnostic"]["quality"]
        / distinct[0]["refined"]["best_diagnostic"]["quality"]
        if len(distinct) > 1
        else None
    )
    result["second_to_best_quality"] = ratio
    # Compare explanatory power against the same contextual-background null,
    # rather than rewarding a very bright but poorly explained rug highlight.
    # This is a new research ranking, not a calibrated class probability.
    evidence_ratio = (
        candidate_evidence(distinct[1]) / candidate_evidence(distinct[0])
        if len(distinct) > 1
        else None
    )
    result["second_to_best_evidence"] = evidence_ratio
    result["ambiguity_score"] = "residual_snr_times_relative_contextual_robust_cost_reduction"
    if len(distinct) > 1 and evidence_ratio >= ranges.PATCH_AMBIGUITY_QUALITY_RATIO:
        result["selected"] = None
        result["status"] = "ambiguous"
    else:
        if distinct:
            cx, cy, r = distinct[0]["circle"]
            result["selected"] = {"x_px": cx, "y_px": cy, "radius_px": r}
            d = distinct[0]["refined"]["best_diagnostic"]
            result["selected_parameters"] = [cx, cy, r, d["azimuth_rad"], d["tilt_rad"]]
        result["status"] = "proposed_unqualified" if result["selected"] else "unavailable"


def search(  # pylint: disable=too-many-arguments,too-many-locals,too-many-statements
    frames,
    camera,
    patch,
    controls,
    *,
    first_warm=True,
    max_refinements=8,
    parallel=True,
    event_sink=None,
):
    """The box's ball, or ambiguity, from every seed; an early proposal goes to ``event_sink``."""
    started = time.perf_counter()
    now = _CLOCK()
    result = {
        "selected": None,
        "status": "unavailable",
        "metric_eligible": False,
        "confirmable": False,
        "purpose": "research_image_search",
        "ranked": [],
        "fitted": [],
        "pending": [],
        "fit_reuse": "pixel_verified",
    }
    if cancelled():
        result["status"] = "cancelled"
        return result
    if len(frames) < 5 or not controls_match(frames, controls):
        result["reason"] = "unknown or changing controls"
        return result
    if (
        frames.dtype != np.uint8
        or frames.ndim != 3
        or frames.shape[1:] != (camera.image_height_px, camera.image_width_px)
    ):
        result["reason"] = "frame mode mismatch"
        return result
    context = _context(frames, camera, patch, controls)
    for key in [
        k
        for k, v in _REUSE_CACHE.items()
        if v["context"] != context or not 0 <= now - v["at"] < MAX_REUSE_AGE_S
    ]:
        del _REUSE_CACHE[key]
    image, _noise, seeds = seeds_for({"frames": frames, "camera": camera, "search": patch})
    for score, x, y, radius in seeds:
        left, top = round(x) - 60, round(y) - 60
        if left < 0 or top < 0 or left + 120 > image.shape[1] or top + 120 > image.shape[0]:
            continue
        candidate = rank(image[top : top + 120, left : left + 120].astype(np.uint8))
        candidate.update(origin=[left, top], seed=[score, x, y, radius])
        result["ranked"].append(candidate)
    result["ranked"].sort(key=lambda p: p["improvement"], reverse=True)
    result["rank_ms"] = (time.perf_counter() - started) * 1000
    targets, origins, keys, reused = [], [], [], {}
    factor = max(1, round(image.shape[1] / DISK_SEARCH_WIDTH_PX))
    for i, c in enumerate(result["ranked"][:max_refinements]):
        x = round(c["origin"][0] + c["parameters"][0])
        y = round(c["origin"][1] + c["parameters"][1])
        ox = min(max(x - 120, 0), image.shape[1] - 240)
        oy = min(max(y - 120, 0), image.shape[0] - 240)
        ox, oy = ox // factor * factor, oy // factor * factor
        local = dict(
            c, origin=[c["origin"][0] - ox, c["origin"][1] - oy], native_width_px=image.shape[1]
        )
        region = frames[:, oy : oy + 240, ox : ox + 240].copy()
        centre = (c["origin"][0] + c["parameters"][0], c["origin"][1] + c["parameters"][1])
        key = (ox, oy, i == 0 and first_warm)
        hit = _lookup(key, centre, region, context, now)
        if hit is not None:
            reused[i] = hit
        targets.append((image[oy : oy + 240, ox : ox + 240].copy(), region, i, local, first_warm))
        origins.append((ox, oy, c))
        keys.append((key, centre))
    pending = [t for t in targets if t[2] not in reused]

    def fitted_items():
        for index in sorted(reused):
            yield index, reused[index]
        if parallel:
            fresh = ((pending[i][2], item) for i, item in parallel_fits(pending))
        else:
            fresh = ((t[2], refine_candidate(t)) for t in pending)
        for index, item in fresh:
            key, centre = keys[index]
            _store(key, centre, targets[index][1], context, now, item, image.shape[1])
            yield index, item

    fitted = fitted_items()
    completed = {}
    prefix = 0
    for index, item in fitted:
        if cancelled():
            break
        ox, oy, c = origins[index]
        item["coarse"] = c
        item["origin"] = [item["origin"][0] + ox, item["origin"][1] + oy]
        if item["circle"]:
            item["circle"] = [item["circle"][0] + ox, item["circle"][1] + oy, item["circle"][2]]
            item["inside_image_box"] = bool(point_in_polygon(*item["circle"][:2], patch.outline_px))
            if not item["inside_image_box"]:
                item["circle"] = None
        if item.get("stationarity"):
            a, b, cx, dy = item["stationarity"]["bounds"]
            item["stationarity"]["bounds"] = [a + ox, b + oy, cx + ox, dy + oy]
        item["candidate_index"] = index
        result["fitted"].append(item)
        completed[index] = item
        while prefix in completed:
            candidate = completed[prefix]
            prefix += 1
            if candidate["circle"] and result["selected"] is None:
                result["first_proposal_ms"] = (time.perf_counter() - started) * 1000
                result["selected"] = {
                    "x_px": candidate["circle"][0],
                    "y_px": candidate["circle"][1],
                    "radius_px": candidate["circle"][2],
                }
                d = candidate["refined"]["best_diagnostic"]
                result["selected_parameters"] = [
                    *candidate["circle"],
                    d["azimuth_rad"],
                    d["tilt_rad"],
                ]
                if event_sink is not None:
                    event_sink.send(
                        {
                            "status": "proposal_pending_rivals",
                            "selected": result["selected"],
                            "metric_eligible": False,
                            "confirmable": False,
                            "rivals_pending": True,
                            "fit_parameters": result["selected_parameters"],
                            "first_proposal_ms": result["first_proposal_ms"],
                        }
                    )
    if cancelled():
        fitted.close()
        result.update(
            status="cancelled",
            selected=None,
            pending=[c for i, c in enumerate(result["ranked"]) if i not in completed],
            total_ms=(time.perf_counter() - started) * 1000,
        )
        return result
    _select(result, max_refinements)
    result["reused_candidates"] = sorted(reused)
    result["total_ms"] = (time.perf_counter() - started) * 1000
    return result
