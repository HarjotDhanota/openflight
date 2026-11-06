"""Read-only preflight for single-class YOLO detection datasets."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

import yaml

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


def audit_dataset(folder: str | Path) -> dict:
    """Check labels and byte-identical split leakage without changing inputs."""
    root = Path(folder).resolve()
    errors: list[dict] = []
    warnings: list[dict] = []
    counts = {}
    files = []
    hashes: dict[str, list[dict]] = defaultdict(list)

    def issue(code, path=None, line=None):
        row = {"code": code}
        if path is not None:
            row["file"] = str(path)
        if line is not None:
            row["line"] = line
        errors.append(row)

    def record(path, split, kind):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entry = {"file": str(path), "split": split, "kind": kind, "sha256": digest}
        files.append(entry)
        if kind == "image":
            hashes[digest].append(entry)

    config_path = root / "data.yaml"
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("expected a dataset mapping")
    except (OSError, UnicodeError, ValueError, yaml.YAMLError):
        issue("unreadable_dataset_yaml", config_path)
        config = {}
    if config:
        record(config_path, None, "metadata")
    names = config.get("names")
    if isinstance(names, dict) and len(names) == 1 and next(iter(names)) in (0, "0"):
        names = list(names.values())
    valid_name = (
        isinstance(names, list)
        and len(names) == 1
        and isinstance(names[0], str)
        and "".join(c for c in names[0].lower() if c.isalnum()) == "golfball"
    )
    if not valid_name or config.get("nc", 1) != 1:
        issue("expected_single_golf_ball_class", config_path)
    base = config.get("path", str(root))
    if not isinstance(base, str):
        issue("unsupported_dataset_path", config_path)
        base = str(root)
    base_path = Path(base)
    if not base_path.is_absolute():
        base_path = root / base_path
    for split in ["train", "val", "test"]:
        if split == "test" and not config.get(split):
            continue
        declared = config.get(split)
        if not isinstance(declared, str):
            issue("expected_image_directory", config_path)
            continue
        directory = (base_path / declared).resolve()
        if not directory.exists() and declared.startswith("../"):
            directory = (base_path / declared[3:]).resolve()
        if not directory.is_dir() or directory.name != "images":
            issue("expected_paired_images_labels_directories", directory)
            continue
        labels = directory.parent / "labels"
        images = sorted(
            p for p in directory.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
        )
        if not images:
            issue("empty_image_split", directory)
        seen_labels = set()
        counts[split] = {
            "images": len(images),
            "labelled_images": 0,
            "explicit_empty_labels": 0,
            "boxes": 0,
        }
        for image in images:
            record(image, split, "image")
            label = labels / image.relative_to(directory).with_suffix(".txt")
            if label in seen_labels:
                issue("ambiguous_image_label_pair", label)
            seen_labels.add(label)
            try:
                lines = label.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeError):
                issue("missing_or_unreadable_label", label)
                continue
            record(label, split, "label")
            rows = [(i, line.split()) for i, line in enumerate(lines, 1) if line.strip()]
            counts[split]["labelled_images"] += 1
            counts[split]["explicit_empty_labels"] += int(not rows)
            for number, row in rows:
                try:
                    if len(row) != 5:
                        raise ValueError("expected five detection fields")
                    values = [float(value) for value in row]
                    if not all(math.isfinite(value) for value in values):
                        raise ValueError("nonfinite detection")
                except ValueError:
                    issue("invalid_detection_row", label, number)
                    continue
                class_id, x, y, width, height = values
                if class_id != 0:
                    issue("expected_class_zero", label, number)
                if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < width <= 1 and 0 < height <= 1):
                    issue("invalid_normalized_box", label, number)
                elif (
                    min(x - width / 2, y - height / 2) < -1e-6
                    or max(x + width / 2, y + height / 2) > 1 + 1e-6
                ):
                    warnings.append(
                        {"code": "box_crosses_image_boundary", "file": str(label), "line": number}
                    )
                counts[split]["boxes"] += 1
        for label in sorted(labels.rglob("*.txt")):
            if label not in seen_labels:
                issue("orphan_label", label)
    duplicates = []
    for digest, entries in hashes.items():
        if len(entries) > 1:
            duplicates.append({"sha256": digest, "files": entries})
            if len({entry["split"] for entry in entries}) > 1:
                issue("identical_image_across_splits", entries[0]["file"])
    return {
        "scope": (
            "Class/annotation format and exact file-duplicate audit; "
            "not label truth, image decoding or session independence"
        ),
        "root": str(root),
        "ok": not errors,
        "splits": counts,
        "errors": errors,
        "warnings": warnings,
        "duplicate_image_groups": duplicates,
        "files": files,
    }


def require_valid_dataset(folder: str | Path) -> dict:
    """Stop preparation or training before copying unsupported annotations."""
    result = audit_dataset(folder)
    if not result["ok"]:
        codes = sorted({row["code"] for row in result["errors"]})
        raise ValueError(f"Dataset preflight failed for {folder}: {', '.join(codes)}")
    return result


def main(argv=None) -> int:
    """Print audit evidence and return nonzero when any dataset fails."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("datasets", type=Path, nargs="+")
    args = parser.parse_args(argv)
    results = [audit_dataset(path) for path in args.datasets]
    print(json.dumps(results, indent=2))
    return int(any(not result["ok"] for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
