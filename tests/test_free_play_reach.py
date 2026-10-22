"""What a tester reaches during free play: club, hand, aim, face photos, Setup, export."""

import io
import json
import urllib.error
from types import SimpleNamespace

import numpy as np
import pytest
from flask import Flask

from openflight import free_play, server, session_bundle, tester_hooks
from openflight.camera import tester_server as ts
from tests.test_free_play import Jobs
from tests.test_tester_server import eligible_app, setup_saw_ball
from tests.test_tester_target_line_flow import confirm_aim, inputs as _inputs_fixture
from tests.test_tester_tee_range_flow import app_for, confirm_box

TESTER = "20260922-name"


def _free_play_app(tmp_path, monkeypatch, manager=None):
    setup_saw_ball(tmp_path)
    monkeypatch.setattr(ts, "setup_light_warning", lambda _: {"light_index_dn_per_us_gain": 0.25})
    manager = manager or Jobs()
    return eligible_app(sessions_root=tmp_path, manager=manager), manager


def _start(app, **extra):
    return app.test_client().post(
        "/api/tester/run",
        json={
            "action": "free_play",
            "tester_id": TESTER,
            "arm_id": "arm1",
            "environment": "indoors",
            "resolution": "arm5",
            **extra,
        },
    )


def _flag(command, flag):
    return command[command.index(flag) + 1] if flag in command else None


class TestFirstClub:
    def test_the_kiosk_starts_on_the_club_the_tester_tapped(self, tmp_path, monkeypatch):
        app, manager = _free_play_app(tmp_path, monkeypatch)
        response = _start(app, club="pw")
        assert response.status_code == 202, response.get_json()
        assert _flag(manager.calls[0][1][0], "--club") == "pw"

    def test_without_a_tap_the_kiosk_keeps_its_old_club(self, tmp_path, monkeypatch):
        app, manager = _free_play_app(tmp_path, monkeypatch)
        assert _start(app).status_code == 202
        assert _flag(manager.calls[0][1][0], "--club") == ts.CLUB

    @pytest.mark.parametrize("club", ["putter", "unknown", 7])
    def test_a_club_the_kiosk_does_not_know_is_refused(self, tmp_path, monkeypatch, club):
        app, manager = _free_play_app(tmp_path, monkeypatch)
        response = _start(app, club=club)
        assert response.status_code == 400
        assert "club" in response.get_json()["error"]
        assert not manager.calls


class TestHandedness:
    def test_right_handed_is_the_default_and_a_choice_is_kept_per_tester(self, tmp_path):
        client = eligible_app(sessions_root=tmp_path, manager=Jobs()).test_client()
        assert client.get(f"/api/tester/handedness?tester_id={TESTER}").get_json() == {
            "tester_id": TESTER,
            "handed": "RH",
            "source": "default",
        }
        saved = client.post("/api/tester/handedness", json={"tester_id": TESTER, "handed": "LH"})
        assert saved.status_code == 200
        assert saved.get_json()["handed"] == "LH"
        again = client.get(f"/api/tester/handedness?tester_id={TESTER}").get_json()
        assert again == {"tester_id": TESTER, "handed": "LH", "source": "tester"}
        other = client.get("/api/tester/handedness?tester_id=someone-else").get_json()
        assert other["handed"] == "RH"

    @pytest.mark.parametrize("handed", ["left", "", None, "rh"])
    def test_only_rh_or_lh_is_saved(self, tmp_path, handed):
        client = eligible_app(sessions_root=tmp_path, manager=Jobs()).test_client()
        response = client.post(
            "/api/tester/handedness", json={"tester_id": TESTER, "handed": handed}
        )
        assert response.status_code == 400

    @pytest.mark.parametrize("saved", [None, "RH", "LH"])
    def test_the_kiosk_starts_with_the_testers_hand(self, tmp_path, monkeypatch, saved):
        app, manager = _free_play_app(tmp_path, monkeypatch)
        if saved:
            app.test_client().post(
                "/api/tester/handedness", json={"tester_id": TESTER, "handed": saved}
            )
        assert _start(app).status_code == 202
        assert _flag(manager.calls[0][1][0], "--handed") == (saved or "RH")


