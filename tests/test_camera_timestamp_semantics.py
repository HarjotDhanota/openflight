"""Known-answer tests for camera timestamp semantics (synthetic clips; one optional recorded).

The synthetic clips mirror what the kiosk writes (``capture_runtime._save_capture``): the
capture-mode context id is the sha256 of the whole startup snapshot, so it changes with
``exposure_us`` (pre-flight F4 / ruling R-F4).
"""

import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

from openflight.camera import timestamp_semantics as ts
from openflight.camera.timing_validation import cadence_summary

ROOT = Path(__file__).resolve().parents[1]
BENCH = Path(
    os.environ.get(
        "OPENFLIGHT_BENCH_ROOT",
        Path.home() / "Desktop/Coding/Openflight Test Bench/openflight-test-bench",
    )
)
TEST7 = BENCH / "sessions/Outdoors-test-7/arm5/paired/run-01/arm5/camera"
PERIOD_120 = 8_333_333
EXPOSURES = (100, 300, 1000, 3000)


def _settings(exposure_us, period, size):
    return {
        "width": size[0],
        "height": size[1],
        "fps": round(1e9 / period, 3),
        "pre_ms": 150.0,
        "post_ms": 50.0,
        "exposure_us": exposure_us,
        "gain": 1.0,
        "frame_duration_us": round(period / 1000),
        "stream": "raw",
        "rotate_180": False,
        "mirror_horizontal": False,
        "roll_correction_deg": 0.0,
        "scaler_crop": None,
        "auto_exposure": False,
    }


