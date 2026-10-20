"""The Pi trace across one shot's first, first-final and late results."""

import threading

import pytest

from openflight import server as server_module
from openflight.server import on_shot_detected
from tests.pi_trace_helpers import installed_recorder, read_marks
from tests.test_server import OnShotDetectedHelpers


@pytest.fixture
def recorder(tmp_path):
    """The process-wide Pi trace recorder for one test (pi_trace_helpers)."""
    yield from installed_recorder(tmp_path)


class TestTracedShots(OnShotDetectedHelpers):
    def test_late_iwr_result_is_traced_as_a_later_revision_of_the_same_attempt(
        self, monkeypatch, recorder
    ):
        """R-F5: first, first-final and late update are revisions 1-3 of one attempt."""
        release_dump = threading.Event()

        def late_iwr(shot):
            assert release_dump.wait(5.0)
            shot.launch_angle_vertical = 14.0
            shot.launch_angle_vertical_source = "radar"
            return server_module._ShotEnrichmentResult(iwr6843_ms=21000.0)

        run = self._late_iwr_session(monkeypatch, late_iwr)
        attempt = recorder.open_ops_attempt()
        on_shot_detected(run.shot)
        assert run.first_result_published.wait(2.0)
        release_dump.set()
        for thread in run.background_threads:
            thread.join(timeout=2.0)

        tags = [payload["trace"] for _event, payload in run.emitted]
        assert [(t["attempt_id"], t["revision"], t["final"], t["update_kind"]) for t in tags] == [
            (attempt, 1, False, "first"),
            (attempt, 2, True, "first"),
            (attempt, 3, True, "late_update"),
        ]
        assert not any(t["core_eligible"] for t in tags), "no distance before W13"
        marks = [m for m in read_marks(recorder) if m["attempt_id"] == attempt]
        names = [m["name"] for m in marks]
        assert names.count("server_emit_invoked") == names.count("server_emit_returned") == 3
        watchdog = [m for m in marks if m["name"] == "server_enrichment_watchdog_fired"]
        assert [m["reason"] for m in watchdog] == ["enrichment_deadline"]
        applied = [m for m in marks if m["name"] == "server_late_update_applied"]
        assert [(m["state"], m["first_result_reason"]) for m in applied] == [
            ("late_result_applied", "enrichment_deadline")
        ]
        assert names.index("server_enrichment_watchdog_fired") < names.index(
            "server_late_update_applied"
        )
        late_invoked = [m for m in marks if m["name"] == "server_emit_invoked"][2]
        assert late_invoked["core"]["launch_angle_vertical"] == 14.0
        assert names[:3] == ["ops_wait_started", "server_callback_started", "server_shot_accepted"]
