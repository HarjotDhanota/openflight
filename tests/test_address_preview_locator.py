"""The box step's address-preview locator keeps the classic locator's contract."""

from __future__ import annotations

import logging
import threading
import time

import numpy as np
import pytest

from openflight.camera import tester_server as ts
from openflight.camera.address_preview import display
from openflight.camera.address_preview.pipeline import AddressPreviewPipeline
from tests.test_tester_box_contract import _locator as classic_locator, _ScriptedLive

A = {"x_px": 640.0, "y_px": 440.0, "radius_px": 10.0}
B = {"x_px": 720.0, "y_px": 440.0, "radius_px": 10.0}
FRAMES = np.full((5, 800, 1280), 105, np.uint8)
# the box preview brightness is settled at 1200 us x 1.0 over a 105 DN picture
APPLIED = [(1200, 1.0)] * 5


class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


def view(selected=None, *, status="checking_rivals", source_at=99.5, now=100.0):
    if selected is None:
        return display.unavailable("no committed measurement")
    return {
        **display.unavailable("exact analysed callback window"),
        "status": status,
        "selected": dict(selected),
        "source_observed_at_monotonic": source_at,
        "look_age_s": now - source_at,
        "rivals_pending": status == "checking_rivals",
    }


class FakePipeline:
    """Stands in for AddressPreviewPipeline: the test says what a read shows."""

    def __init__(self, camera, box, **kwargs):
        self.camera = camera
        self.box = box
        self.kwargs = kwargs
        self.view = view()
        self.failure = None
        self.last_search = None
        self.offered = []
        self.closed = 0

    def offer(self, pixels, metadata, sequence, observed_at):
        self.offered.append((pixels, metadata, sequence, observed_at))

    def read(self):
        return dict(self.view)

    def status(self):
        return {
            "ready": self.failure is None,
            "failure": self.failure,
            "closed": bool(self.closed),
            "last_search": self.last_search,
            "skipped_windows": 0,
            "observed_windows": 0,
        }

    def close(self):
        self.closed += 1


def _camera():
    return ts.BallPlaneCamera.nominal(
        focal_px=933.3,
        image_width_px=1280,
        image_height_px=800,
        pitch_deg=0.0,
        roll_correction_deg=0.0,
        mirror_horizontal=False,
        camera_origin_lfu=(0.0, 0.0, 0.095),
        radar_origin_lfu=(0.0, -0.0014, 0.0444),
        angular_uncertainty_deg=3.0,
        focal_relative_uncertainty=0.08,
    )


def _adapter(*, clock=None, pipeline_factory=FakePipeline, fallback=None):
    camera = _camera()
    record = ts.fixed_box_record(camera, {"camera_pitch_deg": 0.0}, 0.0)
    box = ts.ground_patch.PatchSearch.from_record(record)
    live = _ScriptedLive()
    brightness = ts.BoxPreviewExposure(live, ts.ARMS["arm5"], APPLIED[0])
    changes = []
    fallbacks = []

    def classic():
        locator = ts.BoxBallLocator(brightness, camera, box, None, clock=clock or time.monotonic)
        fallbacks.append(locator)
        return locator if fallback is None else fallback(locator)

    adapter = ts.AddressPreviewLocator(
        brightness,
        camera,
        box,
        fallback=classic,
        clock=clock or time.monotonic,
        on_change=changes.append,
        pipeline_factory=pipeline_factory,
    )
    live.analyzer = adapter
    return adapter, live, changes, fallbacks


@pytest.fixture
def looks():
    clock = Clock()
    adapter, live, changes, fallbacks = _adapter(clock=clock)
    pipeline = adapter._pipeline  # pylint: disable=protected-access
    sequence = [0]

    def look(selected=None, *, status="checking_rivals", age=0.5, last_search=None):
        clock.t += 1.0
        sequence[0] += 5
        pipeline.view = view(selected, status=status, source_at=clock.t - age, now=clock.t)
        pipeline.last_search = last_search
        payload = adapter(FRAMES, sequence[0], APPLIED, observed_at=clock.t - 0.05)
        return adapter.ball(), payload

    return adapter, look, clock, pipeline, changes, live, fallbacks


