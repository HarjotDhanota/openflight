"""Acquire and normalize the frozen Phase F1 meshes without vendoring them.

Usage from the repository root:

    $env:SKETCHFAB_API_TOKEN = "..."
    uv run --group research python research/silhouette_poc/meshes/download_meshes.py `
        --local-iron "C:/path/to/690CB 7-iron.STL"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

from silhouette_poc.generator.mesh_truth import (
    MESH_SOURCES,
    MeshSource,
    default_mesh_asset_root,
    load_binary_stl,
    load_gltf_archive,
    load_normalized_mesh,
    normalize_clubhead,
    save_normalized_mesh,
)

_NOMINAL_DIMENSIONS_MM = {
    "poc_driver": {"width": 110.0, "height": 60.0, "depth": 55.0},
    "poc_7iron": {"width": 80.0, "height": 50.0, "depth": 18.0},
}
_API_ROOT = "https://api.sketchfab.com/v3/models"


def _request_json(url: str, token: str | None = None) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Token {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as reply:
        return json.load(reply)


def _download(url: str, destination: Path) -> None:
    with urllib.request.urlopen(url, timeout=120) as reply, destination.open("wb") as output:
        while chunk := reply.read(1024 * 1024):
            output.write(chunk)


def validate_source_metadata(source: MeshSource, metadata: dict[str, Any]) -> None:
    """Fail closed if identity, author, download status, or license drifted."""
    if str(metadata.get("uid")) != source.uid or str(metadata.get("name")) != source.name:
        raise ValueError(f"source identity changed for {source.club}")
    if not bool(metadata.get("isDownloadable")):
        raise ValueError(f"source is no longer downloadable for {source.club}")
    author = str(metadata.get("user", {}).get("displayName", ""))
    if author != source.author:
        raise ValueError(f"source author changed for {source.club}: {author!r}")
    license_payload = metadata.get("license", {})
    license_url = str(license_payload.get("url", "")).replace("http://", "https://")
    if str(license_payload.get("label")) != "CC Attribution" or license_url.rstrip(
        "/"
    ) != source.license_url.rstrip("/"):
        raise ValueError(f"source license changed for {source.club}")


def acquire_source(source: MeshSource, token: str, output_root: Path) -> dict[str, Any]:
    if source.source_kind != "sketchfab":
        raise ValueError(f"{source.club} is not an authenticated Sketchfab source")
    metadata = _request_json(f"{_API_ROOT}/{source.uid}")
    validate_source_metadata(source, metadata)
    downloads = _request_json(f"{_API_ROOT}/{source.uid}/download", token)
    gltf = downloads.get("gltf")
    if not gltf or not gltf.get("url"):
        raise ValueError(f"Sketchfab returned no glTF download for {source.club}")
    with tempfile.TemporaryDirectory(prefix=f"openflight-{source.club}-") as temporary:
        archive = Path(temporary) / "source-gltf.zip"
        _download(str(gltf["url"]), archive)
        archive_sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
        if (
            source.expected_source_sha256 is not None
            and archive_sha256 != source.expected_source_sha256
        ):
            raise ValueError(
                f"download archive SHA-256 mismatch for {source.club}: {archive_sha256}"
            )
        loaded = load_gltf_archive(archive, source_uid=source.uid, source_sha256=archive_sha256)
        normalized = normalize_clubhead(loaded, _NOMINAL_DIMENSIONS_MM[source.club])
        asset_metadata = {
            "source_uid": source.uid,
            "source_name": source.name,
            "author": source.author,
            "page_url": source.page_url,
            "license_spdx": source.license_spdx,
            "license_url": source.license_url,
            "download_archive_sha256": archive_sha256,
            "download_format": "gltf",
            "normalization": (
                "largest compact welded connected component; PCA extent order; "
                "independent depth/width/height calibration"
            ),
            "nominal_dimensions_mm": _NOMINAL_DIMENSIONS_MM[source.club],
            "source_vertex_count": int(len(loaded.vertices_local_mm)),
            "source_triangle_count": int(len(loaded.faces)),
            "clubhead_vertex_count": int(len(normalized.vertices_local_mm)),
            "clubhead_triangle_count": int(len(normalized.faces)),
        }
        asset_path = output_root / f"{source.club}.npz"
        asset_sha256 = save_normalized_mesh(asset_path, normalized, asset_metadata)
        if (
            source.expected_asset_sha256 is not None
            and asset_sha256 != source.expected_asset_sha256
        ):
            raise ValueError(f"normalized asset SHA-256 mismatch for {source.club}: {asset_sha256}")
    record = {**asset_metadata, "asset_path": asset_path.name, "asset_sha256": asset_sha256}
    print(json.dumps(record, indent=2, sort_keys=True))
    return record


def import_local_stl(
    source_path: Path | str,
    output_root: Path,
    *,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Import the registered maintainer-local 7-iron without copying its STL."""
    source = MESH_SOURCES["poc_7iron"]
    registered_hash = source.expected_source_sha256
    if expected_sha256 is not None and registered_hash is not None:
        if expected_sha256.lower() != registered_hash.lower():
            raise ValueError("caller SHA-256 does not match the frozen local-source registration")
    required_hash = expected_sha256 or registered_hash
    loaded = load_binary_stl(source_path, source_uid=source.uid, expected_sha256=required_hash)
    normalized = normalize_clubhead(loaded, _NOMINAL_DIMENSIONS_MM[source.club])
    asset_metadata = {
        "source_uid": source.uid,
        "source_name": source.name,
        "author": source.author,
        "page_url": source.page_url,
        "license_spdx": source.license_spdx,
        "license_url": source.license_url,
        "source_file_sha256": loaded.source_sha256,
        "download_format": "binary_stl_maintainer_local",
        "redistribution": "prohibited; local research use only",
        "normalization": (
            "largest compact welded connected component; PCA extent order; "
            "independent depth/width/height calibration"
        ),
        "nominal_dimensions_mm": _NOMINAL_DIMENSIONS_MM[source.club],
        "source_vertex_count": int(len(loaded.vertices_local_mm)),
        "source_triangle_count": int(len(loaded.faces)),
        "clubhead_vertex_count": int(len(normalized.vertices_local_mm)),
        "clubhead_triangle_count": int(len(normalized.faces)),
        "trademark_note": "synthetic truth only; no Titleist endorsement implied",
    }
    asset_path = output_root / f"{source.club}.npz"
    asset_sha256 = save_normalized_mesh(asset_path, normalized, asset_metadata)
    record = {**asset_metadata, "asset_path": asset_path.name, "asset_sha256": asset_sha256}
    print(json.dumps(record, indent=2, sort_keys=True))
    return record


