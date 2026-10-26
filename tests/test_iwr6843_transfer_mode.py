"""The transfer setting: what the operator asks for, gated by what the radar supports."""

from __future__ import annotations

import pytest

from openflight.iwr6843.transfer_mode import (
    NOT_CHECKED_REASON,
    TransferMode,
    help_lists_two_round,
)

HELP = "help\r\nsensorStart   Configure\r\noverview   x\r\ncells   y\r\nrelease   z\r\nDone\r\nl3dump:/>"


def test_help_listing_names_every_two_round_command():
    assert help_lists_two_round(HELP) == (True, ())
    assert help_lists_two_round(HELP.replace("cells", "cp").encode()) == (False, ("cells",))
    assert help_lists_two_round(b"") == (False, ("overview", "cells", "release"))


def test_two_round_is_effective_only_once_the_radar_supports_it():
    mode = TransferMode("two_round")
    status = mode.status()
    assert (status.requested, status.effective, status.available) == ("two_round", "full", False)
    assert status.reason == NOT_CHECKED_REASON
    mode.set_support(False, "no commands")
    assert mode.effective() == "full" and mode.status().reason == "no commands"
    mode.set_support(True)
    assert mode.effective() == "two_round" and mode.status().reason is None
    mode.request("full")
    assert mode.effective() == "full"
    assert mode.status().to_dict()["requested"] == "full"
    with pytest.raises(ValueError, match="transfer"):
        mode.request("fast")


def test_the_last_fallback_is_kept_for_the_ui():
    mode = TransferMode("two_round")
    mode.note_fallback("the ring was released")
    assert mode.status().last_fallback == "the ring was released"
    mode.note_fallback(None)
    assert mode.status().last_fallback is None
