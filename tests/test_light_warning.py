"""Which ladder rungs the tester's light supports, told before any swing (13 Oct).

light-audit.md section 4 (and B3): indoors every rung under 300 us at 1:1 was a pure
"light required" outcome; the setup's measured light predicts each rung's ball level
at the gain ceiling, against the swings' own 20 DN ball gate.
"""

import pytest

from openflight.camera import study_ladder as sl, tester_server as ts

# retest 2 at 1.32 m: the ball core 53 DN above black at 300 us x 12
INDOOR_1_32_M = 53.0 / (300.0 * 12.0)


def test_the_640x400_mode_factor_is_the_measured_two():
    """Measured from the paired gain screens in harjot-indoor-test-1/-2 and
    Outdoors-test-5/-8: 640x400 gives 1.96-2.04x the 1280x800 signal per pixel at the
    same exposure and gain (coordinator, 13 Oct). It supersedes the audit's 'same rows'."""
    assert sl.MODE_SIGNAL_FACTOR == {"arm5": 1.0, "arm6": 2.0}
    assert sl.ARM6_SIGNAL_FACTOR == 2.0


def test_each_rung_predicts_its_ball_at_the_gain_ceiling():
    support = {item["rung_id"]: item for item in sl.rung_light_support(INDOOR_1_32_M)}

    assert support["full-300"]["predicted_ball_dn"] == pytest.approx(53.0)
    assert support["full-200"]["predicted_ball_dn"] == pytest.approx(35.33, abs=0.01)
    assert support["full-150"]["supported"] is True
    # the audit's table: 100 us reads 17.7 DN at 1.32 m and needs 1.1x today's light
    assert support["full-100"]["supported"] is False
    assert support["full-100"]["light_needed_factor"] == pytest.approx(20.0 / 17.67, abs=0.01)
    # 640x400 at the same exposure collects twice the signal per pixel
    assert support["half-300"]["predicted_ball_dn"] == pytest.approx(106.0)
    assert support["half-75"]["predicted_ball_dn"] == pytest.approx(26.5)
    assert support["half-75"]["supported"] is True


def test_the_warning_names_the_supported_rungs_and_what_the_rest_need():
    warning = sl.light_warning(INDOOR_1_32_M, source="setup_static_lock")

    assert warning["status"] == "measured"
    assert warning["supported"][:3] == ["full-300", "full-200", "full-150"]
    assert "full-100" in warning["unsupported"]
    assert warning["gate_dn"] == sl.BALL_MIN_SIGNAL_DN
    assert "300" in warning["message"] and "more light" in warning["message"]


def test_without_a_measured_light_the_warning_says_so_and_never_guesses():
    warning = sl.light_warning(None, source=None)

    assert warning["status"] == "not_measured"
    assert warning["rungs"] == []
    assert warning["supported"] == [] and warning["unsupported"] == []


def test_the_setup_lock_carries_the_light_index_to_the_warning():
    lock = {
        "applied_exposure_us": 3600,
        "applied_gain": 1.0,
        "observation": {"signal_above_floor_dn": 53.0},
    }

    assert ts.lock_light_index(lock) == pytest.approx(53.0 / 3600.0)
    assert ts.lock_light_index({**lock, "observation": {"light_index_dn_per_us_gain": 0.02}}) == (
        pytest.approx(0.02)
    )
    assert ts.lock_light_index(None) is None


def test_the_640x400_ladder_basis_uses_the_measured_mode_factor():
    class Candidate:
        source_group = "camera"
        candidate_id = "camera-x-arm5"
        evidence = {
            "result": {"status": "selected"},
            "static_exposure_lock": {"applied_exposure_us": 3600.0, "applied_gain": 2.0},
        }

    class Solution:
        candidates = [Candidate()]

    basis = ts.ladder_gain_basis(Solution(), "arm6", {})

    assert basis["source"] == "setup_lock_scaled"
    assert basis["signal_us"] == pytest.approx(3600.0 * 2.0 / 2.0)
    assert basis["mode_signal_factor"] == 2.0


@pytest.fixture
def flow_inputs(tmp_path):
    from tests.test_tester_tee_range_flow import make_inputs  # noqa: PLC0415

    return make_inputs(tmp_path)


