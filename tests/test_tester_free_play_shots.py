"""Free play's per-shot records on the kiosk: run id, exposure decision, kept OPS numbers."""

from pathlib import Path
from types import SimpleNamespace

from openflight import server as server_module, tester_hooks
from openflight.server import on_shot_detected
from tests.test_server import OnShotDetectedHelpers


class TestFreePlayShots(OnShotDetectedHelpers):
    def test_free_play_camera_failure_keeps_ops_numbers_and_persists_the_shot(self, monkeypatch):
        emitted, session_log = self._record_finalization(monkeypatch)
        workers = []

        class FailedCamera:
            @staticmethod
            def trigger_rejection_for_shot(_impact):
                return None

            @staticmethod
            def trigger_evidence_for_shot(_impact):
                raise OSError("camera disconnected")

            @staticmethod
            def capture_for_shot(_impact, **_kwargs):
                raise OSError("camera disconnected")

        monkeypatch.setattr(
            tester_hooks, "free_play_config", {"enabled": True, "run_id": "free-run"}
        )
        monkeypatch.setattr(tester_hooks, "tester_setup_required", True)
        monkeypatch.setattr(server_module, "camera_capture_runtime", FailedCamera())
        monkeypatch.setattr(server_module, "iwr6843_runtime", None)
        monkeypatch.setattr(server_module, "shot_enrichment_queue", self._enrichment_queue())
        monkeypatch.setattr(server_module, "shot_enrichment_task", None)
        monkeypatch.setattr(server_module, "log_session_error", lambda *_a, **_kw: None)
        monkeypatch.setattr(
            server_module.socketio,
            "start_background_task",
            lambda target, *_a, **_kw: workers.append(target) or object(),
        )
        shot = self._shot()
        on_shot_detected(shot)
        assert emitted[0][1]["shot"]["ball_speed_mph"] == 150
        workers[0]()
        self._wait_for_finalization_coordinator_idle()
        assert session_log.shots[0]["ball_speed_mph"] == 150
        assert session_log.shots[0]["free_play"]["run_id"] == "free-run"
        record = session_log.shots[0]["exposure_decision"]
        assert record["status"] == "unavailable"
        # the camera could not answer: no decision is invented, the gap is named
        assert record["decision"] is None
        assert record["decision_scope"] is None
        assert "auto_exposure_status" in record["decision_reason"]

    def test_free_play_shot_without_a_clip_records_the_live_decision_labelled(self, monkeypatch):
        emitted, session_log = self._record_finalization(monkeypatch)
        workers = []
        live = {
            "status": "tracking",
            "setting": {"exposure_us": 250, "gain": 3.0},
            "recent_decisions": [{"exposure_us": 1}] * 50,
        }

        class NoClipCamera:
            settings = SimpleNamespace(lens={"lens_id": "6mm", "focal_mm": 6.0})

            @staticmethod
            def auto_exposure_status():
                return live

            @staticmethod
            def trigger_rejection_for_shot(_impact):
                return None

            @staticmethod
            def capture_for_shot(_impact, **_kwargs):
                return None

        monkeypatch.setattr(
            tester_hooks, "free_play_config", {"enabled": True, "run_id": "free-run"}
        )
        monkeypatch.setattr(server_module, "camera_capture_runtime", NoClipCamera())
        monkeypatch.setattr(server_module, "iwr6843_runtime", None)
        monkeypatch.setattr(server_module, "shot_enrichment_queue", self._enrichment_queue())
        monkeypatch.setattr(server_module, "shot_enrichment_task", None)
        monkeypatch.setattr(
            server_module.socketio,
            "start_background_task",
            lambda target, *_a, **_kw: workers.append(target) or object(),
        )
        on_shot_detected(self._shot())
        live["setting"]["exposure_us"] = 900  # a later change must not rewrite the shot
        workers[0]()
        self._wait_for_finalization_coordinator_idle()
        assert emitted
        record = session_log.shots[0]["exposure_decision"]
        assert record["status"] == "unavailable"
        assert record["reason"] == "no matched camera capture"
        assert record["decision_scope"] == "live_at_shot"
        assert record["decision"]["setting"]["exposure_us"] == 250
        assert "recent_decisions" not in record["decision"]
        assert record["lens"] == {"lens_id": "6mm", "focal_mm": 6.0}
        assert record["lens_scope"] == "kiosk_run"

    def test_free_play_records_the_matched_capture_decision_instead_of_live_controls(
        self, monkeypatch
    ):
        _emitted, session_log = self._record_finalization(monkeypatch)
        # the clip metadata as capture_runtime writes it: ``settings`` is the startup
        # snapshot and carries no lens; the lens is its own top-level key
        metadata = {
            "auto_exposure": {
                "setting": {"exposure_us": 100, "gain": 2},
                "reading": {"median_dn": 60},
            },
            "settings": {
                "width": 1280,
                "height": 800,
                "fps": 120,
                "exposure_us": 8,
                "gain": 1.0,
                "auto_exposure": True,
            },
            "settings_scope": "capture_startup",
            "capture_mode": {"context_status": "uniform"},
            "lens": {
                "lens_id": "6mm",
                "effective_lens_id": "6mm",
                "label": "6 mm f/2.0",
                "focal_mm": 6.0,
                "f_number": 2.0,
            },
        }
        capture = SimpleNamespace(
            trigger_timestamp=100,
            path=Path("camera-001"),
            metadata=metadata,
            valid=True,
            error=None,
            sequence=1,
        )
        runtime = SimpleNamespace(
            capture_for_shot=lambda *_a, **_kw: capture,
            auto_exposure_status=lambda: {"setting": {"exposure_us": 200}},
        )
        monkeypatch.setattr(tester_hooks, "free_play_config", {"enabled": True})
        monkeypatch.setattr(server_module, "camera_capture_runtime", runtime)
        monkeypatch.setattr(server_module, "iwr6843_runtime", None)
        monkeypatch.setattr(server_module, "_load_camera_capture_archive", lambda *_: None)
        monkeypatch.setattr(server_module, "_attach_camera_replay", lambda *_: None)
        monkeypatch.setattr(server_module, "_fuse_camera_measurements", lambda *_: None)
        shot = self._shot()
        server_module._enrich_shot_from_optional_hardware(shot)
        metadata["auto_exposure"]["setting"]["exposure_us"] = 300
        assert shot.free_play_exposure["decision"]["setting"]["exposure_us"] == 100
        assert shot.free_play_exposure["decision_scope"] == "clip"
        assert shot.free_play_exposure["lens"]["lens_id"] == "6mm"
        assert shot.free_play_exposure["lens_scope"] == "clip"
        metadata["lens"]["lens_id"] = "stock"  # the record is the clip's own copy
        assert shot.free_play_exposure["lens"]["lens_id"] == "6mm"
        shot.free_play_run_id = "free-run"
        assert shot.to_dict()["exposure_decision"]["lens"]["focal_mm"] == 6.0
        assert len(session_log.camera_captures) == 1