def _existing_record(source: MeshSource, output_root: Path) -> dict[str, Any] | None:
    asset_path = output_root / f"{source.club}.npz"
    if not asset_path.is_file():
        return None
    mesh, metadata, asset_sha256 = load_normalized_mesh(str(asset_path.resolve()))
    if mesh.source_uid != source.uid:
        raise ValueError(f"cached source identity mismatch for {source.club}")
    if source.expected_source_sha256 is not None and (
        mesh.source_sha256 != source.expected_source_sha256
    ):
        raise ValueError(f"cached source SHA-256 mismatch for {source.club}")
    if source.expected_asset_sha256 is not None and (asset_sha256 != source.expected_asset_sha256):
        raise ValueError(f"cached asset SHA-256 mismatch for {source.club}")
    return {**metadata, "asset_path": asset_path.name, "asset_sha256": asset_sha256}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=default_mesh_asset_root())
    parser.add_argument("--local-iron", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("SKETCHFAB_API_TOKEN")
    driver = MESH_SOURCES["poc_driver"]
    driver_record = _existing_record(driver, args.output)
    if driver_record is None:
        if not token:
            parser.error("SKETCHFAB_API_TOKEN is required to acquire the missing driver")
        driver_record = acquire_source(driver, token, args.output)
    iron = MESH_SOURCES["poc_7iron"]
    iron_record = _existing_record(iron, args.output)
    if iron_record is None:
        if args.local_iron is None:
            parser.error("--local-iron is required to import the missing maintainer-local 690CB")
        iron_record = import_local_stl(args.local_iron, args.output)
    records = [driver_record, iron_record]
    (args.output / "manifest.json").write_text(
        json.dumps({"sources": records}, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
