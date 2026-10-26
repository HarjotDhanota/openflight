"""The kiosk's Tester button: where the tester page is, and only while it answers (25 Oct)."""

import argparse
import sys

import pytest
from flask import Flask

from openflight import tester_link


class Ping:
    """The tester's /api/tester/ping, as the kiosk's probe sees it."""

    def __init__(self, answer=None):
        self.answer = answer if answer is not None else {"tester": True, "lan": False}
        self.urls = []

    def __call__(self, url, timeout_s):
        self.urls.append((url, timeout_s))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def _client(link):
    app = Flask(__name__)
    tester_link.register(app, link)
    return app.test_client()


def _ask(link, host="localhost:8080", remote="127.0.0.1"):
    response = _client(link).get(
        "/api/tester-page", headers={"Host": host}, environ_base={"REMOTE_ADDR": remote}
    )
    assert response.status_code == 200
    return response.get_json()


def test_a_kiosk_the_tester_did_not_start_never_offers_the_button():
    ping = Ping()
    answer = _ask(tester_link.TesterLink(None, fetch=ping))

    assert answer["available"] is False
    assert answer["url"] is None
    # it never changes for this kiosk: the page can stop asking
    assert answer["final"] is True
    assert ping.urls == []


def test_the_button_goes_to_the_tester_the_way_this_page_reached_the_kiosk():
    ping = Ping()
    answer = _ask(tester_link.TesterLink(9000, fetch=ping))

    assert answer == {
        "available": True,
        "url": "http://localhost:9000/",
        "reason": None,
        "final": False,
    }
    # the probe stays on this unit
    assert ping.urls[0][0] == "http://127.0.0.1:9000/api/tester/ping"


def test_a_phone_on_the_lan_gets_the_unit_address():
    link = tester_link.TesterLink(8765, fetch=Ping({"tester": True, "lan": True}))

    answer = _ask(link, host="192.0.2.10:8080", remote="192.0.2.20")

    assert answer["available"] is True
    assert answer["url"] == "http://192.0.2.10:8765/"


def test_a_phone_is_not_offered_a_tester_page_only_the_unit_can_open():
    link = tester_link.TesterLink(8765, fetch=Ping({"tester": True, "lan": False}))

    answer = _ask(link, host="192.0.2.10:8080", remote="192.0.2.20")

    assert answer["available"] is False
    assert "unit's own screen" in answer["reason"]
    assert answer["final"] is False


def test_an_ipv6_loopback_host_keeps_its_brackets():
    answer = _ask(tester_link.TesterLink(8765, fetch=Ping()), host="[::1]:8080", remote="::1")

    assert answer["url"] == "http://[::1]:8765/"


@pytest.mark.parametrize(
    "failure", [OSError("connection refused"), ValueError("not json"), {"tester": False}, ["x"]]
)
def test_no_button_while_the_tester_does_not_answer(failure):
    answer = _ask(tester_link.TesterLink(8765, fetch=Ping(failure)))

    assert answer["available"] is False
    assert answer["url"] is None
    assert answer["reason"] == "the tester page is not answering"
    # it may come back: keep asking
    assert answer["final"] is False


def test_the_ping_is_cached_briefly_so_polls_stay_cheap():
    ping, clock = Ping(), Clock()
    link = tester_link.TesterLink(8765, fetch=ping, clock=clock)

    _ask(link)
    _ask(link)
    assert len(ping.urls) == 1
    clock.now += tester_link.CACHE_S + 0.1
    ping.answer = OSError("gone")
    assert _ask(link)["available"] is False
    assert len(ping.urls) == 2


def test_configure_replaces_the_port_and_forgets_the_last_answer():
    ping = Ping()
    link = tester_link.TesterLink(8765, fetch=ping)
    _ask(link)

    link.configure(9000)

    assert _ask(link)["url"] == "http://localhost:9000/"
    link.configure(None)
    assert _ask(link)["final"] is True


def test_the_real_probe_has_a_short_timeout(monkeypatch):
    opened = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b'{"tester": true, "lan": false}'

    def urlopen(url, timeout):
        opened.append((url, timeout))
        return Response()

    monkeypatch.setattr(tester_link.urllib.request, "urlopen", urlopen)

    assert tester_link.fetch_json("http://127.0.0.1:8765/api/tester/ping", 1.0) == {
        "tester": True,
        "lan": False,
    }
    assert opened == [("http://127.0.0.1:8765/api/tester/ping", 1.0)]


@pytest.mark.parametrize(
    "argv, port",
    [
        (["--tester-setup-required", "--tester-config-hash", "h"], 8765),
        (
            [
                "--tester-setup-required",
                "--tester-config-hash",
                "h",
                "--tester-control-port",
                "9000",
            ],
            9000,
        ),
        ([], None),
    ],
)
def test_the_kiosk_links_to_the_tester_only_when_the_tester_started_it(monkeypatch, argv, port):
    from openflight import tester_hooks

    for name in ("tester_setup_required", "tester_config_hash", "study_mode_enabled"):
        monkeypatch.setattr(tester_hooks, name, getattr(tester_hooks, name))
    monkeypatch.setattr(tester_hooks, "free_play_config", {})
    previous = tester_hooks.tester_page.port
    parser = argparse.ArgumentParser()
    tester_hooks._add_arguments(parser)
    monkeypatch.setattr(sys, "argv", ["openflight-server", *argv])

    try:
        tester_hooks._configure(parser.parse_args(argv), parser)

        assert tester_hooks.tester_page.port == port
    finally:
        tester_hooks.tester_page.configure(previous)
