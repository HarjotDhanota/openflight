"""Every golf metric reaches every place the owner looks (V-wiring).

One parametrized test per place, over the owner's metric list
(``tests.every_metric.EXPECTED_METRIC_IDS``). A metric missing from any place
fails here by name. ``docs/reference/metric-coverage.md`` is the matrix these
tests pin; the browser places (kiosk tiles, tester page, session-review page)
are covered by the vitest and Playwright specs it names.
"""

from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from openflight import pipeline_check, server as server_module, session_bundle, shot_metrics
from openflight.server import shot_to_dict
from openflight.session_logger import SessionLogger
from openflight.session_review import attempts_csv, build_session_review, report_markdown
from tests.every_metric import EXPECTED_METRIC_IDS, LABELS, PENDING_METRIC_IDS, fusion_shot
from tests.test_session_review import _tester_tree

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def shot():
    return fusion_shot()


@pytest.fixture(scope="module")
def payload(shot):
    return shot_to_dict(shot)


@pytest.fixture(scope="module")
def logged(shot, tmp_path_factory):
    logger = SessionLogger(log_dir=tmp_path_factory.mktemp("log"), enabled=True)
    logger.start_session(mode="rolling-buffer", trigger_type="sound")
    assert logger.log_shot(shot)
    text = logger.session_path.read_text(encoding="utf-8")
    lines = [json.loads(line) for line in text.splitlines()]
    return next(line for line in lines if line["type"] == "shot_detected")


@pytest.fixture(scope="module")
def review(shot, tmp_path_factory):
    root = _tester_tree(tmp_path_factory.mktemp("review"))
    session = next((root / "t1" / "arm5" / "paired" / "run-01").glob("session_*.jsonl"))
    record = {"type": "shot_detected", "ts": "shot-5", **shot.to_dict(), "shot_number": 5}
    with session.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    return build_session_review(root, "t1", analysis={})


@pytest.fixture(scope="module")
def bundle(shot, tmp_path_factory):
    """A session bundle built as the Pi builds it: raw logs plus the stored review."""
    root = _tester_tree(tmp_path_factory.mktemp("bundle"))
    session = next((root / "t1" / "arm5" / "paired" / "run-01").glob("session_*.jsonl"))
    record = {"type": "shot_detected", "ts": "shot-5", **shot.to_dict(), "shot_number": 5}
    with session.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    built = build_session_review(root, "t1", analysis={})
    analysis = root / "t1" / "analysis"
    (analysis / "session_review.json").write_text(json.dumps(built), encoding="utf-8")
    (analysis / "report.md").write_text(report_markdown(built), encoding="utf-8")
    made = session_bundle.build_bundle(root, "t1", viewer=None, provenance={"software": "test"})
    with zipfile.ZipFile(made["path"]) as archive:
        members = {name: archive.read(name).decode("utf-8") for name in archive.namelist()}
    stored = json.loads(members["t1/analysis/session_review.json"])
    log = next(text for name, text in members.items() if name.endswith(".jsonl"))
    logged = [json.loads(line) for line in log.splitlines() if '"shot_number": 5' in line]
    return {"review": stored, "report": members["t1/analysis/report.md"], "log": logged[-1]}


def _review_attempt(review):
    return next(a for a in review["attempts"] if a["attempt_id"] == "session-one:5")


def _assert_record(record, metric_id):
    assert record is not None, f"{metric_id} is missing"
    assert record["status"] in LABELS, f"{metric_id}: label {record['status']!r}"
    if record["value"] is None:
        assert record["status"] == "not_measured" and record["reason"], metric_id


@pytest.mark.parametrize("metric_id", EXPECTED_METRIC_IDS)
def test_shot_carries_the_metric(shot, metric_id):
    _assert_record(shot.to_dict()["metrics"].get(metric_id), metric_id)


@pytest.mark.parametrize("metric_id", EXPECTED_METRIC_IDS)
def test_kiosk_socket_payload_carries_the_metric(payload, metric_id):
    _assert_record(payload["metrics"].get(metric_id), metric_id)


