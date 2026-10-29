"""Model prewarming retains capture-friendly scheduling and parent cleanup."""

from types import SimpleNamespace

import pytest

from openflight.camera import tester_server as ts


@pytest.mark.parametrize("model", [None, "test-model.onnx"])
def test_ball_worker_initializes_scheduling_before_optional_model_warmup(monkeypatch, model):
    calls = []
    monkeypatch.setattr(
        ts.worker_scheduling, "initialize_analysis_worker", lambda: calls.append("scheduling")
    )

    def warm(path):
        calls.append(("warm", path))
        return {"status": "warmed"}

    monkeypatch.setattr(ts.golf_ball_hint, "warm_model", warm)
    ready = SimpleNamespace(put=lambda status: calls.append(("ready", status)))
    ts._initialize_ball_search_worker(model, ready)
    expected = ["scheduling"]
    if model is not None:
        expected += [("warm", model), ("ready", {"status": "warmed"})]
    assert calls == expected
