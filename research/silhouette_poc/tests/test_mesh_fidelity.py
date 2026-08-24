import hashlib
import json
import struct
import zipfile
from pathlib import Path

import numpy as np
import pytest

from silhouette_poc.eval.mesh_fidelity import (
    FIDELITY_ARMS,
    build_fidelity_bundle,
    build_fidelity_cells,
    decide_fidelity_verdict,
    render_fidelity_markdown,
)
from silhouette_poc.eval.run_mesh_fidelity import OUTPUT_FILENAMES
from silhouette_poc.generator.mesh_truth import (
    ACTIVE_MESH_SOURCES,
    CATEGORY_DIMENSIONS_MM,
    MESH_SOURCES,
    TriangleMesh,
    admit_mesh,
    detect_face_plane,
    face_detection_record,
    geometry_hash,
    load_binary_stl,
    load_gltf_archive,
    normalize_clubhead,
    rasterize_projected_triangles,
    render_mesh_mask,
)
from silhouette_poc.generator.synthetic import GeneratorConfig, generate_shot
from silhouette_poc.meshes.download_meshes import import_local_stl, validate_source_metadata


def test_frozen_sources_retire_art_scene_driver_and_keep_local_cad_iron_active():
    assert set(MESH_SOURCES) == {"poc_driver", "poc_7iron"}
    driver = MESH_SOURCES["poc_driver"]
    assert driver.uid == "978d0740dc514c8695bbb02f4083f0e3"
    assert driver.status == "retired_source_quality"
    assert "art scene" in driver.status_reason.lower()
    assert set(ACTIVE_MESH_SOURCES) == {"poc_7iron"}
    assert CATEGORY_DIMENSIONS_MM["poc_driver"] == {
        "width": 118.0,
        "height": 60.0,
        "depth": 112.0,
    }
    iron = MESH_SOURCES["poc_7iron"]
    assert iron.uid == "grabcad:titleist-7-iron-golf-club-1:690cb-right-handed"
    assert iron.license_spdx == "LicenseRef-GrabCAD-Local-Research-Only"
    assert not iron.downloadable


def test_downloader_refuses_a_source_or_license_change():
    source = MESH_SOURCES["poc_driver"]
    valid = {
        "uid": source.uid,
        "name": source.name,
        "isDownloadable": True,
        "license": {"label": "CC Attribution", "url": source.license_url},
        "user": {"displayName": source.author},
    }

    validate_source_metadata(source, valid)
    invalid = {**valid, "license": {"label": "Editorial", "url": "https://example.test"}}
    with pytest.raises(ValueError, match="license"):
        validate_source_metadata(source, invalid)


def test_numpy_triangle_rasterizer_preserves_nonconvex_silhouette():
    # Two triangles leave the upper-right quadrant empty; a convex hull would
    # incorrectly fill it.
    vertices_uv = np.array([[1, 1], [6, 1], [1, 6], [3, 3], [6, 3], [3, 6]], dtype=float)
    faces = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int32)

    mask = rasterize_projected_triangles(vertices_uv, faces, width=8, height=8)

    assert mask.dtype == np.bool_
    assert mask[1, 1]
    assert mask[3, 4]
    assert not mask[5, 5]
    assert not mask[0, 0]


def test_mesh_projection_uses_existing_a0_camera():
    mesh = TriangleMesh(
        vertices_local_mm=np.array(
            [[0.0, -20.0, -10.0], [0.0, 20.0, -10.0], [0.0, 20.0, 10.0], [0.0, -20.0, 10.0]]
        ),
        faces=np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32),
        source_uid="unit-square",
        source_sha256="0" * 64,
    )

    mask = render_mesh_mask(mesh, np.zeros(3), roll_rad=0.0, preset_name="A0")

    assert mask.shape == (200, 320)
    # 0.656 px/mm makes the projected 40x20 mm face about 26x13 pixels.
    assert 300 < np.count_nonzero(mask) < 400


