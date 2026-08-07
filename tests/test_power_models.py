from openflight.power.models import (
    PackReading,
    PowerSnapshot,
    PowerView,
    RailReading,
    SourceReading,
)


def _absent(cls, **kw):
    return cls(status="absent", timestamp=0.0, **kw)


def test_snapshot_holds_three_independent_readers():
    snapshot = PowerSnapshot(
        timestamp=1.0,
        pack=PackReading(status="ok", timestamp=1.0, volts=3.85, percent=62.0),
        rail=_absent(RailReading, ext5v_volts=None, throttled=None),
        source=_absent(SourceReading, state="unknown"),
    )
    assert snapshot.pack.status == "ok"
    assert snapshot.rail.status == "absent"
    assert snapshot.source.state == "unknown"


def test_readings_are_frozen():
    import dataclasses

    import pytest

    reading = PackReading(status="ok", timestamp=1.0, volts=3.85, percent=62.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        reading.volts = 4.0


def test_view_serializes_to_plain_json_types():
    view = PowerView(
        pack_volts=3.85,
        pack_percent=62.0,
        pack_level="ok",
        rail_volts=5.21,
        rail_level="green",
        source="battery",
        runtime_minutes=None,
        shutdown_eligible=False,
        pending_shutdown=None,
        warnings=[],
    )
    payload = view.to_dict()
    assert payload["pack_percent"] == 62.0
    assert payload["pending_shutdown"] is None
    assert payload["warnings"] == []