def test_payload_and_state_carry_every_classic_key(monkeypatch, looks):
    _adapter_, look, _clock, _pipeline, changes, _live, _fallbacks = looks
    inside = {"x_px": 640.0, "y_px": 440.0, "diameter_px": 20.0}
    classic, _live_, _record = classic_locator(
        monkeypatch, [{"status": "selected", "selected": inside}] * 2
    )
    frames = np.full((3, 800, 1280), 20, dtype=np.uint8)
    classic_payload = classic(frames, 1, [(300, 4.0)] * 3)
    classic_ball = classic.ball()

    ball, payload = look(A)

    assert set(payload) >= set(classic_payload)
    assert set(payload["ball_observation"]) >= set(classic_payload["ball_observation"])
    assert set(ball) >= set(classic_ball)
    assert set(changes[-1]) >= set(classic_ball) - {"look_age_s", "missed_looks", "confirmable"}
    assert ball["state"] == "found"
    assert ball["center_px"] == [640.0, 440.0]
    assert ball["diameter_px"] == 20.0
    assert ball["message"] == "Ball found in the box."
    assert ball["frame_size_px"] == [1280, 800]
    assert payload["ball_state"] == "found"
    assert payload["ball_search_mode"] == "address_preview"


def test_nothing_it_finds_is_a_distance_or_a_metric(looks):
    _adapter_, look, *_ = looks
    ball, payload = look(A)
    assert ball["position_estimate"] is None
    selected = payload["ball_observation"]["selected"]
    assert "size_point_lfu_m" not in selected
    assert payload["ball_observation"]["metric_eligible"] is False


def test_confirmable_needs_the_same_ball_on_consecutive_looks(looks):
    _adapter_, look, *_ = looks
    first, _ = look(A)
    second, _ = look(A)
    moved, _ = look(B)
    again, _ = look(B)
    assert first["confirmable"] is False
    assert second["confirmable"] is True
    assert moved["state"] == "found" and moved["confirmable"] is False
    assert again["confirmable"] is True


def test_a_look_without_a_ball_restarts_the_streak(looks):
    _adapter_, look, *_ = looks
    look(A)
    missed, _ = look(None)
    found, _ = look(A)
    assert missed["state"] != "found"
    assert missed["center_px"] is None
    assert found["confirmable"] is False


def test_confirmation_needs_a_fresh_measurement(looks):
    _adapter_, look, *_ = looks
    look(A, age=0.5)
    old, _ = look(A, age=ts.BOX_BALL_LOOK_MAX_AGE_S + 0.1)
    assert old["confirmable"] is False
    assert old["look_age_s"] > ts.BOX_BALL_LOOK_MAX_AGE_S


def test_stable_time_runs_while_it_is_the_same_ball(looks):
    _adapter_, look, *_ = looks
    first, _ = look(A)
    second, _ = look(A)
    moved, _ = look(B)
    assert first["stable_s"] == 0.0
    assert second["stable_s"] == pytest.approx(1.0)
    assert moved["stable_s"] == 0.0


def test_rival_check_is_reported_without_changing_found(looks):
    _adapter_, look, *_ = looks
    pending, _ = look(A, status="checking_rivals")
    checked, _ = look(A, status="proposal")
    assert pending["rivals_checked"] is False
    assert checked["rivals_checked"] is True
    assert pending["state"] == checked["state"] == "found"


def test_ball_withdraws_the_outline_as_soon_as_the_current_read_does(looks):
    adapter, look, _clock, pipeline, *_ = looks
    look(A)
    found, _ = look(A)
    assert found["confirmable"] is True
    pipeline.view = display.unavailable("controls changed")
    withdrawn = adapter.ball()
    assert withdrawn["state"] == "waiting"
    assert withdrawn["center_px"] is None
    assert withdrawn["confirmable"] is False
    assert withdrawn["display_check"] == "controls changed"