class FakeRuntime:
    """The camera as a paused free play uses it: held still controls and recent frames."""

    def __init__(self):
        self.settings = SimpleNamespace(
            exposure_us=150, gain=4.0, fps=120.0, light_index_dn_per_us_gain=None
        )
        self.calls = []
        self.held = None

    def hold_still_controls(self, exposure_us, gain):
        self.calls.append(("hold", exposure_us, gain))
        self.held = (exposure_us, gain)
        return {"exposure_us": exposure_us, "gain": gain}

    def release_still_controls(self):
        self.calls.append(("release",))
        self.held = None
        return {"exposure_us": 150, "gain": 4.0}

    def recent_frames(self, count, *, timeout_s=2.0):
        del timeout_s
        exposure, gain = self.held or (150, 4.0)
        image = np.full((40, 64), 90, dtype=np.uint8)
        return [SimpleNamespace(image=image, exposure_us=exposure, analogue_gain=gain)] * count


class EventLog:
    def __init__(self, run_dir):
        self.log_dir = run_dir
        self.active_session_uuid = "session"
        self.events = []

    def log_event(self, entry_type, data):
        self.events.append((entry_type, data))


@pytest.fixture
def kiosk(tmp_path, monkeypatch):
    """A free-play kiosk recording run-1 for TESTER, its camera and session log faked."""
    config = {
        "enabled": True,
        "phase": "recording",
        "tester_id": TESTER,
        "run_id": "run-1",
        "control_port": 8765,
    }
    # the kiosk's /api/free-play route holds this very dict: change it in place
    for key, value in config.items():
        monkeypatch.setitem(tester_hooks.free_play_config, key, value)
    monkeypatch.setattr(tester_hooks, "free_play_pause", {})
    runtime = FakeRuntime()
    monkeypatch.setattr(server, "camera_capture_runtime", runtime)
    run = tmp_path / "run"
    run.mkdir()
    log = EventLog(run)
    monkeypatch.setattr(server, "get_session_logger", lambda: log)
    monkeypatch.setattr(server, "_shot_sequence_number", 7)
    told = []
    monkeypatch.setattr(
        tester_hooks, "_tell_tester", lambda: told.append(tester_hooks._pause_state())
    )
    return SimpleNamespace(
        client=server.app.test_client(), runtime=runtime, log=log, told=told, run=run
    )


def _run(**body):
    return {"tester_id": TESTER, "run_id": "run-1", **body}


