"""Phase 3 architectural boundaries for the classical fusion engine."""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest

from silhouette_poc.eval import phase1b
from silhouette_poc.fusion import solver
from silhouette_poc.fusion.pipeline import load_fusion_capture, solve_shot
from silhouette_poc.generator.artifacts import write_shot
from silhouette_poc.generator.synthetic import GeneratorConfig


def test_phase1b_gate_and_fusion_use_the_same_solver_objects():
    assert phase1b.solve_club_state is solver.solve_club_state
    assert phase1b.SilhouetteObservation is solver.SilhouetteObservation
    assert phase1b.ClubState is solver.ClubState
    assert phase1b._silhouette_moments is solver._silhouette_moments
    assert phase1b._backproject_range is solver._backproject_range


def test_fusion_modules_cannot_import_generator_or_truth_sidecar_reader():
    fusion_root = Path(inspect.getfile(solver)).parent

    for path in fusion_root.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imported_modules.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )

        assert not any(name.startswith("silhouette_poc.generator") for name in imported_modules)
        assert "truth.json" not in source
        assert "truth_path" not in source


def test_truth_sidecar_is_unread_and_manifest_cannot_redirect_radar_to_it(tmp_path):
    config = GeneratorConfig(
        root_seed=811,
        club="poc_driver",
        exposure_us=10,
        template_dimension_variation_fraction=0.0,
        photometric_noise_sigma_dn=0.0,
        radar_track_noise_sigma_mm=0.0,
        zero_noise_control=True,
    )
    shot_dir = write_shot(tmp_path / "unread", config)
    (shot_dir / "truth.json").write_text("not-json", encoding="utf-8")

    assert solve_shot(shot_dir).ok

    redirected = write_shot(tmp_path / "redirected", config)
    session_path = redirected / "session.json"
    session = json.loads(session_path.read_text(encoding="utf-8"))
    session["radar_evidence_path"] = "truth.json"
    session_path.write_text(json.dumps(session), encoding="utf-8")

    with pytest.raises(ValueError, match="artifact_path"):
        load_fusion_capture(redirected)