def _context_id(startup):
    serialized = json.dumps(startup, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _clip(
    tmp_path,
    name,
    exposure_us,
    *,
    slope,
    frames=24,
    jitter_ns=0,
    offset_ns=2_600,
    drop_at=None,
    period=PERIOD_120,
    size=(1280, 800),
    clocks=True,
    seed=0,
    actual_duration_us=None,
    sensor=None,
    edit=None,
):
    rng = np.random.default_rng(seed)
    if sensor is None:
        sensor = np.arange(frames, dtype=np.int64) * period + 1_000_000_000
        if drop_at is not None:
            sensor[drop_at:] += 4 * period  # one gap event, four missing frames
    frames = sensor.size
    delay = (
        6_000_000
        + int(slope * exposure_us * 1000)
        + rng.integers(-jitter_ns, jitter_ns + 1, frames)
    )
    directory = tmp_path / name
    directory.mkdir()
    arrays = {
        "sensor_timestamp_ns": sensor,
        "host_timestamp_ns": sensor + delay,
        "exposure_us": np.full(frames, exposure_us, dtype=np.int32),
    }
    settings = _settings(exposure_us, period, size)
    startup = {"settings": settings, "resolved_config": {"raw": {"size": list(size)}}}
    actual = round(period / 1000) - 3 if actual_duration_us is None else actual_duration_us
    meta = {
        "settings": settings,
        "settings_scope": "capture_startup",
        "capture_mode": {
            "context_status": "uniform",
            "contexts": [{"id": _context_id(startup), "startup": startup}],
            "frames": {"context_index": [0] * frames, "frame_duration_us": [actual] * frames},
        },
    }
    if clocks:
        meta["trigger_clocks"] = {
            "host_monotonic_ns": 5_000_000_000,
            "boottime_ns": 5_000_000_000 + offset_ns,
            "boottime_minus_monotonic_ns": offset_ns,
        }
    if edit is not None:
        edit(meta, arrays)
    np.savez(directory / "frames.npz", **arrays)
    (directory / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    return directory


def _four(tmp_path, slope, offsets=None, **kw):
    return [
        _clip(
            tmp_path,
            f"c{e}-{k}",
            e,
            slope=slope,
            jitter_ns=20_000,
            seed=k,
            **({"offset_ns": offsets[e]} if offsets else {}),
            **kw,
        )
        for e in EXPOSURES
        for k in range(3)
    ]


def _reasons(result):
    return [item["reason"] for item in result["excluded"]]


def _only(result):
    """The one mode group of a result that must have exactly one."""
    assert len(result["groups"]) == 1, [group["mode_key"] for group in result["groups"]]
    return result["groups"][0]


def _by_height(result):
    return {json.loads(group["mode_key"])["height"]: group for group in result["groups"]}


# --- The brief's known-answer tests ------------------------------------------------------------


@pytest.mark.parametrize(
    "slope,verdict",
    [(1, "slope_supports_exposure_start"), (0, "slope_supports_frame_start")],
)
def test_slope_supports_a_convention_and_says_it_is_conditional(tmp_path, slope, verdict):
    result = ts.analyze_clips(_four(tmp_path, slope))
    assert result["verdict"] == verdict and _only(result)["meets_c1_design"] is True
    assert any("fixed pipeline delay" in text for text in result["limitations"])
    assert any("drift" in text for text in result["limitations"])


def test_half_slope_is_unresolved_not_rounded(tmp_path):
    assert ts.analyze_clips(_four(tmp_path, 0.5))["verdict"] == "unresolved"


def test_missing_clock_metadata_excludes_the_clip_and_blocks_all_within(tmp_path):
    dirs = _four(tmp_path, 0)[:-1] + [_clip(tmp_path, "noclk", 3000, slope=0, clocks=False)]
    result = ts.analyze_clips(dirs)
    assert {"path": str(dirs[-1]), "reason": "trigger_clocks_missing"} in result["excluded"]
    assert _only(result)["clock"]["clock_offsets_missing"] >= 1
    assert _only(result)["clock"]["all_within_1ms"] is False


def test_unequal_modes_are_analysed_separately_and_listed(tmp_path):
    other = _clip(tmp_path, "other", 300, slope=0, size=(640, 400), period=4_166_666)
    result = ts.analyze_clips(_four(tmp_path, 0) + [other])
    groups = _by_height(result)
    assert set(groups) == {800, 400} and result["excluded"] == []
    assert len(groups[800]["clips"]) == 12 and len(groups[400]["clips"]) == 1


def test_suspend_offset_is_handled_per_hypothesis_not_subtracted_blindly(tmp_path):
    dirs = _four(tmp_path, 0, offset_ns=5_000_000_000)  # 5 s of suspend before these clips
    group = _only(ts.analyze_clips(dirs))
    assert set(group["slope_by_hypothesis"]) == {"monotonic", "boottime"}
    assert group["slope_by_hypothesis"]["monotonic"]["verdict"] == "slope_supports_frame_start"
    assert group["clock"]["all_within_1ms"] is False


def test_gap_events_and_inferred_missing_frames(tmp_path):
    group = _only(ts.analyze_clips([_clip(tmp_path, "gap", 300, slope=0, drop_at=10)]))
    assert group["gap_events"] == 1 and group["missing_frames_inferred"] == 4


@pytest.mark.parametrize("frames", [0, 1])
def test_empty_or_single_frame_clip_is_excluded_not_a_crash(tmp_path, frames):
    result = ts.analyze_clips([_clip(tmp_path, f"f{frames}", 300, slope=0, frames=frames)])
    assert result["excluded"][0]["reason"] == "too_few_frames"
    assert result["verdict"] == "unresolved_no_usable_clips"  # fix round 1, Minor 2
    assert result["groups"] == []


def test_one_exposure_cannot_decide(tmp_path):
    result = ts.analyze_clips([_clip(tmp_path, f"c{k}", 300, slope=1, seed=k) for k in range(3)])
    group = _only(result)
    assert result["verdict"] == "unresolved_design_not_met" and group["meets_c1_design"] is False
    assert group["verdict"] == "unresolved_design_not_met"
    raw = {fit["verdict"] for fit in group["slope_by_hypothesis"].values()}
    assert raw == {"unresolved_single_exposure"}


@pytest.mark.skipif(not TEST7.exists(), reason="bench test-7 clips not present on this machine")
def test_historical_clips_have_one_exposure_and_cannot_answer():
    dirs = sorted(p for p in TEST7.iterdir() if (p / "frames.npz").exists())[:4]
    result = ts.analyze_clips(dirs)
    assert result["verdict"] == "unresolved_design_not_met"
    raw = {fit["verdict"] for fit in _only(result)["slope_by_hypothesis"].values()}
    assert raw == {"unresolved_single_exposure"}


# --- R-F4: the mode key ignores exposure_us, and works in every candidate mode ----------------


def test_clips_differing_only_in_exposure_share_a_mode_although_context_ids_differ(tmp_path):
    low = _clip(tmp_path, "low", 100, slope=0)
    high = _clip(tmp_path, "high", 3000, slope=0)
    ids = [
        json.loads((d / "metadata.json").read_text())["capture_mode"]["contexts"][0]["id"]
        for d in (low, high)
    ]
    assert ids[0] != ids[1]
    result = ts.analyze_clips([low, high])
    assert result["excluded"] == [] and len(_only(result)["clips"]) == 2
    assert "exposure_us" not in json.loads(_only(result)["mode_key"])


@pytest.mark.parametrize(
    "size,period", [((1280, 800), PERIOD_120), ((1280, 720), PERIOD_120), ((640, 400), 4_166_666)]
)
@pytest.mark.parametrize(
    "slope,verdict",
    [(1, "slope_supports_exposure_start"), (0, "slope_supports_frame_start")],
)
def test_every_candidate_mode_resolves(tmp_path, size, period, slope, verdict):
    result = ts.analyze_clips(_four(tmp_path, slope, size=size, period=period))
    assert result["verdict"] == verdict and result["excluded"] == []
    key = json.loads(_only(result)["mode_key"])
    assert (key["width"], key["height"]) == size
    assert key["frame_duration_us"] == round(period / 1000)


def test_same_frame_duration_different_size_is_a_different_mode(tmp_path):
    first = _clip(tmp_path, "a", 300, slope=0, size=(1280, 800))
    second = _clip(tmp_path, "b", 1000, slope=0, size=(1280, 720))
    groups = _by_height(ts.analyze_clips([first, second]))
    assert [clip["path"] for clip in groups[720]["clips"]] == [str(second)]
    assert [clip["path"] for clip in groups[800]["clips"]] == [str(first)]


def test_unequal_actual_frame_duration_across_clips_is_a_different_mode(tmp_path):
    first = _clip(tmp_path, "a", 300, slope=0, actual_duration_us=8330)
    longer = _clip(tmp_path, "b", 3000, slope=0, actual_duration_us=8400)
    result = ts.analyze_clips([first, longer])
    actual = sorted(json.loads(g["mode_key"])["actual_frame_duration_us"] for g in result["groups"])
    assert actual == [8330, 8400] and result["excluded"] == []
    assert result["verdict"] == "multiple_modes_see_groups"


def test_a_stray_first_clip_leaves_the_main_group_resolved(tmp_path):
    stray = _clip(tmp_path, "0-stray", 300, slope=0, size=(640, 400), period=4_166_666)
    result = ts.analyze_clips([stray, *_four(tmp_path, 1)])
    groups = _by_height(result)
    assert groups[800]["verdict"] == "slope_supports_exposure_start"
    assert groups[400]["verdict"] == "unresolved_design_not_met"
    assert result["verdict"] == "slope_supports_exposure_start"
    assert result["verdict_mode_key"] == groups[800]["mode_key"]


def test_two_complete_groups_are_each_resolved_and_listed_never_picked(tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    dirs = _four(first, 1) + _four(second, 0, size=(1280, 720))
    result = ts.analyze_clips(dirs)
    groups = _by_height(result)
    assert groups[800]["verdict"] == "slope_supports_exposure_start"
    assert groups[720]["verdict"] == "slope_supports_frame_start"
    assert groups[800]["meets_c1_design"] and groups[720]["meets_c1_design"]
    assert result["verdict"] == "multiple_modes_see_groups"
    assert result["verdict_mode_key"] is None


def test_input_order_does_not_change_the_output(tmp_path):
    dirs = _four(tmp_path, 0) + [
        _clip(tmp_path, "stray", 300, slope=0, size=(640, 400), period=4_166_666),
        _clip(tmp_path, "noclk", 1000, slope=0, clocks=False),
        _clip(tmp_path, "short", 300, slope=0, frames=1),
    ]
    forward = ts.analyze_clips(dirs)
    assert forward == ts.analyze_clips(dirs[::-1])
    assert forward == ts.analyze_clips(dirs[3:] + dirs[:3])


# --- Strict metadata: each gap has its own reason, and nothing crashes ------------------------


def _set_frame_duration(meta, values):
    meta["capture_mode"]["frames"]["frame_duration_us"] = values


@pytest.mark.parametrize(
    "edit,reason",
    [
        (
            lambda m, a: _set_frame_duration(m, [8330] * 23 + [8400]),
            "frame_duration_changed_within_clip",
        ),
        (
            lambda m, a: _set_frame_duration(m, [8330] * 23 + [None]),
            "actual_frame_duration_missing",
        ),
        (lambda m, a: m["capture_mode"].pop("frames"), "actual_frame_duration_missing"),
        (lambda m, a: _set_frame_duration(m, [8330] * 3), "actual_frame_duration_missing"),
        (
            lambda m, a: m["capture_mode"].update(context_status="mixed_or_incomplete"),
            "capture_mode_not_uniform",
        ),
        (lambda m, a: m.pop("capture_mode"), "capture_mode_not_uniform"),
        (lambda m, a: m["settings"].pop("frame_duration_us"), "frame_duration_missing"),
        (lambda m, a: m["settings"].pop("width"), "mode_settings_missing"),
        (lambda m, a: m["settings"].clear(), "mode_settings_missing"),
        (lambda m, a: a["exposure_us"].__setitem__(5, 1000), "exposure_changed_within_clip"),
        (lambda m, a: a["exposure_us"].__setitem__(slice(None), 0), "exposure_missing"),
        (
            lambda m, a: a.update(host_timestamp_ns=a["host_timestamp_ns"][:-1]),
            "array_lengths_differ",
        ),
        (lambda m, a: a["sensor_timestamp_ns"].__setitem__(3, 0), "timestamps_not_increasing"),
        (
            lambda m, a: a["host_timestamp_ns"].__setitem__(4, a["host_timestamp_ns"][3]),
            "timestamps_not_increasing",
        ),
        (
            lambda m, a: a.update(sensor_timestamp_ns=a["sensor_timestamp_ns"].astype(float)),
            "timestamps_not_integer",
        ),
        (lambda m, a: m["trigger_clocks"].update(boottime_ns=None), "trigger_clocks_missing"),
        (lambda m, a: m["trigger_clocks"].pop("host_monotonic_ns"), "trigger_clocks_missing"),
        (lambda m, a: m["trigger_clocks"].update(boottime_ns=True), "trigger_clocks_malformed"),
        (lambda m, a: m["trigger_clocks"].update(boottime_ns=5.0e9), "trigger_clocks_malformed"),
    ],
)
def test_incomplete_or_inconsistent_clip_is_excluded_with_its_reason(tmp_path, edit, reason):
    directory = _clip(tmp_path, "bad", 300, slope=0, edit=edit)
    result = ts.analyze_clips([directory])
    assert result["excluded"] == [{"path": str(directory), "reason": reason}]
    assert all(group["clips"] == [] for group in result["groups"])


def test_metadata_without_a_startup_snapshot_keys_on_the_saved_settings(tmp_path):
    def brief_format(meta, _arrays):
        meta["capture_mode"]["contexts"][0].pop("startup")

    dirs = [_clip(tmp_path, f"b{e}", e, slope=0, edit=brief_format) for e in (100, 3000)]
    result = ts.analyze_clips(dirs)
    assert result["excluded"] == [] and json.loads(_only(result)["mode_key"])["width"] == 1280


def test_startup_snapshot_wins_over_the_saved_settings(tmp_path):
    def stale_top_level(meta, _arrays):
        meta["settings"] = {**meta["settings"], "width": 9999}

    result = ts.analyze_clips([_clip(tmp_path, "s", 300, slope=0, edit=stale_top_level)])
    assert json.loads(_only(result)["mode_key"])["width"] == 1280


@pytest.mark.parametrize("broken", ["no_npz", "no_metadata", "bad_json", "missing_array"])
def test_unreadable_clip_is_excluded_not_a_crash(tmp_path, broken):
    directory = _clip(tmp_path, "broken", 300, slope=0)
    if broken == "no_npz":
        (directory / "frames.npz").unlink()
    elif broken == "no_metadata":
        (directory / "metadata.json").unlink()
    elif broken == "bad_json":
        (directory / "metadata.json").write_text("{not json", encoding="utf-8")
    else:
        np.savez(directory / "frames.npz", sensor_timestamp_ns=np.arange(3, dtype=np.int64))
    good = _clip(tmp_path, "good", 300, slope=0)
    result = ts.analyze_clips([directory, good])
    assert result["excluded"][0]["path"] == str(directory)
    assert result["excluded"][0]["reason"] == "unreadable"
    assert result["excluded"][0]["detail"]
    assert [clip["path"] for clip in _only(result)["clips"]] == [str(good)]


def test_the_same_clip_twice_is_one_point(tmp_path):
    clip = _clip(tmp_path, "once", 300, slope=0)
    result = ts.analyze_clips([clip, tmp_path / "." / "once"])
    assert len(_only(result)["clips"]) == 1
    assert _reasons(result) == ["duplicate_clip"]
    assert result["excluded"][0]["path"] in {str(clip), str(tmp_path / "." / "once")}
    assert _only(result)["clock"]["clock_offsets_observed"] == 1


def test_no_clips_at_all_is_an_empty_result_not_a_crash():
    result = ts.analyze_clips([])
    assert result["verdict"] == "unresolved_no_usable_clips"
    assert result["groups"] == [] and result["excluded"] == []
    assert result["verdict_mode_key"] is None


def test_a_group_with_only_clockless_clips_has_no_usable_clips(tmp_path):
    result = ts.analyze_clips([_clip(tmp_path, "n", 300, slope=0, clocks=False)])
    group = _only(result)
    assert group["clips"] == [] and group["verdict"] == "unresolved_no_usable_clips"
    assert group["clock"]["clock_offsets_missing"] == 1
    assert group["clock"]["all_within_1ms"] is False
    assert group["gap_events"] == 0 and group["missing_frames_inferred"] is None
    assert result["verdict"] == "unresolved_no_usable_clips"


def test_gap_scope_is_stated_as_usable_clips_only(tmp_path):
    gappy_but_clockless = _clip(tmp_path, "n", 300, slope=0, clocks=False, drop_at=4)
    result = ts.analyze_clips([gappy_but_clockless, _clip(tmp_path, "u", 300, slope=0)])
    assert result["gap_scope"] == "usable_clips_only"
    assert _only(result)["gap_events"] == 0


# --- Both clock domains from raw values ---------------------------------------------------------


def test_clip_delay_known_answer_under_both_hypotheses(tmp_path):
    clip = ts.load_clip(_clip(tmp_path, "k", 1000, slope=1, offset_ns=7_000))
    delay = ts.clip_delay(clip)
    assert delay["excluded_reason"] is None
    assert delay["exposure_us"] == 1000 and delay["n_frames"] == 24
    assert delay["trigger_monotonic_ns"] == 5_000_000_000
    assert delay["trigger_boottime_ns"] == 5_000_007_000
    assert delay["boottime_minus_monotonic_ns"] == 7_000
    assert delay["median_delay_ns"] == {"monotonic": 7_000_000.0, "boottime": 7_007_000.0}
    assert delay["cadence"] == cadence_summary(clip["sensor_timestamp_ns"])


def test_offset_is_recomputed_from_raw_values_not_trusted_from_the_stored_difference(tmp_path):
    def lie(meta, _arrays):
        meta["trigger_clocks"]["boottime_minus_monotonic_ns"] = 0

    delay = ts.clip_delay(ts.load_clip(_clip(tmp_path, "k", 300, slope=0, edit=lie)))
    assert delay["boottime_minus_monotonic_ns"] == 2_600


def test_suspend_between_runs_makes_the_hypotheses_disagree(tmp_path):
    # Fixed order: the host suspended for 2 s before the last (3000 us) run only.
    offsets = {100: 2_600, 300: 2_600, 1000: 2_600, 3000: 2_000_002_600}
    result = ts.analyze_clips(_four(tmp_path, 0, offsets=offsets))
    slopes = _only(result)["slope_by_hypothesis"]
    assert slopes["monotonic"]["verdict"] == "slope_supports_frame_start"
    assert slopes["boottime"]["verdict"] == "unresolved"
    assert result["verdict"] == "domain_dependent_unresolved"


def test_clock_section_lists_every_raw_offset(tmp_path):
    dirs = _four(tmp_path, 0)
    clock = _only(ts.analyze_clips(dirs))["clock"]
    assert clock["clock_offsets_observed"] == 12 and clock["clock_offsets_missing"] == 0
    assert clock["all_within_1ms"] is True
    assert clock["max_abs_boottime_minus_monotonic_ns"] == 2_600
    assert clock["raw_offsets"][0] == {
        "path": str(dirs[0]),
        "host_monotonic_ns": 5_000_000_000,
        "boottime_ns": 5_000_002_600,
        "boottime_minus_monotonic_ns": 2_600,
    }


@pytest.mark.parametrize(
    "offset,within", [(1_000_000, True), (1_000_001, False), (-1_000_001, False)]
)
def test_all_within_1ms_boundary(tmp_path, offset, within):
    result = ts.analyze_clips([_clip(tmp_path, "c", 300, slope=0, offset_ns=offset)])
    assert _only(result)["clock"]["all_within_1ms"] is within


def test_offsets_of_clips_in_another_mode_are_not_required(tmp_path):
    other = _clip(tmp_path, "other", 300, slope=0, size=(640, 400), period=4_166_666, clocks=False)
    result = ts.analyze_clips(_four(tmp_path, 0) + [other])
    groups = _by_height(result)
    assert groups[800]["clock"]["clock_offsets_missing"] == 0
    assert groups[800]["clock"]["all_within_1ms"] is True
    assert groups[400]["clock"]["clock_offsets_missing"] == 1
    assert {"path": str(other), "reason": "trigger_clocks_missing"} in result["excluded"]
    assert result["verdict"] == "slope_supports_frame_start"


# --- Gaps and the justified cadence -----------------------------------------------------------


def test_gaps_are_counted_but_missing_frames_not_inferred_from_an_unstable_cadence(tmp_path):
    steps = np.where(np.arange(23) % 2 == 0, 8_000_000, 8_700_000)  # +/-4%: not a stable period
    steps[11] = 40_000_000
    sensor = np.concatenate([[1_000_000_000], 1_000_000_000 + np.cumsum(steps)]).astype(np.int64)
    result = ts.analyze_clips([_clip(tmp_path, "wobbly", 300, slope=0, sensor=sensor)])
    assert result["excluded"] == []
    assert _only(result)["gap_events"] == 1
    assert _only(result)["missing_frames_inferred"] is None


def _one_gap(ratio, frames=24, period=PERIOD_120):
    steps = np.full(frames - 1, period, dtype=np.int64)
    steps[10] = int(round(ratio * period))
    return np.concatenate([[1_000_000_000], 1_000_000_000 + np.cumsum(steps)]).astype(np.int64)


@pytest.mark.parametrize(
    "ratio,missing",
    [(1.6, None), (2.5, None), (3.0, 2), (3.015, 2), (2.985, 2), (3.03, None), (5.0, 4)],
)
def test_missing_frames_need_a_gap_that_is_a_whole_number_of_periods(tmp_path, ratio, missing):
    clip = _clip(tmp_path, "g", 300, slope=0, sensor=_one_gap(ratio))
    group = _only(ts.analyze_clips([clip]))
    assert group["gap_events"] == 1
    assert group["missing_frames_inferred"] == missing


def test_one_unjustified_gap_makes_the_group_total_none(tmp_path):
    dirs = [
        _clip(tmp_path, "whole", 300, slope=0, sensor=_one_gap(3.0)),
        _clip(tmp_path, "odd", 1000, slope=0, sensor=_one_gap(1.6)),
    ]
    group = _only(ts.analyze_clips(dirs))
    assert group["gap_events"] == 2 and group["missing_frames_inferred"] is None


def test_gaps_sum_over_analysed_clips(tmp_path):
    dirs = [
        _clip(tmp_path, "a", 300, slope=0, drop_at=5),
        _clip(tmp_path, "b", 1000, slope=0, drop_at=15),
        _clip(tmp_path, "c", 3000, slope=0),
    ]
    group = _only(ts.analyze_clips(dirs))
    assert group["gap_events"] == 2 and group["missing_frames_inferred"] == 8


# --- exposure_slope directly --------------------------------------------------------------------


def test_exposure_slope_known_answer():
    points = [
        (e * 1000.0, 5_000_000.0 + e * 1000.0 + d) for e, d in [(100, 10), (1000, -10), (3000, 0)]
    ]
    fit = ts.exposure_slope(points)
    assert fit["slope"] == pytest.approx(1.0, abs=1e-4)
    assert fit["ci95"][0] < 1.0 < fit["ci95"][1]
    assert fit["verdict"] == "slope_supports_exposure_start"
    assert fit["n_points"] == 3 and fit["distinct_exposures"] == 3
    assert fit["exposure_span_us"] == 2900.0


def test_exposure_slope_two_points_has_no_interval():
    fit = ts.exposure_slope([(100_000.0, 1.0), (3_000_000.0, 2.0)])
    assert fit["verdict"] == "unresolved_too_few_points"
    assert fit["slope"] is None and fit["ci95"] is None and fit["distinct_exposures"] == 2


def test_exposure_slope_empty():
    fit = ts.exposure_slope([])
    assert fit == {
        "slope": None,
        "ci95": None,
        "verdict": "unresolved_single_exposure",
        "n_points": 0,
        "distinct_exposures": 0,
        "exposure_span_us": 0.0,
    }


def test_exposure_slope_too_noisy_is_unresolved():
    points = [(e * 1000.0, 6e6 + n) for e, n in [(100, 0), (300, 3e6), (1000, -3e6), (3000, 1e6)]]
    assert ts.exposure_slope(points)["verdict"] == "unresolved"


def test_design_needs_three_exposures_spanning_a_millisecond(tmp_path):
    narrow = [_clip(tmp_path, f"n{e}", e, slope=0) for e in (100, 300, 900)]
    assert _only(ts.analyze_clips(narrow))["meets_c1_design"] is False
    two = [_clip(tmp_path, f"t{e}-{k}", e, slope=0, seed=k) for e in (100, 3000) for k in range(2)]
    assert _only(ts.analyze_clips(two))["meets_c1_design"] is False


def test_two_exposures_20us_apart_do_not_meet_the_design(tmp_path):
    dirs = [
        _clip(tmp_path, f"c{e}-{k}", e, slope=1, jitter_ns=20_000, seed=k)
        for e in (300, 320)
        for k in range(2)
    ]
    result = ts.analyze_clips(dirs)
    group = _only(result)
    assert group["meets_c1_design"] is False and len(group["clips"]) == 4
    assert group["verdict"] == "unresolved_design_not_met"
    assert result["verdict"] == "unresolved_design_not_met"
    assert all(fit["slope"] is not None for fit in group["slope_by_hypothesis"].values())


def test_three_clips_at_two_exposures_do_not_meet_the_design(tmp_path):
    dirs = [
        _clip(tmp_path, "a", 100, slope=1, jitter_ns=20_000, seed=0),
        _clip(tmp_path, "b", 100, slope=1, jitter_ns=20_000, seed=1),
        _clip(tmp_path, "c", 3000, slope=1, jitter_ns=20_000, seed=2),
    ]
    result = ts.analyze_clips(dirs)
    group = _only(result)
    assert group["meets_c1_design"] is False
    assert result["verdict"] == group["verdict"] == "unresolved_design_not_met"
    assert group["slope_by_hypothesis"]["monotonic"]["verdict"] != "unresolved_design_not_met"


def test_a_zero_noise_fit_has_no_interval_and_is_unresolved():
    points = [(e * 1000.0, 6e6) for e in (100, 300, 900)]  # flat, no residual: stderr is NaN
    fit = ts.exposure_slope(points)
    assert fit["verdict"] == "unresolved" and fit["ci95"] is None


def test_jitter_free_clips_never_crash_the_json(tmp_path, capsys):
    dirs = [_clip(tmp_path, f"z{e}", e, slope=0) for e in (100, 300, 900)]
    raw = _only(ts.analyze_clips(dirs))["slope_by_hypothesis"]
    assert all(fit["verdict"] == "unresolved" and fit["ci95"] is None for fit in raw.values())
    out = tmp_path / "z.json"
    assert _cli().main([str(d) for d in dirs] + ["--out", str(out)]) == 0
    json.loads(out.read_text(encoding="utf-8"))
    assert "unresolved_design_not_met" in capsys.readouterr().out


def test_limitations_state_what_this_tool_does_not_decide(tmp_path):
    limitations = " ".join(ts.analyze_clips([])["limitations"])
    assert "W2b" in limitations and "E/2" in limitations
    assert "provenance" in limitations
    assert "MONOTONIC" in limitations and "BOOTTIME" in limitations


# --- CLI ----------------------------------------------------------------------------------------


def _cli():
    sys.path.insert(0, str(ROOT / "scripts" / "analysis"))
    try:
        import camera_timestamp_semantics  # noqa: PLC0415  pylint: disable=import-outside-toplevel
    finally:
        sys.path.pop(0)
    return camera_timestamp_semantics


def test_cli_globs_a_parent_directory_and_writes_json(tmp_path, capsys):
    parent = tmp_path / "camera"
    parent.mkdir()
    _four(parent, 1)
    (parent / "not-a-clip").mkdir()
    out = tmp_path / "report.json"
    assert _cli().main([str(parent), "--out", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["verdict"] == "slope_supports_exposure_start"
    assert len(_only(report)["clips"]) == 12
    printed = capsys.readouterr().out
    assert "slope_supports_exposure_start" in printed and "boottime" in printed
    assert "distinct exposures: 4" in printed


def test_cli_takes_clip_directories_and_lists_exclusions(tmp_path, capsys):
    good = _clip(tmp_path, "good", 300, slope=0)
    bad = _clip(tmp_path, "bad", 300, slope=0, clocks=False)
    out = tmp_path / "r.json"
    assert _cli().main([str(good), str(bad), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "trigger_clocks_missing" in printed and "missing: 1" in printed


def test_cli_prints_every_group(tmp_path, capsys):
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    dirs = _four(first, 1) + _four(second, 0, size=(1280, 720))
    assert _cli().main([str(first), str(second), "--out", str(tmp_path / "r.json")]) == 0
    printed = capsys.readouterr().out
    assert "verdict: multiple_modes_see_groups" in printed
    assert "1280x800" in printed and "1280x720" in printed
    assert len(dirs) == 24


def test_cli_with_no_clips_is_an_error(tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _cli().main([str(empty), "--out", str(tmp_path / "r.json")]) == 2
    assert "no clip" in capsys.readouterr().err
    assert not (tmp_path / "r.json").exists()