class TestKioskPause:
    def test_a_photo_pause_holds_still_controls_and_the_light_says_wait(self, kiosk):
        response = kiosk.client.post("/api/free-play/pause", json=_run(cause="photo"))
        assert response.status_code == 200, response.get_json()
        assert response.get_json()["paused"]["cause"] == "photo"
        assert kiosk.runtime.calls[0][0] == "hold"
        states = server.SensorStates(ops=None)
        assert tester_hooks._ready_light_states(states).amber_causes == ("taking a face photo",)
        assert kiosk.log.events[0][0] == "free_play_paused"
        assert kiosk.told[-1]["cause"] == "photo"
        assert kiosk.client.get("/api/free-play").get_json()["paused"]["cause"] == "photo"

    def test_a_swing_while_paused_is_not_counted(self, kiosk, monkeypatch):
        results = []
        monkeypatch.setattr(
            server.ready_light, "swing_result", lambda kind, **_k: results.append(kind)
        )
        monkeypatch.setattr(tester_hooks, "tester_setup_required", False)
        swing = SimpleNamespace(ball_speed_mph=120.0, trace_attempt_id=None)
        kiosk.client.post("/api/free-play/pause", json=_run(cause="photo"))
        assert tester_hooks._admit_shot(swing) is False
        assert results == ["paused"]
        kiosk.client.post("/api/free-play/resume", json=_run())
        assert tester_hooks._admit_shot(swing) is True

    def test_the_photo_is_saved_against_the_latest_shot_then_free_play_resumes(self, kiosk):
        kiosk.client.post("/api/free-play/pause", json=_run(cause="photo"))
        response = kiosk.client.post("/api/free-play/photo", json=_run(action="capture"))
        assert response.status_code == 200, response.get_json()
        body = response.get_json()
        assert body["photo"]["shot_number"] == 7
        saved = kiosk.run / body["photo"]["path"]
        assert saved.name == "shot-0007.pgm"
        assert saved.read_bytes().startswith(b"P5\n64 40\n255\n")
        assert body["paused"] is None
        assert kiosk.runtime.calls[-1] == ("release",)
        kinds = [kind for kind, _data in kiosk.log.events]
        assert kinds == ["free_play_paused", "face_photo", "free_play_resumed"]
        photo = kiosk.log.events[1][1]
        assert photo["shot_number"] == 7
        assert photo["path"] == "face-photos/shot-0007.pgm"
        assert photo["exposure_us"] == kiosk.runtime.calls[0][1]

    def test_a_second_photo_of_the_same_shot_keeps_the_first(self, kiosk):
        for _ in range(2):
            kiosk.client.post("/api/free-play/pause", json=_run(cause="photo"))
            kiosk.client.post("/api/free-play/photo", json=_run(action="capture"))
        assert sorted(path.name for path in (kiosk.run / "face-photos").iterdir()) == [
            "shot-0007-2.pgm",
            "shot-0007.pgm",
        ]

    def test_skip_saves_nothing_and_resumes(self, kiosk):
        kiosk.client.post("/api/free-play/pause", json=_run(cause="photo"))
        body = kiosk.client.post("/api/free-play/photo", json=_run(action="skip")).get_json()
        assert body["photo"] is None and body["paused"] is None
        assert not (kiosk.run / "face-photos").exists()
        kinds = [kind for kind, _data in kiosk.log.events]
        assert kinds[-2:] == ["face_photo_skipped", "free_play_resumed"]

    def test_a_photo_needs_a_photo_pause(self, kiosk):
        photo = _run(action="capture")
        assert kiosk.client.post("/api/free-play/photo", json=photo).status_code == 409
        kiosk.client.post("/api/free-play/pause", json=_run(cause="aim"))
        assert kiosk.client.post("/api/free-play/photo", json=photo).status_code == 409

    def test_another_runs_request_changes_nothing(self, kiosk):
        stale = {"tester_id": TESTER, "run_id": "old", "cause": "photo"}
        assert kiosk.client.post("/api/free-play/pause", json=stale).status_code == 409
        assert kiosk.runtime.calls == []

    @pytest.mark.parametrize("cause", ["nap", None])
    def test_only_a_photo_or_the_aim_pauses(self, kiosk, cause):
        response = kiosk.client.post("/api/free-play/pause", json=_run(cause=cause))
        assert response.status_code == 400

    def test_the_camera_view_is_served_only_while_paused(self, kiosk):
        assert kiosk.client.get("/api/free-play/view.jpg").status_code == 409
        kiosk.client.post("/api/free-play/pause", json=_run(cause="aim"))
        response = kiosk.client.get("/api/free-play/view.jpg")
        assert response.status_code == 200
        assert response.mimetype == "image/jpeg"

    def test_a_new_target_line_applies_and_ends_an_aim_pause(self, kiosk, monkeypatch):
        applied = []
        monkeypatch.setattr(
            server, "apply_target_line", lambda line: applied.append(line) or {"revision": 1}
        )
        kiosk.client.post("/api/free-play/pause", json=_run(cause="aim"))
        line = {"status": "set", "yaw_deg": 2.0, "kiosk_offset_deg": -2.0}
        response = kiosk.client.post("/api/free-play/target-line", json=_run(target_line=line))
        assert response.status_code == 200, response.get_json()
        assert applied == [line]
        assert response.get_json()["target_line"] == {"revision": 1}
        assert response.get_json()["paused"] is None

    def test_a_refused_target_line_keeps_the_pause(self, kiosk, monkeypatch):
        def busy(_line):
            raise RuntimeError("a shot is still being processed; try again in a moment")

        monkeypatch.setattr(server, "apply_target_line", busy)
        kiosk.client.post("/api/free-play/pause", json=_run(cause="aim"))
        skipped = _run(target_line={"status": "skipped"})
        assert kiosk.client.post("/api/free-play/target-line", json=skipped).status_code == 409
        assert tester_hooks.free_play_pause["cause"] == "aim"

    def test_the_scoreboard_learns_where_the_tester_page_is(self, kiosk):
        state = kiosk.client.get("/api/free-play", base_url="http://192.0.2.10:8080")
        assert state.get_json()["tester_url"] == "http://192.0.2.10:8765/"


