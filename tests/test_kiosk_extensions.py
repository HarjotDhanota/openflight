"""The kiosk's one registration point for optional extensions."""

import contextlib
import importlib.metadata

import pytest

from openflight import kiosk_extensions as ke


@pytest.fixture
def isolated(monkeypatch):
    """An empty registry for this test; the session's extensions come back after."""
    monkeypatch.setattr(ke, "_extensions", [])
    return ke


class Recorder(ke.KioskExtension):
    def __init__(self, name, *, admit=True, diagnostic=False, provider=None):
        self.name = name
        self.calls = []
        self._admit = admit
        self._diagnostic = diagnostic
        self._provider = provider

    def on_shot_accepted(self, shot):
        self.calls.append(("accepted", shot))

    def admit_shot(self, shot):
        self.calls.append(("admit", shot))
        return self._admit

    def diagnostic_camera(self):
        return self._diagnostic

    def trigger_evidence_provider(self):
        return self._provider

    def ready_light_states(self, states):
        return states + (self.name,)

    def observe_shot_emit(self, shot, event, payload, *, final, update_kind):
        @contextlib.contextmanager
        def observer():
            self.calls.append(("before", event, final, update_kind))
            try:
                yield
            except BaseException as error:
                self.calls.append(("after", type(error).__name__))
                raise
            self.calls.append(("after", None))

        return observer()


def test_a_plain_kiosk_has_no_extensions_and_every_answer_is_the_default(isolated):
    assert ke.registered() == ()
    assert ke.any_true("diagnostic_camera") is False
    assert ke.first("trigger_evidence_provider") is None
    assert ke.all_admit(object()) is True
    assert ke.adjust_ready_light(("states",)) == ("states",)
    with ke.observing_shot_emit(object(), "shot", {}, final=True, update_kind="first"):
        pass


def test_the_base_extension_does_nothing():
    base = ke.KioskExtension()
    assert base.diagnostic_camera() is False and base.iwr_optional() is False
    assert base.trigger_evidence_provider() is None
    assert base.admit_shot(object()) is True
    assert base.ready_light_states("unchanged") == "unchanged"
    config = {}
    base.session_start_config(config)
    assert config == {}
    assert base.observe_shot_emit(object(), "shot", {}, final=False, update_kind="first") is None


def test_registering_twice_keeps_one_and_order_is_kept(isolated):
    first, second = Recorder("a"), Recorder("b")
    ke.register(first)
    ke.register(second)
    ke.register(first)
    assert ke.registered() == (first, second)
    ke.unregister(first)
    assert ke.registered() == (second,)


def test_hooks_reach_every_extension_in_order(isolated):
    first, second = ke.register(Recorder("a")), ke.register(Recorder("b"))
    ke.call_all("on_shot_accepted", "shot-1")
    assert first.calls == [("accepted", "shot-1")] and second.calls == first.calls
    assert ke.adjust_ready_light(()) == ("a", "b")


def test_one_refusal_refuses_the_shot_and_later_extensions_are_not_asked(isolated):
    refuser, later = ke.register(Recorder("a", admit=False)), ke.register(Recorder("b"))
    assert ke.all_admit("shot-1") is False
    assert refuser.calls == [("admit", "shot-1")] and later.calls == []


def test_questions_take_the_first_non_default_answer(isolated):
    provider = object()
    ke.register(Recorder("a"))
    ke.register(Recorder("b", diagnostic=True, provider=provider))
    assert ke.any_true("diagnostic_camera") is True
    assert ke.first("trigger_evidence_provider") is provider


def test_emit_observers_wrap_the_emit_and_see_its_error(isolated):
    observer = ke.register(Recorder("a"))
    with ke.observing_shot_emit("shot", "shot_detected", {}, final=True, update_kind="first"):
        pass
    with pytest.raises(RuntimeError):
        with ke.observing_shot_emit("shot", "shot_updated", {}, final=True, update_kind="late"):
            raise RuntimeError("socket down")
    assert observer.calls == [
        ("before", "shot_detected", True, "first"),
        ("after", None),
        ("before", "shot_updated", True, "late"),
        ("after", "RuntimeError"),
    ]


class _FakeEntryPoint:
    def __init__(self, name, value, loader):
        self.name, self.value, self._loader = name, value, loader

    def load(self):
        return self._loader()


def test_installed_extensions_are_loaded_and_a_broken_one_is_skipped(isolated, monkeypatch):
    good = Recorder("good")

    def broken():
        raise ImportError("missing dependency")

    points = [
        _FakeEntryPoint("good", "pkg.good:extension", lambda: good),
        _FakeEntryPoint("broken", "pkg.broken:extension", broken),
    ]
    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda group: points if group == ke.ENTRY_POINT_GROUP else [],
    )
    assert ke.load_installed() == ["good"]
    assert ke.registered() == (good,)
