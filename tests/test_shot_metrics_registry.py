"""The shot-metric catalogue: the metrics every unit measures, and how a model adds its own."""

import importlib.metadata
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from openflight import shot_metrics as sm
from openflight.clubs import ClubType
from openflight.launch_monitor import Shot

CORE_IDS = (
    "ball_speed",
    "launch_vertical",
    "launch_horizontal",
    "spin_rate",
    "spin_axis",
    "smash_factor",
    "ball_range",
    "carry",
    "club_speed",
    "club_path",
    "attack_angle",
)


def test_catalog_cli_matches_the_installed_registry():
    result = subprocess.run(
        [sys.executable, "-I", "-m", "openflight.shot_metrics"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert json.loads(result.stdout) == sm.catalog()


def test_ui_catalog_matches_the_core_registry(core_only):
    path = Path(__file__).resolve().parents[1] / "ui/src/data/metricCatalog.json"

    assert json.loads(path.read_text(encoding="utf-8")) == core_only.catalog()


@pytest.fixture
def core_only(monkeypatch):
    """The catalogue as a unit without any model module has it."""
    core = list(sm._CORE_METRICS)  # pylint: disable=protected-access
    builders = {spec.id: sm._builders[spec.id] for spec in core}  # pylint: disable=protected-access
    builders["carry"] = sm._carry  # pylint: disable=protected-access
    monkeypatch.setattr(sm, "_metrics", core)
    monkeypatch.setattr(sm, "_specs", {spec.id: spec for spec in core})
    monkeypatch.setattr(sm, "_builders", builders)
    monkeypatch.setattr(sm, "_modules_loaded", True)
    return sm


def test_the_core_catalogue_is_the_metrics_every_unit_measures(core_only):
    assert sm.METRIC_IDS == CORE_IDS
    assert [entry["id"] for entry in sm.catalog()["metrics"]] == list(CORE_IDS)


def test_a_plain_shot_has_every_core_metric_and_a_table_carry(core_only):
    shot = Shot(
        ball_speed_mph=140.0,
        club_speed_mph=95.0,
        timestamp=datetime(2026, 10, 19),
        club=ClubType.IRON_7,
    )
    records = shot.to_dict()["metrics"]

    assert tuple(records) == CORE_IDS
    assert records["ball_speed"]["status"] == sm.MEASURED
    assert records["carry"]["status"] == sm.ESTIMATED
    assert records["carry"]["source"] == sm.TABLE_CARRY_SOURCE


def test_registered_metrics_go_after_their_anchor_in_order(core_only):
    first = (sm.MetricSpec("first", "First", sm.BALL, ""), lambda shot: {})
    second = (sm.MetricSpec("second", "Second", sm.BALL, ""), lambda shot: {})
    sm.register_metrics([first, second], after="ball_speed")
    sm.register_metrics([first], after="club_speed")  # already there: left as it is

    assert sm.METRIC_IDS[:4] == ("ball_speed", "first", "second", "launch_vertical")
    assert sm.METRIC_IDS.count("first") == 1
    assert sm.metric_spec("second").label == "Second"


def test_a_model_may_replace_a_core_builder_but_not_invent_a_metric(core_only):
    sm.replace_builder("carry", lambda shot: sm.build_record("carry", 123.0, sm.CALCULATED))
    shot = Shot(ball_speed_mph=140.0, timestamp=datetime(2026, 10, 19))

    assert shot.to_dict()["metrics"]["carry"]["value"] == 123.0
    with pytest.raises(KeyError):
        sm.replace_builder("no_such_metric", lambda shot: {})


class _EntryPoint:
    def __init__(self, name, loader):
        self.name, self.value, self._loader = name, f"pkg.{name}", loader

    def load(self):
        return self._loader()


def test_model_modules_load_once_and_a_broken_one_is_skipped(core_only, monkeypatch):
    loaded = []

    def broken():
        raise ImportError("missing dependency")

    points = [_EntryPoint("good", lambda: loaded.append("good")), _EntryPoint("bad", broken)]
    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda group: points if group == sm.ENTRY_POINT_GROUP else [],
    )
    monkeypatch.setattr(sm, "_modules_loaded", False)

    assert sm.METRIC_IDS == CORE_IDS
    sm.catalog()
    assert loaded == ["good"]