class FakeKiosk:
    """The kiosk's /api/free-play routes as urllib sees them, recording each request."""

    def __init__(self):
        self.requests = []
        self.replies = {}

    def urlopen(self, query, timeout):
        del timeout
        path = query.full_url.split("/api/free-play", 1)[1]
        body = json.loads(query.data) if query.data else None
        self.requests.append((path, body))
        status, reply = self.replies.get(path, (200, {"paused": None}))
        raw = reply if isinstance(reply, bytes) else json.dumps(reply).encode()
        if status >= 400:
            raise urllib.error.HTTPError(query.full_url, status, "refused", {}, io.BytesIO(raw))
        return _Reply(raw)


class _Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


@pytest.fixture
def tester_side(tmp_path, monkeypatch):
    """The tester server's free play, its run recording, and a fake kiosk behind it."""
    (tmp_path / TESTER).mkdir()
    controller = free_play.FreePlayController(tmp_path, Jobs(), Jobs(), kiosk_port=9123)
    controller.begin(TESTER, "run-1", tmp_path / TESTER)
    app = Flask(__name__)
    controller.register(app)
    fake = FakeKiosk()
    monkeypatch.setattr(free_play.urllib.request, "urlopen", fake.urlopen)
    return SimpleNamespace(controller=controller, client=app.test_client(), kiosk=fake)


class TestTesterSidePause:
    def test_the_page_pauses_this_run_for_a_photo(self, tester_side):
        tester_side.kiosk.replies["/pause"] = (200, {"paused": {"cause": "photo"}})
        response = tester_side.client.post(
            "/api/tester/free-play/pause", json={"tester_id": TESTER, "cause": "photo"}
        )
        assert response.status_code == 200, response.get_json()
        assert tester_side.kiosk.requests == [
            ("/pause", {"tester_id": TESTER, "run_id": "run-1", "cause": "photo"})
        ]
        assert tester_side.controller.status()["paused"] == {"cause": "photo"}

    @pytest.mark.parametrize("action", ["capture", "skip"])
    def test_photograph_and_skip_reach_the_kiosk(self, tester_side, action):
        tester_side.controller.run["paused"] = {"cause": "photo"}
        response = tester_side.client.post(
            "/api/tester/free-play/photo", json={"tester_id": TESTER, "action": action}
        )
        assert response.status_code == 200
        assert tester_side.kiosk.requests[0] == (
            "/photo",
            {"tester_id": TESTER, "run_id": "run-1", "action": action},
        )
        assert tester_side.controller.status()["paused"] is None

    def test_the_kiosks_refusal_reaches_the_page(self, tester_side):
        tester_side.kiosk.replies["/photo"] = (409, {"error": "tap Photograph the face first"})
        response = tester_side.client.post(
            "/api/tester/free-play/photo", json={"tester_id": TESTER, "action": "capture"}
        )
        assert response.status_code == 409
        assert response.get_json()["error"] == "tap Photograph the face first"

    def test_another_tester_cannot_pause_this_run(self, tester_side):
        response = tester_side.client.post(
            "/api/tester/free-play/pause", json={"tester_id": "someone-else", "cause": "photo"}
        )
        assert response.status_code == 409
        assert tester_side.kiosk.requests == []

    def test_nothing_reaches_a_kiosk_that_is_not_recording(self, tester_side):
        tester_side.controller.run["phase"] = "packaging"
        response = tester_side.client.post(
            "/api/tester/free-play/resume", json={"tester_id": TESTER}
        )
        assert response.status_code == 409
        assert tester_side.kiosk.requests == []

    def test_the_kiosk_tells_the_page_it_paused_from_the_scoreboard(self, tester_side):
        told = {"tester_id": TESTER, "run_id": "run-1", "paused": {"cause": "photo"}}
        assert tester_side.client.post("/api/tester/free-play/paused", json=told).status_code == 200
        assert tester_side.controller.status()["paused"] == {"cause": "photo"}
        stale = {**told, "run_id": "old", "paused": None}
        assert (
            tester_side.client.post("/api/tester/free-play/paused", json=stale).status_code == 409
        )
        assert tester_side.controller.status()["paused"] == {"cause": "photo"}

    def test_the_camera_view_is_relayed(self, tester_side):
        tester_side.kiosk.replies["/view.jpg"] = (200, b"\xff\xd8jpeg")
        response = tester_side.client.get(f"/api/tester/free-play/view.jpg?tester_id={TESTER}")
        assert response.status_code == 200
        assert response.mimetype == "image/jpeg"
        assert response.data == b"\xff\xd8jpeg"


