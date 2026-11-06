"""Training-data failures must be explicit before datasets are combined."""

import ast
import hashlib
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts.vision.audit_golf_training import audit_dataset, main, require_valid_dataset


def dataset(root):
    root.mkdir()
    config = {"names": ["golf-ball"], "nc": 1, "train": "train/images", "val": "valid/images"}
    (root / "data.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    for split in ["train", "valid"]:
        (root / split / "images").mkdir(parents=True)
        (root / split / "labels").mkdir()
        (root / split / "images/ball.jpg").write_bytes(split.encode())
        (root / split / "labels/ball.txt").write_text("0 0.5 0.5 0.2 0.2\n")
    return root


def codes(result):
    return {row["code"] for row in result["errors"]}


def file_hashes(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def test_valid_audit_and_explicit_negative_leave_dataset_unchanged(tmp_path):
    root = dataset(tmp_path / "data")
    (root / "train/labels/ball.txt").write_text("")
    before = file_hashes(root)
    result = require_valid_dataset(root)
    assert result["ok"]
    assert result["splits"]["train"]["explicit_empty_labels"] == 1
    assert result["splits"]["val"]["boxes"] == 1
    assert file_hashes(root) == before


@pytest.mark.parametrize("names", [["hole", "golf-ball"], ["hole"], {1: "golf-ball"}])
def test_foreign_or_multiclass_schema_is_not_silently_renamed(tmp_path, names):
    root = dataset(tmp_path / "data")
    config = yaml.safe_load((root / "data.yaml").read_text())
    config["names"] = names
    (root / "data.yaml").write_text(yaml.safe_dump(config))
    assert "expected_single_golf_ball_class" in codes(audit_dataset(root))
    with pytest.raises(ValueError, match="preflight failed"):
        require_valid_dataset(root)


def test_single_class_dict_and_dataset_relative_path_are_supported(tmp_path):
    root = dataset(tmp_path / "data")
    config = yaml.safe_load((root / "data.yaml").read_text())
    config.update(names={0: "golf_ball"}, path=".")
    (root / "data.yaml").write_text(yaml.safe_dump(config))
    assert audit_dataset(root)["ok"]


def test_roboflow_parent_relative_paths_match_the_actual_dataset(tmp_path):
    root = dataset(tmp_path / "data")
    config = yaml.safe_load((root / "data.yaml").read_text())
    config.update(train="../train/images", val="../valid/images")
    (root / "data.yaml").write_text(yaml.safe_dump(config))
    assert audit_dataset(root)["ok"]


def test_existing_parent_directory_takes_precedence_over_relative_fallback(tmp_path):
    root = dataset(tmp_path / "data")
    for split in ["train", "valid"]:
        images = tmp_path / split / "images"
        images.mkdir(parents=True)
        labels = images.parent / "labels"
        labels.mkdir()
        (images / "parent.jpg").write_bytes(("parent-" + split).encode())
        (labels / "parent.txt").write_text("0 0.5 0.5 0.2 0.2\n")
    config = yaml.safe_load((root / "data.yaml").read_text())
    config.update(train="../train/images", val="../valid/images")
    (root / "data.yaml").write_text(yaml.safe_dump(config))
    result = audit_dataset(root)
    assert result["ok"]
    images = [row["file"] for row in result["files"] if row["kind"] == "image"]
    assert set(images) == {
        str(tmp_path / split / "images/parent.jpg") for split in ["train", "valid"]
    }


@pytest.mark.parametrize(
    "row,code",
    [
        ("1 0.5 0.5 0.2 0.2", "expected_class_zero"),
        ("0.5 0.5 0.5 0.2 0.2", "expected_class_zero"),
        ("0 nan 0.5 0.2 0.2", "invalid_detection_row"),
        ("0 0.5 0.5 0.2", "invalid_detection_row"),
        ("0 0.5 0.5 0.2 0.2 0.3", "invalid_detection_row"),
        ("0 1.1 0.5 0.2 0.2", "invalid_normalized_box"),
        ("0 0.5 0.5 0 0.2", "invalid_normalized_box"),
        ("0 0.5 0.5 -0.2 0.2", "invalid_normalized_box"),
    ],
)
def test_unsupported_detection_rows_are_reported(tmp_path, row, code):
    root = dataset(tmp_path / "data")
    (root / "train/labels/ball.txt").write_text(row + "\n")
    result = audit_dataset(root)
    assert code in codes(result)
    assert result["errors"][0]["line"] == 1


def test_identical_pixels_files_cannot_cross_splits_under_different_names(tmp_path):
    root = dataset(tmp_path / "data")
    (root / "valid/images/ball.jpg").write_bytes((root / "train/images/ball.jpg").read_bytes())
    assert "identical_image_across_splits" in codes(audit_dataset(root))


def test_missing_and_orphan_labels_are_not_assumed_negative(tmp_path):
    root = dataset(tmp_path / "data")
    (root / "train/labels/ball.txt").rename(root / "train/labels/other.txt")
    result = audit_dataset(root)
    assert {"orphan_label", "missing_or_unreadable_label"} <= codes(result)


def test_two_image_extensions_cannot_implicitly_share_a_label(tmp_path):
    root = dataset(tmp_path / "data")
    (root / "train/images/ball.png").write_bytes(b"another image")
    assert "ambiguous_image_label_pair" in codes(audit_dataset(root))


def test_crossing_boundary_is_distinct_from_invalid_normalized_fields(tmp_path):
    root = dataset(tmp_path / "data")
    (root / "train/labels/ball.txt").write_text("0 0.98 0.5 0.1 0.2\n")
    result = audit_dataset(root)
    assert result["ok"]
    assert result["warnings"][0]["code"] == "box_crosses_image_boundary"


def test_cli_is_nonzero_for_one_bad_dataset_without_mutating_another(tmp_path, capsys):
    good, bad = dataset(tmp_path / "good"), dataset(tmp_path / "bad")
    (bad / "data.yaml").write_text("names: [hole]\n")
    before = file_hashes(good)
    assert main([str(good), str(bad)]) == 1
    assert '"ok": false' in capsys.readouterr().out
    assert file_hashes(good) == before


def test_bad_yaml_returns_evidence_instead_of_starting_training(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    (root / "data.yaml").write_text("names: [broken\n")
    assert "unreadable_dataset_yaml" in codes(audit_dataset(root))


def notebook_cells():
    notebook = Path(__file__).parents[1] / "scripts/vision/train_golf_ball_yolo11.ipynb"
    return json.loads(notebook.read_text(encoding="utf-8"))["cells"]


def test_foreign_classes_stop_actual_notebook_before_copy(tmp_path, monkeypatch):
    root = dataset(tmp_path / "source")
    config = yaml.safe_load((root / "data.yaml").read_text())
    config["names"] = ["hole"]
    (root / "data.yaml").write_text(yaml.safe_dump(config))
    monkeypatch.chdir(tmp_path)
    tree = ast.parse("".join(notebook_cells()[3]["source"]))
    branch = next(node for node in tree.body if isinstance(node, ast.If))
    command = compile(ast.Module(body=[branch], type_ignores=[]), "notebook-preparation", "exec")
    namespace = dict(
        os=os,
        shutil=shutil,
        yaml=yaml,
        MODE="combined",
        COMBINE_DATASETS=["fixture"],
        download_dataset=lambda _: SimpleNamespace(location=str(root)),
        require_valid_dataset=require_valid_dataset,
    )
    with pytest.raises(ValueError, match="preflight failed"):
        exec(command, namespace)  # pylint: disable=exec-used
    assert not list((tmp_path / "combined_dataset").rglob("*.jpg"))


def test_split_leakage_stops_actual_notebook_before_model_training(tmp_path):
    root = dataset(tmp_path / "source")
    (root / "valid/images/ball.jpg").write_bytes((root / "train/images/ball.jpg").read_bytes())
    calls = []
    model = SimpleNamespace(train=lambda **kwargs: calls.append(kwargs))
    namespace = dict(
        model=model,
        dataset_location=str(root),
        EPOCHS=1,
        IMAGE_SIZE=640,
        BATCH_SIZE=1,
        require_valid_dataset=require_valid_dataset,
    )
    command = compile("".join(notebook_cells()[6]["source"]), "notebook-training", "exec")
    with pytest.raises(ValueError, match="identical_image_across_splits"):
        exec(command, namespace)  # pylint: disable=exec-used
    assert not calls