def test_gltf_loader_applies_node_transform(tmp_path: Path):
    positions = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype="<f4")
    indices = np.array([0, 1, 2], dtype="<u2")
    payload = positions.tobytes() + indices.tobytes()
    gltf = {
        "asset": {"version": "2.0"},
        "buffers": [{"uri": "mesh.bin", "byteLength": len(payload)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": positions.nbytes},
            {"buffer": 0, "byteOffset": positions.nbytes, "byteLength": indices.nbytes},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
            {"bufferView": 1, "componentType": 5123, "count": 3, "type": "SCALAR"},
        ],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "nodes": [{"mesh": 0, "translation": [2, 3, 4]}],
        "scenes": [{"nodes": [0]}],
        "scene": 0,
    }
    archive = tmp_path / "model.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("scene.gltf", json.dumps(gltf))
        bundle.writestr("mesh.bin", payload)

    mesh = load_gltf_archive(archive, source_uid="fixture", source_sha256="1" * 64)

    np.testing.assert_allclose(mesh.vertices_local_mm, positions + [2, 3, 4])
    np.testing.assert_array_equal(mesh.faces, [[0, 1, 2]])


def test_binary_stl_loader_validates_count_and_decodes_triangles(tmp_path: Path):
    path = tmp_path / "iron.stl"
    header = b"OpenFlight binary STL fixture".ljust(80, b"\0")
    triangle = struct.pack(
        "<12fH",
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0,
    )
    path.write_bytes(header + struct.pack("<I", 1) + triangle)

    mesh = load_binary_stl(path, source_uid="fixture", expected_sha256=None)

    np.testing.assert_allclose(mesh.vertices_local_mm, [[0, 0, 0], [1, 0, 0], [0, 1, 0]])
    np.testing.assert_array_equal(mesh.faces, [[0, 1, 2]])
    assert mesh.source_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_local_stl_import_fails_closed_on_hash_mismatch(tmp_path: Path):
    path = tmp_path / "iron.stl"
    path.write_bytes(b"not the registered source")

    with pytest.raises(ValueError, match="SHA-256"):
        import_local_stl(path, tmp_path / "assets", expected_sha256="0" * 64)


def _box_mesh(*, rotation: np.ndarray | None = None) -> TriangleMesh:
    vertices = np.array(
        [
            [-10, -40, -20],
            [-10, 40, -20],
            [-10, 40, 20],
            [-10, -40, 20],
            [10, -40, -20],
            [10, 40, -20],
            [10, 40, 20],
            [10, -40, 20],
        ],
        dtype=float,
    )
    faces = np.array(
        [
            [0, 1, 2],
            [0, 2, 3],
            [4, 6, 5],
            [4, 7, 6],
            [0, 4, 5],
            [0, 5, 1],
            [1, 5, 6],
            [1, 6, 2],
            [2, 6, 7],
            [2, 7, 3],
            [3, 7, 4],
            [3, 4, 0],
        ],
        dtype=np.int32,
    )
    if rotation is not None:
        vertices = vertices @ rotation.T + np.array([17.0, -9.0, 31.0])
    return TriangleMesh(vertices, faces, "fixture", "2" * 64)


def test_face_plane_geometrically_anchors_a_rotated_metric_mesh():
    angle = np.radians(37.0)
    tilt = np.radians(-23.0)
    rotate_z = np.array(
        [[np.cos(angle), -np.sin(angle), 0], [np.sin(angle), np.cos(angle), 0], [0, 0, 1]]
    )
    rotate_y = np.array(
        [[np.cos(tilt), 0, np.sin(tilt)], [0, 1, 0], [-np.sin(tilt), 0, np.cos(tilt)]]
    )
    rotation = rotate_z @ rotate_y
    mesh = _box_mesh(rotation=rotation)

    detection = detect_face_plane(mesh)

    assert abs(float(detection.normal_source @ rotation[:, 0])) > 0.99
    assert detection.coherent_area_mm2 == pytest.approx(3_200.0)
    np.testing.assert_allclose(detection.face_span_mm, [80.0, 40.0], atol=1e-6)


def test_metric_normalization_preserves_scale_and_anchors_face_to_positive_x():
    angle = np.radians(31.0)
    rotation = np.array(
        [[np.cos(angle), -np.sin(angle), 0], [np.sin(angle), np.cos(angle), 0], [0, 0, 1]]
    )
    mesh = _box_mesh(rotation=rotation)

    normalized = normalize_clubhead(
        mesh,
        {"width": 80.0, "height": 40.0, "depth": 20.0},
        source_units_mm=True,
    )
    normalized_face = detect_face_plane(normalized)
    extents = np.ptp(normalized.vertices_local_mm, axis=0)

    np.testing.assert_allclose(extents, [20.0, 80.0, 40.0], atol=1e-8)
    assert float(normalized_face.normal_source @ np.array([1.0, 0.0, 0.0])) > 0.99
    record = face_detection_record(normalized_face)
    assert set(record) == {
        "normal",
        "coherent_area_mm2",
        "face_span_mm",
        "triangle_count",
    }
    assert record["normal"] == pytest.approx([1.0, 0.0, 0.0])


def test_admission_rejects_disconnected_scene_props_and_hash_dedupes_reordering():
    mesh = _box_mesh()
    prop = np.array([[200.0, 0.0, 0.0], [201.0, 0.0, 0.0], [200.0, 1.0, 0.0]])
    contaminated = TriangleMesh(
        np.vstack([mesh.vertices_local_mm, prop]),
        np.vstack([mesh.faces, [[8, 9, 10]]]),
        mesh.source_uid,
        mesh.source_sha256,
    )

    rejected = admit_mesh(
        contaminated,
        category_dimensions_mm={"width": 80.0, "height": 40.0, "depth": 20.0},
        source_units_mm=True,
    )
    reordered = TriangleMesh(
        mesh.vertices_local_mm[::-1],
        (len(mesh.vertices_local_mm) - 1 - mesh.faces[:, ::-1])[::-1],
        mesh.source_uid,
        mesh.source_sha256,
    )

    assert not rejected.accepted
    assert "component_count" in rejected.reasons
    assert geometry_hash(mesh) == geometry_hash(reordered)


def test_f1_grid_is_frozen_and_paired_with_phase4b():
    cells = build_fidelity_cells()

    assert FIDELITY_ARMS == ("analytic_truth", "mesh_truth")
    assert len(cells) == 8
    assert all(cell.n == 200 for cell in cells)
    assert all(cell.seeds == tuple(range(20260824, 20261024)) for cell in cells)
    assert all(cell.frame_count == 10 and cell.pre_trigger_count == 8 for cell in cells)
    assert all(cell.template_variation_fraction == 0.01 for cell in cells)
    paired = {(cell.club, cell.candidate): set() for cell in cells}
    for cell in cells:
        paired[(cell.club, cell.candidate)].add(cell.truth_arm)
    assert all(arms == set(FIDELITY_ARMS) for arms in paired.values())
    assert OUTPUT_FILENAMES == {
        "json": "results_f1_mesh_fidelity.json",
        "markdown": "RESULTS_F1_MESH_FIDELITY.md",
    }


@pytest.mark.parametrize(
    ("mesh_rate", "analytic_rate", "median", "p90", "expected"),
    [
        (0.79, 1.0, 2.0, 4.0, "TEMPLATE_COLLAPSE"),
        (0.89, 1.0, 2.0, 4.0, "TEMPLATE_COLLAPSE"),
        (0.90, 1.0, 11.0, 15.0, "ACCURACY_NO_GO"),
        (0.90, 1.0, 2.0, 4.0, "PASS"),
    ],
)
def test_honest_outcome_rules_are_executable(mesh_rate, analytic_rate, median, p90, expected):
    rows = []
    for club in ("poc_driver", "poc_7iron"):
        for candidate in ("strobed_10us", "ambient_500us"):
            rows.extend(
                [
                    {
                        "truth_arm": "analytic_truth",
                        "club": club,
                        "candidate": candidate,
                        "solve_rate": analytic_rate,
                        "impact_error_mm_median": 1.0,
                        "impact_error_mm_p90": 2.0,
                    },
                    {
                        "truth_arm": "mesh_truth",
                        "club": club,
                        "candidate": candidate,
                        "solve_rate": mesh_rate,
                        "impact_error_mm_median": median,
                        "impact_error_mm_p90": p90,
                    },
                ]
            )

    assert decide_fidelity_verdict(rows)["verdict"] == expected


def test_generator_requires_a_pinned_mesh_asset_for_mesh_truth(tmp_path: Path):
    config = GeneratorConfig(
        root_seed=1,
        club="poc_driver",
        exposure_us=500,
        truth_geometry="mesh",
        mesh_asset_root=str(tmp_path),
    )

    with pytest.raises(FileNotFoundError, match="download_meshes"):
        generate_shot(config)


def test_fidelity_report_names_gate_and_complete_metrics():
    rows = []
    for arm in FIDELITY_ARMS:
        for club in ("poc_driver", "poc_7iron"):
            for candidate in ("strobed_10us", "ambient_500us"):
                rows.append(
                    {
                        "truth_arm": arm,
                        "club": club,
                        "candidate": candidate,
                        "n": 200,
                        "solve_rate": 1.0,
                        "impact_error_mm_median": 1.0,
                        "impact_error_mm_p90": 2.0,
                        "offset_error_mm_median": 0.1,
                        "offset_error_mm_p90": 0.2,
                        "height_error_mm_median": -0.1,
                        "height_error_mm_p90": 0.3,
                        "silhouette_iou_median": 0.9,
                        "silhouette_iou_p10": 0.8,
                        "fit_residual_px_median": 2.1,
                        "fit_residual_px_p90": 3.2,
                        "failure_categories": {"visibility_club": 2},
                        "config_hash": "abc",
                        "seeds": tuple(range(20260824, 20261024)),
                    }
                )
    bundle = build_fidelity_bundle(rows, {"sources": [{"source_uid": "fixture"}]})

    report = render_fidelity_markdown(bundle)

    assert "**F1 GATE: PASS**" in report
    assert "Pre-outcome source amendment" in report
    assert "Unicode fraktur" in report
    assert "Median mm" in report and "p90 mm" in report
    assert "Signed horizontal median/p90" in report
    assert "Fit residual median/p90" in report
    assert "Visibility failures" in report
    assert "Rejection taxonomy" in report
    assert "visibility_club:2" in report
    assert bundle["cells"][0]["visibility_failure_count"] == 2
    assert bundle["evaluation_hash"] in report
