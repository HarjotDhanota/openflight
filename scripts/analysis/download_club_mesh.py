"""Acquire and normalize the club meshes without vendoring them.

The 7-iron used by this research is a GrabCAD community model. It is not
redistributed, so you fetch your own copy under GrabCAD's terms and point this
script at it; see SOURCES.md for the link, the expected SHA-256 and the licence
position. Run from the repository root:

    uv run python scripts/analysis/download_club_mesh.py \
        --local-iron "/path/to/690CB 7-iron.STL"

Right-handed is the default and is all most runs need. The same GrabCAD listing
also ships a left-handed 690CB; import it with `--local-iron-left` so a
left-handed golfer is fitted against a left-handed model rather than a mirrored
right-handed one. That source is not hash-pinned yet, so its first import must
name the hash it is pinning:

    uv run python scripts/analysis/download_club_mesh.py \
        --local-iron-left "/path/to/690CB 7-iron LH.STL" \
        --local-iron-left-sha256 <sha256 of that file>

"""

# Imports follow the repository-root path bootstrap below.
# pylint: disable=wrong-import-position

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from openflight.camera.clubpose.mesh import (  # noqa: E402
    ACTIVE_MESH_SOURCES,
    CATEGORY_DIMENSIONS_MM,
    MESH_SOURCES,
    MeshSource,
    admit_mesh,
    default_mesh_asset_root,
    detect_face_plane,
    face_detection_record,
    load_binary_stl,
    load_normalized_mesh,
    normalize_clubhead,
    save_normalized_mesh,
)

# Bumped with the v4 cache fields: `handedness` split into `club_handedness`
# and `reflect_into_world_frame`. A manifest written by an older build no longer
# validates, so the asset is re-imported rather than read under the old meaning.
_NORMALIZATION_VERSION = "geometric-face-anchor-v4-club-handedness"


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


def import_local_stl(
    source_path: Path | str,
    output_root: Path,
    *,
    expected_sha256: str | None = None,
    club: str = "poc_7iron",
) -> dict[str, Any]:
    """Import one registered maintainer-local iron without copying its STL.

    Defaults to the right-handed 690CB. ``club="poc_7iron_left"`` imports the
    left-handed model, which is not hash-pinned in the registration yet and so
    requires an explicit ``expected_sha256`` -- importing unverified CAD would
    defeat the point of pinning the right-handed one.
    """
    source = MESH_SOURCES[club]
    registered_hash = source.expected_source_sha256
    if expected_sha256 is not None and registered_hash is not None:
        if expected_sha256.lower() != registered_hash.lower():
            raise ValueError("caller SHA-256 does not match the frozen local-source registration")
    required_hash = expected_sha256 or registered_hash
    if required_hash is None:
        raise ValueError(
            f"{club} has no registered SHA-256; pass the hash of the file you are "
            "importing so the source is pinned from its first import"
        )
    loaded = load_binary_stl(
        source_path,
        source_uid=source.uid,
        expected_sha256=required_hash,
        club_handedness=source.club_handedness,
        reflect_into_world_frame=source.reflect_into_world_frame,
    )
    admission = admit_mesh(
        loaded,
        category_dimensions_mm=CATEGORY_DIMENSIONS_MM[source.club],
        source_units_mm=True,
    )
    if not admission.accepted:
        raise ValueError(f"mesh admission failed for {source.club}: {admission.reasons}")
    assert admission.face is not None
    normalized = normalize_clubhead(
        loaded,
        CATEGORY_DIMENSIONS_MM[source.club],
        source_units_mm=True,
    )
    normalized_face = detect_face_plane(normalized)
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
        "normalization": _NORMALIZATION_VERSION,
        # Which club the CAD depicts, and whether the geometry still has to be
        # reflected into the left-handed world frame at load. Independent facts:
        # a left-handed club needs reflecting just as much as a right-handed one.
        # See `mesh.reflect_mesh_into_world_frame`.
        "club_handedness": normalized.club_handedness,
        "reflect_into_world_frame": normalized.reflect_into_world_frame,
        "world_frame_reflection_transform": "mirror_local_z_reverse_winding",
        "source_units_mm": True,
        "category_dimensions_mm": CATEGORY_DIMENSIONS_MM[source.club],
        "geometry_sha256": admission.geometry_sha256,
        "component_count_after_weld": admission.component_count,
        "boundary_edge_count_after_weld": admission.boundary_edge_count,
        "boundary_edge_fraction_after_weld": admission.boundary_edge_fraction,
        "dimensions_before_normalization_mm": admission.dimensions_mm,
        "face_detection_source": face_detection_record(admission.face),
        "face_detection_normalized": face_detection_record(normalized_face),
        "source_vertex_count": int(len(loaded.vertices_local_mm)),
        "source_triangle_count": int(len(loaded.faces)),
        "clubhead_vertex_count": int(len(normalized.vertices_local_mm)),
        "clubhead_triangle_count": int(len(normalized.faces)),
        "trademark_note": "synthetic truth only; no Titleist endorsement implied",
    }
    asset_path = output_root / f"{source.club}.npz"
    asset_sha256 = save_normalized_mesh(asset_path, normalized, asset_metadata)
    load_normalized_mesh.cache_clear()
    _, loaded_metadata, _ = load_normalized_mesh(str(asset_path.resolve()))
    record = {**loaded_metadata, "asset_path": asset_path.name, "asset_sha256": asset_sha256}
    print(json.dumps(record, indent=2, sort_keys=True))
    return record