def test_no_ball_needs_a_finished_search_on_these_controls(looks):
    _adapter_, look, *_ = looks
    unknown, _ = look(None)
    other, _ = look(None, last_search={"status": "unavailable", "current_controls": False})
    empty, _ = look(None, last_search={"status": "unavailable", "current_controls": True})
    rivals, _ = look(None, last_search={"status": "ambiguous", "current_controls": True})
    assert unknown["state"] == other["state"] == "waiting"
    assert empty["state"] == "no_ball"
    assert rivals["state"] == "ambiguous"
    assert rivals["confirmable"] is False


def test_no_ball_waits_for_the_brightness_to_settle():
    clock = Clock()
    adapter, _live, _changes, _fallbacks = _adapter(clock=clock)
    adapter._pipeline.last_search = {"status": "unavailable", "current_controls": True}  # pylint: disable=protected-access
    adapter(np.full((5, 800, 1280), 20, np.uint8), 5, APPLIED, observed_at=clock.t)
    assert adapter.status()["state"] == "adjusting"
    assert adapter.ball()["state"] == "waiting"


def test_brightness_changes_reach_the_live_view_as_the_adapters(looks):
    adapter, _look, _clock, _pipeline, _changes, live, _fallbacks = looks
    adapter.brightness._state = "adjusting"  # pylint: disable=protected-access
    adapter(np.full((5, 800, 1280), 3, np.uint8), 99, APPLIED, observed_at=_clock.t)
    assert adapter.brightness.owner is adapter
    assert live.controls


def test_frames_are_offered_to_the_preview(looks):
    adapter, _look, _clock, pipeline, *_ = looks
    pixels = np.zeros((800, 1280), np.uint8)
    adapter.offer(pixels, {"ExposureTime": 300, "AnalogueGain": 4.0}, 7, 12.5)
    assert pipeline.offered == [(pixels, {"ExposureTime": 300, "AnalogueGain": 4.0}, 7, 12.5)]


def test_old_or_future_inputs_are_refused_like_the_classic_locator(looks):
    adapter, _look, clock, *_ = looks
    with pytest.raises(ValueError, match="observation time"):
        adapter(FRAMES, 1, APPLIED, observed_at=clock.t + 1.0)


def test_an_unavailable_preview_falls_back_to_the_classic_locator(monkeypatch, caplog):
    clock = Clock()
    adapter, live, _changes, fallbacks = _adapter(clock=clock)
    pipeline = adapter._pipeline  # pylint: disable=protected-access
    pipeline.failure = "NativeUnavailable: no C compiler: cc was not found"
    inside = {"x_px": 640.0, "y_px": 440.0, "diameter_px": 20.0}
    monkeypatch.setattr(
        ts,
        "_guided_camera_analysis",
        lambda *_a, **_k: (None, {"status": "selected", "selected": inside, "candidates": []}),
    )
    caplog.set_level(logging.WARNING)
    for step in range(3):
        clock.t += 1.0
        payload = adapter(FRAMES, 5 * (step + 1), APPLIED, observed_at=clock.t)
    ball = adapter.ball()

    assert len(fallbacks) == 1
    assert _wait(lambda: pipeline.closed == 1)
    assert payload["ball_search_mode"] in {"searched", "watched"}
    assert ball["state"] == "found" and ball["confirmable"] is True
    assert ball["locator"] == {
        "requested": "preview",
        "active": "classic",
        "reason": "NativeUnavailable: no C compiler: cc was not found",
    }
    assert payload["ball_locator"] == ball["locator"]
    assert adapter.brightness.owner is adapter
    messages = [r.getMessage() for r in caplog.records if "classic" in r.getMessage()]
    assert len(messages) == 1
    # frames no longer go to the closed preview
    adapter.offer(np.zeros((800, 1280), np.uint8), {}, 99, clock.t)
    assert not pipeline.offered
    adapter.brightness._state = "adjusting"  # pylint: disable=protected-access
    adapter(np.full((5, 800, 1280), 3, np.uint8), 99, APPLIED, observed_at=clock.t)
    assert live.controls