inputs = _inputs_fixture


@pytest.fixture
def aiming(tmp_path, inputs, monkeypatch):
    """A confirmed box, a confirmed aim, and free play recording on arm5 with its hand-off."""
    app, tester = app_for(tmp_path, inputs, monkeypatch)
    client = app.test_client()
    assert confirm_box(client, tester, "box-1").status_code == 200
    assert confirm_aim(client, tester, "aim-1", [700.0, 380.0]).status_code == 200
    root = tmp_path / "sessions"
    params = ts.TesterParameters(tester, "arm5", "indoors")
    handed = {"tee_m": 2.2, "net_range": {"status": "measured", "net_range_m": 5.2}}
    ts.write_arm_state(root, params, handed_to_swings=handed)
    controller = app.extensions["openflight_free_play"]
    (root / tester).mkdir(parents=True, exist_ok=True)
    controller.begin(tester, "run-1", root / tester, resolution={"arm_id": "arm5"})
    fake = FakeKiosk()
    monkeypatch.setattr(free_play.urllib.request, "urlopen", fake.urlopen)
    return SimpleNamespace(
        client=client, tester=tester, kiosk=fake, controller=controller, root=root
    )


class TestMovingTheAimDuringFreePlay:
    def test_show_pauses_the_kiosk_and_its_camera_shows_the_target(self, aiming):
        aiming.kiosk.replies["/pause"] = (200, {"paused": {"cause": "aim"}})
        body = {"tester_id": aiming.tester, "action": "show"}
        response = aiming.client.post("/api/tester/aim-point", json=body)
        assert response.status_code == 200, response.get_json()
        assert aiming.kiosk.requests[0][0] == "/pause"
        assert aiming.kiosk.requests[0][1]["cause"] == "aim"
        assert response.get_json()["previewing"] is True
        assert response.get_json()["preview_source"] == "kiosk"
        assert aiming.client.get("/api/tester/live").get_json()["owner"] is None

    def test_a_moved_aim_reaches_the_running_kiosk_for_the_next_shot(self, aiming):
        aiming.kiosk.replies["/target-line"] = (
            200,
            {"target_line": {"revision": 1}, "paused": None},
        )
        response = confirm_aim(aiming.client, aiming.tester, "aim-2", [760.0, 380.0])
        assert response.status_code == 200, response.get_json()
        path, sent = aiming.kiosk.requests[-1]
        line = sent["target_line"]
        saved = response.get_json()["target_line"]
        assert path == "/target-line"
        assert line["status"] == "set"
        assert line["yaw_deg"] == pytest.approx(saved["yaw_deg"])
        assert line["kiosk_offset_deg"] == pytest.approx(-saved["yaw_deg"], abs=1e-3)
        assert line["uncertainty"]["parallax_distance_m"] == pytest.approx(3.0)
        assert response.get_json()["free_play"] == {"applied": True, "target_line": {"revision": 1}}
        handed = ts.read_arm_state(aiming.root, aiming.tester, "arm5")["handed_to_swings"]
        assert handed["target_line"]["yaw_deg"] == pytest.approx(saved["yaw_deg"])
        assert not response.get_json()["runs"]["arm5"].get("stale")

    def test_skipping_the_aim_returns_the_kiosk_to_the_unit_boresight(self, aiming):
        body = {"tester_id": aiming.tester, "action": "skip", "request_id": "s"}
        response = aiming.client.post("/api/tester/aim-point", json=body)
        assert response.status_code == 200, response.get_json()
        assert aiming.kiosk.requests[-1][1]["target_line"]["status"] == "skipped"

    def test_a_kiosk_that_refuses_keeps_the_saved_aim_and_says_so(self, aiming):
        busy = {"error": "a shot is still being processed; try again in a moment"}
        aiming.kiosk.replies["/target-line"] = (409, busy)
        response = confirm_aim(aiming.client, aiming.tester, "aim-2", [760.0, 380.0])
        assert response.status_code == 200
        assert response.get_json()["state"] == "confirmed"
        assert response.get_json()["free_play"] == {"applied": False, "error": busy["error"]}

    def test_without_free_play_nothing_is_sent_to_a_kiosk(self, aiming):
        aiming.controller.run["phase"] = "closed"
        response = confirm_aim(aiming.client, aiming.tester, "aim-2", [760.0, 380.0])
        assert response.status_code == 200
        assert aiming.kiosk.requests == []
        assert response.get_json().get("free_play") is None