def _existing_record(source: MeshSource, output_root: Path) -> dict[str, Any] | None:
    """Return a valid current cache record, or request a fresh import."""
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
    if metadata.get("normalization") != _NORMALIZATION_VERSION:
        return None
    return {**metadata, "asset_path": asset_path.name, "asset_sha256": asset_sha256}


def _asset_exists(args, club: str) -> bool:
    return (args.output / f"{club}.npz").is_file()


def _import_or_reuse(parser, args, club: str, stl_arg, sha256_arg) -> dict[str, Any]:
    """Reuse a valid cache for one club, or import its STL."""
    source = ACTIVE_MESH_SOURCES[club]
    record = _existing_record(source, args.output)
    if record is not None:
        return record
    if stl_arg is None:
        parser.error(
            f"--local-iron{'-left' if club.endswith('_left') else ''} is required to "
            f"import the missing maintainer-local {club}"
        )
    stl = Path(stl_arg).expanduser()
    if not stl.is_file():
        parser.error(
            f"no STL at {stl}. Fetch the 690CB 7-iron from the GrabCAD page in "
            "src/openflight/camera/clubpose/meshes/SOURCES.md (free account, "
            "their terms) and point the flag at the downloaded file."
        )
    return import_local_stl(stl, args.output, expected_sha256=sha256_arg, club=club)


def main() -> int:
    """Import any missing local assets and write their ignored manifest."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=default_mesh_asset_root())
    parser.add_argument("--local-iron", type=Path)
    parser.add_argument(
        "--local-iron-left",
        type=Path,
        help="left-handed 690CB STL; optional, and only needed for left-handed golfers",
    )
    parser.add_argument(
        "--local-iron-left-sha256",
        help="SHA-256 to pin the left-handed source on its first import",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    records = [_import_or_reuse(parser, args, "poc_7iron", args.local_iron, None)]
    # Left-handed is opt-in: nothing needs it until a left-handed golfer does.
    if args.local_iron_left is not None or _asset_exists(args, "poc_7iron_left"):
        records.append(
            _import_or_reuse(
                parser,
                args,
                "poc_7iron_left",
                args.local_iron_left,
                args.local_iron_left_sha256,
            )
        )
    (args.output / "manifest.json").write_text(
        json.dumps(
            {
                "sources": records,
                "retired_sources": [
                    {
                        "club": source.club,
                        "source_uid": source.uid,
                        "status": source.status,
                        "reason": source.status_reason,
                    }
                    for source in MESH_SOURCES.values()
                    if source.status != "active"
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