def test_the_preview_is_reported_while_it_runs(looks):
    _adapter_, look, *_ = looks
    ball, payload = look(A)
    assert ball["locator"] == {"requested": "preview", "active": "preview", "reason": None}
    assert payload["ball_locator"] == ball["locator"]


def test_close_ends_the_preview_and_any_fallback():
    clock = Clock()
    closed = []

    def tracked(locator):
        locator.close = lambda: closed.append(True)
        return locator

    adapter, _live, _changes, _fallbacks = _adapter(clock=clock, fallback=tracked)
    pipeline = adapter._pipeline  # pylint: disable=protected-access
    pipeline.failure = "RuntimeError: the broad search worker did not start"
    adapter(FRAMES, 5, APPLIED, observed_at=clock.t)
    adapter.close()
    adapter.close()
    assert pipeline.closed == 1
    assert closed == [True]


# --- with the real frame pipeline and display check ------------------------------


class ScriptedPreview:
    """A preview whose every observation commits the ball it is given."""

    def __init__(self, clock):
        self.clock = clock
        self.selected = dict(A)
        self.watch = None
        self.bounds = None
        self.observed = []
        self.closed = False

    def observe(self, frames, camera, patch, controls, sequence, *, at):
        self.observed.append((sequence, list(controls)))
        state = view(self.selected, source_at=at, now=self.clock())
        return state

    def last_search(self):
        return None

    def close(self):
        self.closed = True


def _offer(pipeline, sequence, at, exposure=300):
    pixels = np.full((800, 1280), 105, np.uint8)
    pipeline.offer(pixels, {"ExposureTime": exposure, "AnalogueGain": 4.0}, sequence, at)