def _upload(client, name="Trackman Export (1).csv"):
    return client.post(
        "/api/tester/comparator",
        data={"tester_id": TESTER, "file": (io.BytesIO(b"Club,Ball Speed\n7i,120\n"), name)},
        content_type="multipart/form-data",
    )


class TestLaunchMonitorExport:
    def test_before_the_package_it_goes_into_the_package(self, tmp_path):
        client = eligible_app(sessions_root=tmp_path, manager=Jobs()).test_client()
        response = _upload(client)
        assert response.status_code == 200
        assert response.get_json() == {
            "saved": "Trackman_Export_1_.csv",
            "in_package": True,
            "beside": None,
        }
        assert (tmp_path / TESTER / "comparator" / "Trackman_Export_1_.csv").is_file()

    def test_after_the_package_it_is_saved_beside_it_to_send(self, tmp_path):
        send = tmp_path / "send"
        app = eligible_app(sessions_root=tmp_path / "sessions", manager=Jobs(), send_dir=send)
        controller = app.extensions["openflight_free_play"]
        (tmp_path / "sessions" / TESTER).mkdir(parents=True)
        controller.begin(TESTER, "run-1", tmp_path / "sessions" / TESTER)
        bundle = f"{TESTER}-session-bundle-20261021T120000Z.zip"
        controller.run.update(phase="ready", bundle={"name": bundle})

        response = _upload(app.test_client())

        assert response.status_code == 200
        body = response.get_json()
        assert body["in_package"] is False
        beside = send / f"{TESTER}-session-bundle-20261021T120000Z-Trackman_Export_1_.csv"
        assert beside.read_bytes().startswith(b"Club,Ball Speed")
        assert body["beside"] == {
            "bundle": bundle,
            "file": beside.name,
            "folder": session_bundle.display_path(send),
        }
        # a later package still carries it
        assert (tmp_path / "sessions" / TESTER / "comparator" / "Trackman_Export_1_.csv").is_file()
