"""What a tester reaches during free play: club, hand, aim, face photos, Setup, export."""

import pytest

from openflight.camera import tester_server as ts
from tests.test_free_play import Jobs
from tests.test_tester_server import eligible_app, setup_saw_ball

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