def _wait(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


@pytest.fixture
def real_pipeline():
    clock = Clock(10.0)
    previews = []

    def factory(camera, box, **kwargs):
        def start(_model_path):
            preview = ScriptedPreview(clock)
            previews.append(preview)
            return preview

        return AddressPreviewPipeline(
            camera, box, model_path=kwargs["model_path"], clock=clock, preview_factory=start
        )

    adapter, _live, _changes, _fallbacks = _adapter(clock=clock, pipeline_factory=factory)
    yield adapter, clock, previews
    adapter.close()


def test_a_stale_outline_is_withdrawn_after_two_seconds(real_pipeline):
    adapter, clock, previews = real_pipeline
    pipeline = adapter._pipeline  # pylint: disable=protected-access
    for sequence in range(1, 6):
        _offer(pipeline, sequence, clock.t)
    assert _wait(lambda: previews and previews[0].observed)
    clock.t += 0.1
    assert adapter.ball()["center_px"] is None  # nothing looked yet
    adapter(FRAMES, 5, APPLIED, observed_at=clock.t)
    assert adapter.ball()["state"] == "found"
    clock.t += 2.0
    stale = adapter.ball()
    assert stale["center_px"] is None
    assert stale["confirmable"] is False
    assert stale["display_check"] == "measurement expired"


def test_a_controls_change_withdraws_the_outline(real_pipeline):
    adapter, clock, previews = real_pipeline
    pipeline = adapter._pipeline  # pylint: disable=protected-access
    for sequence in range(1, 6):
        _offer(pipeline, sequence, clock.t + 0.01 * sequence)
    assert _wait(lambda: previews and previews[0].observed)
    clock.t += 0.1
    adapter(FRAMES, 5, APPLIED, observed_at=clock.t)
    assert adapter.ball()["state"] == "found"
    _offer(pipeline, 6, clock.t, exposure=500)
    withdrawn = adapter.ball()
    assert withdrawn["center_px"] is None
    assert withdrawn["display_check"] == "controls changed"


def test_the_live_view_offers_every_frame_and_closes_the_adapter(monkeypatch):
    from tests.test_tester_server import FakeCamera

    clock = Clock()
    adapter, _live, _changes, _fallbacks = _adapter(clock=clock)
    pipeline = adapter._pipeline  # pylint: disable=protected-access
    live = ts.LiveView(camera_factory=FakeCamera)
    adapter.brightness._live = live  # pylint: disable=protected-access
    try:
        live.start(ts.ARMS["arm5"], 300, 4.0, analyzer=adapter)
        assert _wait(lambda: len(pipeline.offered) >= 3)
        sequences = [item[2] for item in pipeline.offered]
        assert sequences == sorted(sequences) and len(set(sequences)) == len(sequences)
        pixels, metadata, _sequence, observed_at = pipeline.offered[-1]
        assert pixels.shape == (800, 1280)
        assert metadata == {"ExposureTime": 280, "AnalogueGain": 4.0}
        assert observed_at <= time.monotonic()
    finally:
        live.stop()
    assert _wait(lambda: pipeline.closed == 1)


def test_replacing_the_live_analyzer_closes_the_old_adapter():
    from tests.test_tester_server import FakeCamera

    adapter, _live, _changes, _fallbacks = _adapter(clock=Clock())
    pipeline = adapter._pipeline  # pylint: disable=protected-access
    live = ts.LiveView(camera_factory=FakeCamera)
    try:
        live.start(ts.ARMS["arm5"], 300, 4.0, analyzer=adapter)
        live.start(ts.ARMS["arm5"], 300, 4.0, analyzer=adapter)
        assert pipeline.closed == 0
        live.start(ts.ARMS["arm5"], 300, 4.0, analyzer=lambda *_a: {"status": "not_found"})
        assert _wait(lambda: pipeline.closed == 1)
    finally:
        live.stop()


def test_a_slow_offer_never_holds_the_live_lock():
    from tests.test_tester_server import FakeCamera

    entered = threading.Event()
    release = threading.Event()

    class Slow(FakePipeline):
        def offer(self, *args):
            entered.set()
            release.wait(timeout=2.0)
            super().offer(*args)

    adapter, _live, _changes, _fallbacks = _adapter(clock=Clock(), pipeline_factory=Slow)
    live = ts.LiveView(camera_factory=FakeCamera)
    try:
        live.start(ts.ARMS["arm5"], 300, 4.0, analyzer=adapter)
        assert entered.wait(timeout=2.0)
        started = time.monotonic()
        live.snapshot()
        live.recent_applied_controls()
        assert time.monotonic() - started < 0.5
    finally:
        release.set()
        live.stop()


# --- selection: the command line and the box step ---------------------------------


class _Stop(Exception):
    pass


def _main_kwargs(monkeypatch, tmp_path, argv):
    captured = {}

    def create_app(**kwargs):
        captured.update(kwargs)
        raise _Stop

    class Tilt:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(ts, "create_app", create_app)
    monkeypatch.setattr(ts, "configure_ball_search_workers", lambda *_a, **_k: None)
    monkeypatch.setattr(ts, "EnclosureTilt", Tilt)
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    try:
        with pytest.raises(_Stop):
            ts.main(["--sessions-root", str(tmp_path), "--no-inclinometer", *argv])
    finally:
        for handler in root.handlers[:]:
            if handler not in handlers:
                root.removeHandler(handler)
                handler.close()
        root.setLevel(level)
    return captured


def test_the_command_line_runs_the_preview_by_default(monkeypatch, tmp_path):
    assert _main_kwargs(monkeypatch, tmp_path, [])["box_ball_locator"] == "preview"


def test_the_command_line_can_choose_the_classic_locator(monkeypatch, tmp_path):
    chosen = _main_kwargs(monkeypatch, tmp_path, ["--box-ball-locator", "classic"])
    assert chosen["box_ball_locator"] == "classic"


def test_the_command_line_refuses_an_unknown_locator(capsys):
    with pytest.raises(SystemExit):
        ts.main(["--box-ball-locator", "fastest"])
    assert "--box-ball-locator" in capsys.readouterr().err


def test_an_unknown_locator_is_refused_before_the_app_is_built(tmp_path):
    with pytest.raises(ValueError, match="box_ball_locator"):
        ts.create_app(sessions_root=tmp_path, box_ball_locator="fastest")


@pytest.mark.parametrize(
    "choice,kind", [("preview", ts.AddressPreviewLocator), ("classic", ts.BoxBallLocator)]
)
def test_the_box_step_runs_the_chosen_locator(monkeypatch, tmp_path, choice, kind):
    from tests.test_box_preview_exposure import RIG, EligibleSetup, LinearScene, SceneLive, Tilt

    pipelines = []

    def pipeline(*args, **kwargs):
        pipelines.append(FakePipeline(*args, **kwargs))
        return pipelines[-1]

    monkeypatch.setattr(ts, "AddressPreviewPipeline", pipeline)
    live = SceneLive(LinearScene(light=0.8))
    app = ts.create_app(
        sessions_root=tmp_path / "sessions",
        rig_geometry=RIG,
        manager=ts.TesterJobManager(),
        live_view=live,
        tilt=Tilt(),
        setup_policy=EligibleSetup(),
        box_ball_locator=choice,
    )
    client = app.test_client()
    tester = "20261030-box"

    opened = client.post("/api/tester/placement-box", json={"tester_id": tester, "action": "show"})
    live.pump(2)
    shown = client.get("/api/tester/placement-box", query_string={"tester_id": tester}).get_json()

    assert opened.status_code == 200, opened.get_json()
    assert type(live.analyzer) is kind
    assert live.analyzer.brightness.owner is live.analyzer
    assert shown["ball"]["state"] == "waiting"
    assert shown["ball"]["rivals_checked"] in (None, False)
    assert shown["ball"]["locator"] == (
        {"requested": "preview", "active": "preview", "reason": None}
        if choice == "preview"
        else None
    )
    assert len(pipelines) == (1 if choice == "preview" else 0)
    if pipelines:
        # the preview looks in the box this arm draws, through this arm's camera
        assert pipelines[0].camera.image_width_px == ts.ARMS[ts.PLACEMENT_BOX_ARM].width
        assert pipelines[0].box == live.analyzer.box
        assert pipelines[0].kwargs["model_path"] == ts.ADDRESS_PREVIEW_MODEL


def test_a_box_step_that_cannot_open_the_camera_ends_its_preview(monkeypatch, tmp_path):
    from tests.test_box_preview_exposure import RIG, EligibleSetup, LinearScene, SceneLive, Tilt

    pipelines = []

    def pipeline(*args, **kwargs):
        pipelines.append(FakePipeline(*args, **kwargs))
        return pipelines[-1]

    class StuckLive(SceneLive):
        def start(self, *_args, **_kwargs):
            raise RuntimeError("previous live camera capture is still stopping")

    monkeypatch.setattr(ts, "AddressPreviewPipeline", pipeline)
    app = ts.create_app(
        sessions_root=tmp_path / "sessions",
        rig_geometry=RIG,
        manager=ts.TesterJobManager(),
        live_view=StuckLive(LinearScene(light=0.8)),
        tilt=Tilt(),
        setup_policy=EligibleSetup(),
        box_ball_locator="preview",
    )

    opened = app.test_client().post(
        "/api/tester/placement-box", json={"tester_id": "20261030-stuck", "action": "show"}
    )

    assert opened.status_code != 200
    assert len(pipelines) == 1
    assert _wait(lambda: pipelines[0].closed == 1)


def test_falling_back_never_waits_for_the_preview_to_close():
    release = threading.Event()

    class Hung(FakePipeline):
        def close(self):
            release.wait(timeout=5.0)
            super().close()

    clock = Clock()
    adapter, _live, _changes, _fallbacks = _adapter(clock=clock, pipeline_factory=Hung)
    pipeline = adapter._pipeline  # pylint: disable=protected-access
    pipeline.failure = "RuntimeError: the search worker failed"
    try:
        started = time.monotonic()
        ball = adapter.ball()
        adapter(FRAMES, 5, APPLIED, observed_at=clock.t)
        assert time.monotonic() - started < 1.0
        assert ball["locator"]["active"] == "classic"
    finally:
        release.set()
    assert _wait(lambda: pipeline.closed == 1)