@pytest.mark.parametrize("metric_id", EXPECTED_METRIC_IDS)
def test_latest_shot_api_carries_the_metric(shot, monkeypatch, metric_id):
    monkeypatch.setattr(server_module, "monitor", SimpleNamespace(get_shots=lambda: [shot]))
    body = server_module.app.test_client().get("/api/shots/latest").get_json()
    _assert_record(body["shot"]["metrics"].get(metric_id), metric_id)
    assert metric_id in [entry["id"] for entry in body["catalog"]["metrics"]]


@pytest.mark.parametrize("metric_id", EXPECTED_METRIC_IDS)
def test_session_log_shot_detected_carries_the_metric(logged, metric_id):
    _assert_record(logged["metrics"].get(metric_id), metric_id)


@pytest.mark.parametrize("metric_id", EXPECTED_METRIC_IDS)
def test_session_review_carries_the_metric(review, metric_id):
    rows = {row["key"]: row for row in _review_attempt(review)["kiosk_metrics"]}
    _assert_record(rows.get(metric_id), metric_id)
    assert rows[metric_id]["label"] == shot_metrics.SPECS[metric_id].label
    assert metric_id in review["kiosk_metric_label_counts"]


@pytest.mark.parametrize("metric_id", EXPECTED_METRIC_IDS)
def test_session_review_report_and_csv_carry_the_metric(review, metric_id):
    label = shot_metrics.SPECS[metric_id].label
    assert f"| {label} |" in report_markdown(review)
    rows = list(csv.DictReader(io.StringIO(attempts_csv(review))))
    assert any(
        row["attempt_id"] == "session-one:5" and row["metric"] == f"kiosk.{metric_id}"
        for row in rows
    )


@pytest.mark.parametrize("metric_id", EXPECTED_METRIC_IDS)
def test_session_bundle_carries_the_metric(bundle, metric_id):
    rows = {row["key"]: row for row in _review_attempt(bundle["review"])["kiosk_metrics"]}
    _assert_record(rows.get(metric_id), metric_id)
    _assert_record(bundle["log"]["metrics"].get(metric_id), metric_id)
    assert f"| {shot_metrics.SPECS[metric_id].label} |" in bundle["report"]


@pytest.mark.parametrize("metric_id", EXPECTED_METRIC_IDS)
def test_pipeline_check_reports_the_metric(payload, metric_id):
    rows = pipeline_check.kiosk_metric_stages(shot_metrics.records(payload), upstream={})
    row = next((row for row in rows if row["id"] == f"kiosk:{metric_id}"), None)
    assert row is not None
    if metric_id in PENDING_METRIC_IDS:
        assert row["status"] == pipeline_check.FAIL and row["cause"] == "PENDING"


@pytest.mark.parametrize("metric_id", EXPECTED_METRIC_IDS)
def test_the_ui_catalog_lists_the_metric(metric_id):
    path = REPO / "ui" / "src" / "data" / "metricCatalog.shotModels.json"
    catalog = json.loads(path.read_text("utf-8"))
    assert catalog == shot_metrics.catalog(), f"regenerate {path.name}"
    assert metric_id in [entry["id"] for entry in catalog["metrics"]]


def test_the_review_label_vocabulary_has_every_label(review):
    assert review["label_vocabulary"] == list(LABELS)
    assert "model_geometry" in report_markdown(review)


def test_the_tester_page_reads_the_latest_shot_and_the_catalog():
    page = (REPO / "ui" / "public" / "tester.html").read_text(encoding="utf-8")
    assert "/api/shots/latest" in page
    assert "data-kiosk-metric" in page


def test_the_session_review_page_renders_the_kiosk_metrics():
    page = (REPO / "ui" / "public" / "session-review.html").read_text(encoding="utf-8")
    assert "kiosk_metrics" in page
    assert "label_vocabulary" in page


def test_the_coverage_matrix_names_every_metric():
    matrix = (REPO / "docs" / "reference" / "metric-coverage.md").read_text(encoding="utf-8")
    for metric_id in EXPECTED_METRIC_IDS:
        assert f"`{metric_id}`" in matrix, metric_id


def test_the_documented_command_regenerates_the_ui_catalog():
    result = subprocess.run(
        [sys.executable, "-m", "openflight.shot_metrics"],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,
    )
    assert json.loads(result.stdout) == shot_metrics.catalog()
