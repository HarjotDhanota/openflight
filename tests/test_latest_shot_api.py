"""GET /api/shots/latest builds only the shot it returns.

The tester page polls it about once a second for the whole session. Building
every shot's ~49 KB payload to return the last one made each poll cost grow with
the session, inside the kiosk process that also reads the IWR6843 dump (24 Oct:
332 polls in one field session).
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from openflight import server as server_module, shot_metrics
from openflight.clubs import ClubType
from openflight.launch_monitor import Shot


def _shots(count: int):
    return [
        Shot(
            ball_speed_mph=100.0 + number,
            club_speed_mph=80.0,
            timestamp=datetime(2026, 10, 24, 10, 0, number % 60),
            club=ClubType.IRON_7,
            shot_number=number,
        )
        for number in range(1, count + 1)
    ]


def _counting_shot_to_dict(monkeypatch) -> list:
    built = []
    original = server_module.shot_to_dict

    def counting(shot):
        built.append(shot.shot_number)
        return original(shot)

    monkeypatch.setattr(server_module, "shot_to_dict", counting)
    return built


def test_latest_shot_builds_one_payload_however_long_the_session(monkeypatch):
    built = _counting_shot_to_dict(monkeypatch)
    client = server_module.app.test_client()
    for count in (1, 5, 40):
        built.clear()
        shots = _shots(count)
        monkeypatch.setattr(server_module, "monitor", SimpleNamespace(get_shots=lambda s=shots: s))

        body = client.get("/api/shots/latest").get_json()

        assert body["shot"]["shot_number"] == count
        assert built == [count]


def test_latest_shot_payload_is_byte_identical_to_the_whole_session_build(monkeypatch):
    shots = _shots(3)
    monkeypatch.setattr(server_module, "monitor", SimpleNamespace(get_shots=lambda: shots))
    with server_module.app.app_context():
        expected = server_module.jsonify(
            {
                "shot": [server_module.shot_to_dict(shot) for shot in shots][-1],
                "catalog": shot_metrics.catalog(),
            }
        ).get_data()

    response = server_module.app.test_client().get("/api/shots/latest")

    assert response.get_data() == expected


def test_latest_shot_is_null_without_shots_or_monitor(monkeypatch):
    client = server_module.app.test_client()
    monkeypatch.setattr(server_module, "monitor", SimpleNamespace(get_shots=lambda: []))
    assert client.get("/api/shots/latest").get_json()["shot"] is None
    monkeypatch.setattr(server_module, "monitor", None)
    assert client.get("/api/shots/latest").get_json()["shot"] is None