def test_the_finished_setup_and_the_ladder_show_the_light_warning(
    tmp_path, flow_inputs, monkeypatch
):
    from tests.test_tester_tee_range_flow import app_for, drive  # noqa: PLC0415

    app, tester = app_for(tmp_path, flow_inputs, monkeypatch)
    client = app.test_client()
    drive(client, tester)

    display = client.get("/api/tester/tee-range", query_string={"tester_id": tester}).get_json()[
        "display"
    ]
    ladder = client.get("/api/tester/ladder", query_string={"tester_id": tester}).get_json()

    assert display["light_warning"]["status"] == "measured"
    assert display["light_warning"]["supported"]
    assert ladder["light_warning"] == display["light_warning"]


# --- 13 Oct: shorter rungs to the sensor minimum, and what the kiosk would use ----------

from openflight.camera import swing_exposure as sx  # noqa: E402


def _limits(minimum):
    return sx.SensorLimits(
        exposure_min_us=minimum, exposure_max_us=8_000, gain_min=1.0, gain_max=15.9375
    )


def test_rungs_under_the_recorded_sensor_minimum_are_not_offered():
    below = sl.rungs_below_sensor_minimum({"arm5": 9, "arm6": 4})

    assert set(below) == {"full-7", "full-5", "half-3"}
    assert "9 us" in below["full-7"]


def test_without_a_recorded_minimum_only_the_proven_rungs_are_offered():
    """Both modes took a 10 us request in full sun (30 Sept); shorter waits for the read."""
    below = sl.rungs_below_sensor_minimum({"arm5": None, "arm6": None})

    assert set(below) == {"full-7", "full-5", "half-7", "half-5", "half-3"}
    assert "not been read" in below["half-7"]


def test_the_warning_marks_the_rung_the_kiosk_would_use_in_today_s_room():
    warning = sl.light_warning(
        INDOOR_1_32_M,
        source="setup_static_lock",
        sensor_limits={"arm5": _limits(9), "arm6": _limits(4)},
    )

    full = warning["kiosk_would_use"]["arm5"]
    assert (full["exposure_us"], full["rung_id"]) == (294, "full-300")
    assert full["text"] == "kiosk would use: 1280×800 · 294 µs × gain 15.94"
    marked = [item["rung_id"] for item in warning["rungs"] if item["kiosk_would_use"]]
    assert "full-300" in marked
    assert warning["below_sensor_minimum"].keys() == {"full-7", "full-5", "half-3"}


def test_in_sun_the_kiosk_mark_sits_on_the_shortest_rung_the_sensor_allows():
    sun = 0.6 * 234.0 / 7.0  # 7 us x 1 put the ball at 0.6 of clip (30 Sept)

    warning = sl.light_warning(
        sun, source="setup_static_lock", sensor_limits={"arm5": _limits(9), "arm6": _limits(4)}
    )

    full = warning["kiosk_would_use"]["arm5"]
    half = warning["kiosk_would_use"]["arm6"]
    assert (full["exposure_us"], full["gain"], full["rung_id"]) == (9, 1.0, "full-10")
    # 640x400 doubles the light: at the 4 us minimum the ball sits near 0.69 of clip
    assert half["exposure_us"] == 4 and half["status"] == "ball_driven"
    assert half["rung_id"] == "half-5"


def test_without_the_sensor_minimum_the_kiosk_mark_says_so():
    warning = sl.light_warning(
        INDOOR_1_32_M, source="setup_static_lock", sensor_limits={"arm5": None, "arm6": None}
    )

    assert warning["kiosk_would_use"]["arm5"]["status"] == "needs_sensor_minimum"
    assert "rung_id" not in warning["kiosk_would_use"]["arm5"]


def test_the_ladder_shows_the_kiosk_mark_from_the_recorded_limits(
    tmp_path, flow_inputs, monkeypatch
):
    from tests.test_tester_tee_range_flow import app_for, drive  # noqa: PLC0415

    limits_path = tmp_path / "camera-sensor-limits.json"
    sx.record_sensor_limits(limits_path, 1280, 800, _limits(9))
    sx.record_sensor_limits(limits_path, 640, 400, _limits(4))
    app, tester = app_for(tmp_path, flow_inputs, monkeypatch, sensor_limits_path=limits_path)
    client = app.test_client()
    drive(client, tester)

    ladder = client.get("/api/tester/ladder", query_string={"tester_id": tester}).get_json()

    marks = ladder["light_warning"]["kiosk_would_use"]
    assert marks["arm5"]["text"].startswith("kiosk would use: 1280×800 · ")
    assert marks["arm5"]["sensor_min_us"] == 9
    assert ladder["sensor_limits"]["arm6"]["exposure_min_us"] == 4
    assert set(ladder["below_sensor_minimum"]) == {"full-7", "full-5", "half-3"}
