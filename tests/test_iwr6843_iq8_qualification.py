"""IQ8 qualification stays replayable, provenance-bound, and non-promotional."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from openflight.iwr6843 import iq8_qualification as qualification
from openflight.iwr6843.dump import (
    SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED,
    SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED,
    pack_dump,
    parse_dump,
)
from scripts.iwr6843 import qualify_iq8 as cli

ROOT = Path(__file__).resolve().parents[1]


def _config(path: Path, capture_format: str) -> None:
    scale = "iq8Scale 128\n" if capture_format == "iq8" else ""
    path.write_text(
        "\n".join(
            [
                "dfeDataOutputMode 1",
                "channelCfg 15 7 0",
                "adcCfg 2 1",
                "profileCfg 0 60.0 7 3 38 0 0 100 1 128 4000 0 0 30",
                "chirpCfg 0 0 0 0 0 0 0 1",
                "chirpCfg 1 1 0 0 0 0 0 2",
                "chirpCfg 2 2 0 0 0 0 0 4",
                "frameCfg 0 2 12 0 2 1 0",
                f"captureFormat {capture_format}",
                scale.rstrip(),
                "phaseCaptureCfg 20 7 1 20 7 1 20 7 20 2 1",
                "sensorStart",
            ]
        ).replace("\n\n", "\n")
        + "\n",
        encoding="utf-8",
    )


def _pair_files(tmp_path: Path) -> tuple[Path, Path]:
    rng = np.random.default_rng(84)
    cube = 900 * (rng.standard_normal((4, 36, 4, 7)) + 1j * rng.standard_normal((4, 36, 4, 7)))
    cube[:, 0, 0, 0] = 16_200 + 16_200j
    common = {
        "n_tx": 3,
        "version": 6,
        "frame_period_us": 2000,
        "range_bin_starts": (20, 20, 20, 20),
        "range_bin_counts": (7, 7, 7, 7),
        "frame_time_offsets_us": (0, 2000, 4000, 6000),
    }
    iq16 = tmp_path / "pair-iq16.l3dump"
    iq8 = tmp_path / "pair-iq8.l3dump"
    (tmp_path / "source-cube.bin").write_bytes(cube.astype(np.complex128).tobytes())
    iq16.write_bytes(pack_dump(cube, sample_fmt=SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED, **common))
    iq8.write_bytes(pack_dump(cube, sample_fmt=SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED, **common))
    return iq16, iq8


def _manifest(
    tmp_path: Path,
    iq16: Path,
    iq8: Path,
    iq16_config: Path,
    iq8_config: Path,
) -> Path:
    source = tmp_path / "source-cube.bin"
    iq16_encoder = tmp_path / "iq16-encoder.txt"
    iq8_encoder = tmp_path / "iq8-encoder.txt"
    iq16_encoder.write_text("test pack_dump IQ16 encoder v1\n", encoding="utf-8")
    iq8_encoder.write_text("test pack_dump IQ8 encoder v1\n", encoding="utf-8")
    profile_sha256 = qualification._profile_contract(iq16_config)["measurement_profile_sha256"]
    assert (
        qualification._profile_contract(iq8_config)["measurement_profile_sha256"] == profile_sha256
    )
    iq16_metadata, _ = parse_dump(iq16.read_bytes())
    iq8_metadata, _ = parse_dump(iq8.read_bytes())
    cadence_sha256 = qualification._canonical_sha256(qualification._cadence_identity(iq16_metadata))
    assert (
        qualification._canonical_sha256(qualification._cadence_identity(iq8_metadata))
        == cadence_sha256
    )
    path = tmp_path / "pairs.json"
    path.write_text(
        json.dumps(
            {
                "schema": qualification.MANIFEST_SCHEMA,
                "experiment_id": "synthetic-contract-test",
                "pairs": [
                    {
                        "pair_id": "same-cube-001",
                        "match_basis": "same_raw_cube",
                        "comparison_provenance": {
                            "source": {
                                "kind": "raw_cube",
                                "id": "synthetic-cube-001",
                                "path": source.name,
                                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                            },
                            "profile_identity": {
                                "id": "synthetic-3tx-2ms-4f",
                                "sha256": profile_sha256,
                            },
                            "cadence_identity": {
                                "id": "synthetic-4f-2ms",
                                "sha256": cadence_sha256,
                            },
                            "transformations": {
                                "iq16": {
                                    "operation": "encode_from_raw_cube",
                                    "tool": "openflight.pack_dump",
                                    "version": "test-v1",
                                    "provenance_path": iq16_encoder.name,
                                    "provenance_sha256": hashlib.sha256(
                                        iq16_encoder.read_bytes()
                                    ).hexdigest(),
                                },
                                "iq8": {
                                    "operation": "encode_from_raw_cube",
                                    "tool": "openflight.pack_dump",
                                    "version": "test-v1",
                                    "provenance_path": iq8_encoder.name,
                                    "provenance_sha256": hashlib.sha256(
                                        iq8_encoder.read_bytes()
                                    ).hexdigest(),
                                },
                            },
                        },
                        "iq16": {
                            "path": iq16.name,
                            "ball_speed_mph": 100.0,
                            "club_speed_mph": 75.0,
                            "club": "7i",
                        },
                        "iq8": {
                            "path": iq8.name,
                            "ball_speed_mph": 100.0,
                            "club_speed_mph": 75.0,
                            "club": "7i",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _report(tmp_path: Path, monkeypatch=None) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    iq16, iq8 = _pair_files(tmp_path)
    iq16_config = tmp_path / "iq16.cfg"
    iq8_config = tmp_path / "iq8.cfg"
    _config(iq16_config, "iq16")
    _config(iq8_config, "iq8")
    manifest = _manifest(tmp_path, iq16, iq8, iq16_config, iq8_config)
    if monkeypatch is not None:
        calls = []

        def fake_estimators(raw, _calibration, capture, _runtime):
            calls.append(len(raw))
            accepted = len(raw) == iq16.stat().st_size
            return {
                "ball": {
                    "status": "accepted" if accepted else "rejected_quantized",
                    "accepted": accepted,
                    "launch_angle_deg": 18.0 if accepted else 18.25,
                    "horizontal_status": "accepted",
                    "horizontal_deg": 1.0,
                },
                "club": {
                    "status": "accepted",
                    "accepted": True,
                    "path_deg": 2.0 if accepted else 2.1,
                    "attack_angle_status": "accepted" if accepted else "rejected_quantized",
                    "candidate_attack_angle_deg": -3.0 if accepted else None,
                },
            }

        monkeypatch.setattr(qualification, "_run_estimators", fake_estimators)
    return qualification.build_qualification_report(
        manifest,
        calibration_path=ROOT / "config/iwr6843_calibration_reference.json",
        iq16_config_path=iq16_config,
        iq8_config_path=iq8_config,
        tee_range_m=1.575,
        net_range_m=4.064,
        repo_root=ROOT,
    )


def test_report_compares_transport_estimators_and_exact_provenance(tmp_path, monkeypatch):
    report = _report(tmp_path, monkeypatch)

    pair = report["pairs"][0]
    assert pair["status"] == "compared"
    assert (
        pair["iq8"]["transport"]["sample_payload_bytes"] * 2
        == pair["iq16"]["transport"]["sample_payload_bytes"]
    )
    assert pair["comparison"]["transport"]["theoretical_uart_s_saved"] > 0
    assert pair["comparison"]["estimators"]["status_changes"]["ball.status"] == {
        "iq16": "accepted",
        "iq8": "rejected_quantized",
    }
    assert pair["comparison"]["estimators"]["metric_deltas_iq8_minus_iq16"][
        "ball.launch_angle_deg"
    ] == pytest.approx(0.25)
    assert len(report["pairs"][0]["iq16"]["sha256"]) == 64
    assert report["provenance"]["runtime_inputs_sha256"]
    assert report["provenance"]["software"]["source_content_manifest_sha256"]
    assert report["qualification"]["status"] == "not_hardware_qualified"
    assert report["qualification"]["evidence_status"] == "complete"
    assert report["qualification"]["hardware_qualified"] is False
    assert report["qualification"]["production_default_may_change"] is False
    launch = report["aggregate"]["same_event_metric_parity"]["launch_angle_deg"]
    assert launch["declared_pairs"] == 1
    assert launch["both_available_rate"] == 1.0
    assert launch["paired_iq8_minus_iq16"]["mae"] == pytest.approx(0.25)
    attack = report["aggregate"]["same_event_metric_parity"]["attack_angle_deg"]
    assert attack["declared_pairs"] == 1
    assert attack["iq8_available_count"] == 0
    assert attack["both_available_rate"] == 0.0
    assert attack["status_agreement_rate"] == 0.0
    assert attack["paired_iq8_minus_iq16"]["count"] == 0
    assert report["aggregate"]["transport_totals"]["sample_payload_reduction_fraction"] == 0.5


def test_synthetic_pair_runs_the_complete_current_estimator_path(tmp_path):
    report = _report(tmp_path)

    pair = report["pairs"][0]
    assert pair["status"] == "compared", pair.get("error")
    for representation in ("iq16", "iq8"):
        estimators = pair[representation]["estimators"]
        assert estimators["ball"]["estimator"] == "lcmf_v1"
        assert estimators["ball"]["status"]
        assert estimators["club"]["status"]
        assert "processing_s" not in pair[representation]


def test_profile_mismatch_is_preserved_as_failed_evidence(tmp_path, monkeypatch):
    iq16, iq8 = _pair_files(tmp_path)
    iq16_config = tmp_path / "iq16.cfg"
    iq8_config = tmp_path / "iq8.cfg"
    _config(iq16_config, "iq16")
    _config(iq8_config, "iq8")
    manifest = _manifest(tmp_path, iq16, iq8, iq16_config, iq8_config)
    iq8_config.write_text(
        iq8_config.read_text(encoding="utf-8").replace(
            "frameCfg 0 2 12 0 2 1 0", "frameCfg 0 2 12 0 3 1 0"
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        qualification,
        "_repository_identity",
        lambda _root: {"source_content_manifest_sha256": "f" * 64},
    )

    report = qualification.build_qualification_report(
        manifest,
        calibration_path=ROOT / "config/iwr6843_calibration_reference.json",
        iq16_config_path=iq16_config,
        iq8_config_path=iq8_config,
        tee_range_m=1.575,
        net_range_m=4.064,
        repo_root=ROOT,
    )

    assert report["pairs"][0]["status"] == "error"
    assert "frame_period_us" in report["pairs"][0]["error"]
    assert report["qualification"]["criteria"][0]["passed"] is False
    assert report["qualification"]["evidence_status"] == "incomplete"


def test_manifest_refuses_incomplete_or_unverified_provenance(tmp_path):
    iq16, iq8 = _pair_files(tmp_path)
    iq16_config = tmp_path / "iq16.cfg"
    iq8_config = tmp_path / "iq8.cfg"
    _config(iq16_config, "iq16")
    _config(iq8_config, "iq8")
    manifest = _manifest(tmp_path, iq16, iq8, iq16_config, iq8_config)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    transform = payload["pairs"][0]["comparison_provenance"]["transformations"]["iq8"]
    transform["provenance_sha256"] = "0" * 64
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="does not match source bytes"):
        qualification.load_pair_manifest(manifest)


def test_same_event_derived_encoding_names_the_captured_source(tmp_path):
    iq16, iq8 = _pair_files(tmp_path)
    iq16_config = tmp_path / "iq16.cfg"
    iq8_config = tmp_path / "iq8.cfg"
    _config(iq16_config, "iq16")
    _config(iq8_config, "iq8")
    manifest = _manifest(tmp_path, iq16, iq8, iq16_config, iq8_config)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    pair = payload["pairs"][0]
    pair["match_basis"] = "same_event_derived_encoding"
    pair["comparison_provenance"]["source"]["kind"] = "recorded_event"
    transforms = pair["comparison_provenance"]["transformations"]
    transforms["iq16"]["operation"] = "captured_event"
    transforms["iq8"].update({"operation": "derived_encoding", "source_representation": "iq16"})
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    assert qualification.load_pair_manifest(manifest)["pairs"][0]["match_basis"] == (
        "same_event_derived_encoding"
    )
    transforms["iq8"]["source_representation"] = "iq8"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="must name its source representation"):
        qualification.load_pair_manifest(manifest)


def test_declared_cadence_must_match_both_parsed_dumps(tmp_path, monkeypatch):
    iq16, iq8 = _pair_files(tmp_path)
    iq16_config = tmp_path / "iq16.cfg"
    iq8_config = tmp_path / "iq8.cfg"
    _config(iq16_config, "iq16")
    _config(iq8_config, "iq8")
    manifest = _manifest(tmp_path, iq16, iq8, iq16_config, iq8_config)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["pairs"][0]["comparison_provenance"]["cadence_identity"]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        qualification,
        "_repository_identity",
        lambda _root: {"source_content_manifest_sha256": "f" * 64},
    )

    report = qualification.build_qualification_report(
        manifest,
        calibration_path=ROOT / "config/iwr6843_calibration_reference.json",
        iq16_config_path=iq16_config,
        iq8_config_path=iq8_config,
        tee_range_m=1.575,
        net_range_m=4.064,
        repo_root=ROOT,
    )

    assert report["pairs"][0]["status"] == "error"
    assert "declared cadence identity" in report["pairs"][0]["error"]


def test_evidence_hash_is_relocation_and_clock_independent(tmp_path, monkeypatch):
    monkeypatch.setattr(
        qualification,
        "_repository_identity",
        lambda _root: {"source_content_manifest_sha256": "f" * 64},
    )
    first = _report(tmp_path / "first", monkeypatch)
    second = _report(tmp_path / "second", monkeypatch)

    assert first == second
    assert first["evidence_sha256"] == second["evidence_sha256"]


def test_distinct_swings_compare_each_estimate_only_to_its_reference(tmp_path, monkeypatch):
    iq16, iq8 = _pair_files(tmp_path)
    iq16_config = tmp_path / "iq16.cfg"
    iq8_config = tmp_path / "iq8.cfg"
    _config(iq16_config, "iq16")
    _config(iq8_config, "iq8")
    manifest = _manifest(tmp_path, iq16, iq8, iq16_config, iq8_config)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    pair = payload["pairs"][0]
    pair["match_basis"] = "reference_matched_distinct_swings"
    pair.pop("comparison_provenance")
    match_provenance = tmp_path / "match-review.json"
    match_provenance.write_text('{"reviewed":true}\n', encoding="utf-8")
    pair["reference_match"] = {
        "id": "match-001",
        "method": "interleaved_same-club_speed-bin",
        "provenance_path": match_provenance.name,
        "provenance_sha256": hashlib.sha256(match_provenance.read_bytes()).hexdigest(),
    }
    references = {
        "iq16": ("event-16", "reference-16", (17.5, 0.5, 1.0, -2.0)),
        "iq8": ("event-8", "reference-8", (18.0, 0.75, 1.4, -2.5)),
    }
    for representation, (event_id, reference_id, values) in references.items():
        reference_path = tmp_path / f"{reference_id}.json"
        reference_path.write_text(json.dumps({"id": reference_id}), encoding="utf-8")
        capture = pair[representation]
        capture["event_id"] = event_id
        capture["reference"] = {
            "id": reference_id,
            "source": "independent-launch-monitor",
            "provenance_path": reference_path.name,
            "provenance_sha256": hashlib.sha256(reference_path.read_bytes()).hexdigest(),
            "metrics": dict(
                zip(
                    (
                        "launch_angle_deg",
                        "horizontal_deg",
                        "club_path_deg",
                        "attack_angle_deg",
                    ),
                    values,
                    strict=True,
                )
            ),
        }
    pair["iq8"]["ball_speed_mph"] = 110.0
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    def fake_estimators(_raw, _calibration, capture, _runtime):
        candidate = capture["ball_speed_mph"] == 110.0
        return {
            "ball": {
                "status": "accepted",
                "accepted": True,
                "launch_angle_deg": 18.25 if candidate else 18.0,
                "horizontal_status": "accepted",
                "horizontal_deg": 1.0,
            },
            "club": {
                "status": "accepted",
                "accepted": True,
                "path_deg": 2.1 if candidate else 2.0,
                "attack_angle_status": "accepted",
                "candidate_attack_angle_deg": -3.0,
            },
        }

    monkeypatch.setattr(qualification, "_run_estimators", fake_estimators)
    monkeypatch.setattr(
        qualification,
        "_repository_identity",
        lambda _root: {"source_content_manifest_sha256": "f" * 64},
    )
    report = qualification.build_qualification_report(
        manifest,
        calibration_path=ROOT / "config/iwr6843_calibration_reference.json",
        iq16_config_path=iq16_config,
        iq8_config_path=iq8_config,
        tee_range_m=1.575,
        net_range_m=4.064,
        repo_root=ROOT,
    )

    estimator_comparison = report["pairs"][0]["comparison"]["estimators"]
    assert estimator_comparison["method"] == "independent_error_to_each_event_reference"
    assert "metric_deltas_iq8_minus_iq16" not in estimator_comparison
    launch = estimator_comparison["metrics"]["launch_angle_deg"]
    assert launch["iq16"]["signed_error"] == pytest.approx(0.5)
    assert launch["iq8"]["signed_error"] == pytest.approx(0.25)
    aggregate = report["aggregate"]["reference_metric_parity"]["launch_angle_deg"]
    assert aggregate["declared_pairs"] == 1
    assert aggregate["iq16_error_to_reference"]["mae"] == pytest.approx(0.5)
    assert aggregate["iq8_error_to_reference"]["mae"] == pytest.approx(0.25)


def test_cli_writes_deterministic_incomplete_evidence_and_returns_nonzero(tmp_path, monkeypatch):
    output = tmp_path / "rejected.json"
    monkeypatch.setattr(cli, "tx_order_from_config", lambda _path: "normal")
    monkeypatch.setattr(
        cli,
        "build_qualification_report",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad match basis")),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "qualify_iq8.py",
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--out",
            str(output),
            "--tee-m",
            "1.575",
        ],
    )

    assert cli.main() == 2
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["qualification"]["evidence_status"] == "incomplete"
    assert report["qualification"]["hardware_qualified"] is False
    assert report["failure"] == {
        "code": "input_contract_rejected",
        "exception_type": "ValueError",
    }
    assert report == qualification.build_incomplete_report(
        "input_contract_rejected", ValueError("different local message")
    )


def test_cli_refuses_to_replace_an_existing_output(tmp_path, monkeypatch):
    output = tmp_path / "existing.json"
    output.write_text("preserve me\n", encoding="utf-8")
    monkeypatch.setattr(cli, "tx_order_from_config", lambda _path: "normal")
    monkeypatch.setattr(
        cli,
        "build_qualification_report",
        lambda *_args, **_kwargs: {
            "aggregate": {"compared_pairs": 1, "declared_pairs": 1},
            "qualification": {
                "status": "not_hardware_qualified",
                "evidence_status": "complete",
            },
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "qualify_iq8.py",
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--out",
            str(output),
            "--tee-m",
            "1.575",
        ],
    )

    assert cli.main() == 3
    assert output.read_text(encoding="utf-8") == "preserve me\n"
