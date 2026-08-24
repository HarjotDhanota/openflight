"""License-pinned mesh acquisition helpers and a NumPy silhouette renderer."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import struct
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from silhouette_poc.fusion.solver import FACE_NORMAL, _face_axes, _project, camera_presets


@dataclass(frozen=True)
class MeshSource:
    club: str
    uid: str
    name: str
    author: str
    page_url: str
    license_spdx: str
    license_url: str
    downloadable: bool
    published_triangles: int
    source_kind: str = "sketchfab"
    expected_source_sha256: str | None = None
    expected_asset_sha256: str | None = None


MESH_SOURCES = {
    "poc_driver": MeshSource(
        club="poc_driver",
        uid="978d0740dc514c8695bbb02f4083f0e3",
        name="Callaway Maverik golf driver",
        author="Paul Ekins",
        page_url=(
            "https://sketchfab.com/3d-models/"
            "callaway-maverik-golf-driver-978d0740dc514c8695bbb02f4083f0e3"
        ),
        license_spdx="CC-BY-4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        downloadable=True,
        published_triangles=41_855,
        expected_source_sha256=("0b9fac0caa2f7f26bc7492a6e12047c1552a6f8d934f6828cc2b3537f75105a2"),
        expected_asset_sha256=("6b9ba5a70b868f61fab40d2bdf11b7c355204b612f17efde76500b45b5308dc1"),
    ),
    "poc_7iron": MeshSource(
        club="poc_7iron",
        uid="grabcad:titleist-7-iron-golf-club-1:690cb-right-handed",
        name="Titleist 690CB 7-iron golf club",
        author="GrabCAD Community contributor",
        page_url="https://grabcad.com/library/titleist-7-iron-golf-club-1",
        license_spdx="LicenseRef-GrabCAD-Local-Research-Only",
        license_url=("https://help.grabcad.com/article/246-how-can-models-be-used-and-shared"),
        downloadable=False,
        published_triangles=26_238,
        source_kind="maintainer_local_binary_stl",
        expected_source_sha256=("f35936799295e6ce344279e557f0265ccbb8acef69c4508daff80d219d03cb85"),
    ),
}


@dataclass(frozen=True)
class TriangleMesh:
    """Triangle mesh in club-local coordinates: +x depth, +y width, +z height."""

    vertices_local_mm: np.ndarray
    faces: np.ndarray
    source_uid: str
    source_sha256: str

    def __post_init__(self) -> None:
        vertices = np.asarray(self.vertices_local_mm, dtype=float)
        faces = np.asarray(self.faces, dtype=np.int32)
        if vertices.ndim != 2 or vertices.shape[1] != 3:
            raise ValueError("mesh vertices must have shape [N,3]")
        if faces.ndim != 2 or faces.shape[1] != 3:
            raise ValueError("mesh faces must have shape [M,3]")
        if faces.size and (int(faces.min()) < 0 or int(faces.max()) >= len(vertices)):
            raise ValueError("mesh face index is outside the vertex array")
        if not np.all(np.isfinite(vertices)):
            raise ValueError("mesh vertices must be finite")
        object.__setattr__(self, "vertices_local_mm", vertices)
        object.__setattr__(self, "faces", faces)


_COMPONENT_DTYPES = {
    5120: np.dtype("i1"),
    5121: np.dtype("u1"),
    5122: np.dtype("<i2"),
    5123: np.dtype("<u2"),
    5125: np.dtype("<u4"),
    5126: np.dtype("<f4"),
}
_TYPE_WIDTHS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def _read_accessor(document: dict[str, Any], buffers: list[bytes], index: int) -> np.ndarray:
    accessor = document["accessors"][index]
    if "sparse" in accessor:
        raise ValueError("sparse glTF accessors are not supported")
    view = document["bufferViews"][accessor["bufferView"]]
    dtype = _COMPONENT_DTYPES[int(accessor["componentType"])]
    width = _TYPE_WIDTHS[str(accessor["type"])]
    count = int(accessor["count"])
    offset = int(view.get("byteOffset", 0)) + int(accessor.get("byteOffset", 0))
    stride = int(view.get("byteStride", dtype.itemsize * width))
    raw = buffers[int(view["buffer"])]
    if stride == dtype.itemsize * width:
        values = np.frombuffer(raw, dtype=dtype, count=count * width, offset=offset)
        return values.reshape(count, width).copy()
    output = np.empty((count, width), dtype=dtype)
    for row in range(count):
        output[row] = np.frombuffer(raw, dtype=dtype, count=width, offset=offset + row * stride)
    return output


def _quaternion_matrix(value: list[float]) -> np.ndarray:
    x, y, z, w = (float(item) for item in value)
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if norm == 0.0:
        return np.eye(4)
    x, y, z, w = x / norm, y / norm, z / norm, w / norm
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), 0],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), 0],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), 0],
            [0, 0, 0, 1],
        ],
        dtype=float,
    )


def _node_matrix(node: dict[str, Any]) -> np.ndarray:
    if "matrix" in node:
        return np.asarray(node["matrix"], dtype=float).reshape(4, 4, order="F")
    translation = np.eye(4)
    translation[:3, 3] = np.asarray(node.get("translation", [0, 0, 0]), dtype=float)
    scale = np.eye(4)
    scale[np.arange(3), np.arange(3)] = np.asarray(node.get("scale", [1, 1, 1]), dtype=float)
    return translation @ _quaternion_matrix(node.get("rotation", [0, 0, 0, 1])) @ scale


def _archive_buffer(bundle: zipfile.ZipFile, gltf_dir: str, uri: str) -> bytes:
    if uri.startswith("data:"):
        return base64.b64decode(uri.split(",", 1)[1])
    return bundle.read(str(Path(gltf_dir, uri)).replace("\\", "/"))


def load_gltf_archive(
    archive_path: Path | str, *, source_uid: str, source_sha256: str
) -> TriangleMesh:
    """Load triangle primitives and scene-node transforms from a Sketchfab glTF ZIP."""
    with zipfile.ZipFile(archive_path) as bundle:
        gltf_names = sorted(name for name in bundle.namelist() if name.lower().endswith(".gltf"))
        if len(gltf_names) != 1:
            raise ValueError("download archive must contain exactly one .gltf scene")
        gltf_name = gltf_names[0]
        document = json.loads(bundle.read(gltf_name))
        gltf_dir = str(Path(gltf_name).parent)
        buffers = [
            _archive_buffer(bundle, gltf_dir, str(item["uri"])) for item in document["buffers"]
        ]

    vertices: list[np.ndarray] = []
    faces: list[np.ndarray] = []

    def visit(node_index: int, parent: np.ndarray) -> None:
        node = document["nodes"][node_index]
        world = parent @ _node_matrix(node)
        if "mesh" in node:
            mesh = document["meshes"][int(node["mesh"])]
            for primitive in mesh["primitives"]:
                if int(primitive.get("mode", 4)) != 4:
                    raise ValueError("only glTF TRIANGLES primitives are supported")
                position = _read_accessor(
                    document, buffers, int(primitive["attributes"]["POSITION"])
                ).astype(float)
                transformed = np.column_stack([position, np.ones(len(position))]) @ world.T
                transformed = transformed[:, :3] / transformed[:, 3, None]
                if "indices" in primitive:
                    triangle = _read_accessor(document, buffers, int(primitive["indices"])).reshape(
                        -1
                    )
                else:
                    triangle = np.arange(len(position), dtype=np.int32)
                if len(triangle) % 3:
                    raise ValueError("triangle index count is not divisible by three")
                offset = sum(len(item) for item in vertices)
                vertices.append(transformed)
                faces.append(triangle.reshape(-1, 3).astype(np.int32) + offset)
        for child in node.get("children", []):
            visit(int(child), world)

    scene_index = int(document.get("scene", 0))
    for root in document["scenes"][scene_index].get("nodes", []):
        visit(int(root), np.eye(4))
    if not vertices or not faces:
        raise ValueError("glTF scene contains no triangle geometry")
    return TriangleMesh(np.vstack(vertices), np.vstack(faces), source_uid, source_sha256)


def load_binary_stl(
    path: Path | str, *, source_uid: str, expected_sha256: str | None
) -> TriangleMesh:
    """Decode a binary STL after an optional fail-closed source-hash check."""
    payload = Path(path).read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if expected_sha256 is not None and digest.lower() != expected_sha256.lower():
        raise ValueError(
            f"binary STL SHA-256 mismatch: expected {expected_sha256.lower()}, got {digest}"
        )
    if len(payload) < 84:
        raise ValueError("binary STL is shorter than its 84-byte header")
    triangle_count = struct.unpack_from("<I", payload, 80)[0]
    expected_size = 84 + int(triangle_count) * 50
    if len(payload) != expected_size:
        raise ValueError(
            f"binary STL length does not match triangle count: expected {expected_size}, "
            f"got {len(payload)}"
        )
    record_dtype = np.dtype(
        [("normal", "<f4", (3,)), ("vertices", "<f4", (3, 3)), ("attribute", "<u2")]
    )
    records = np.frombuffer(payload, dtype=record_dtype, count=triangle_count, offset=84)
    vertices = records["vertices"].reshape(-1, 3).astype(float)
    faces = np.arange(len(vertices), dtype=np.int32).reshape(-1, 3)
    return TriangleMesh(vertices, faces, source_uid, digest)


def _connected_face_components(vertices: np.ndarray, faces: np.ndarray) -> list[np.ndarray]:
    # Weld identical seam vertices before topology traversal. glTF material
    # primitives commonly duplicate vertices along an otherwise connected shell.
    _, welded = np.unique(np.round(vertices, decimals=9), axis=0, return_inverse=True)
    welded_faces = welded[faces]
    parent = np.arange(int(welded.max()) + 1, dtype=np.int32)

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = int(parent[index])
        return index

    for a, b, c in welded_faces:
        anchor = root(int(a))
        for other in (int(b), int(c)):
            other_root = root(other)
            if anchor != other_root:
                parent[other_root] = anchor
    labels = np.array([root(int(face[0])) for face in welded_faces], dtype=np.int32)
    return [np.flatnonzero(labels == label) for label in np.unique(labels)]


def normalize_clubhead(mesh: TriangleMesh, dimensions_mm: dict[str, float]) -> TriangleMesh:
    """Select the compact head component and normalize PCA axes to calibrated dimensions."""
    candidates: list[tuple[float, np.ndarray]] = []
    for face_indices in _connected_face_components(mesh.vertices_local_mm, mesh.faces):
        vertex_indices = np.unique(mesh.faces[face_indices].reshape(-1))
        points = mesh.vertices_local_mm[vertex_indices]
        extents = np.sort(np.ptp(points, axis=0))[::-1]
        if extents[2] <= 0.0:
            continue
        compactness = float(extents[0] / extents[2])
        if compactness <= 8.0:
            candidates.append((float(np.prod(extents)), face_indices))
    if not candidates:
        raise ValueError("no compact connected component qualifies as a clubhead")
    selected_faces = max(candidates, key=lambda item: item[0])[1]
    old_vertices = np.unique(mesh.faces[selected_faces].reshape(-1))
    remap = np.full(len(mesh.vertices_local_mm), -1, dtype=np.int32)
    remap[old_vertices] = np.arange(len(old_vertices), dtype=np.int32)
    faces = remap[mesh.faces[selected_faces]]
    points = mesh.vertices_local_mm[old_vertices]

    centered = points - np.mean(points, axis=0)
    _, _, principal = np.linalg.svd(centered, full_matrices=False)
    axes = principal.copy()
    for row in range(3):
        dominant = int(np.argmax(np.abs(axes[row])))
        if axes[row, dominant] < 0.0:
            axes[row] *= -1.0
    if np.linalg.det(axes) < 0.0:
        axes[2] *= -1.0
    principal_points = centered @ axes.T
    order = np.argsort(np.ptp(principal_points, axis=0))[::-1]
    width = principal_points[:, order[0]]
    height = principal_points[:, order[1]]
    depth = principal_points[:, order[2]]
    local = np.column_stack([depth, width, height])
    local -= (np.min(local, axis=0) + np.max(local, axis=0)) / 2.0
    target = np.array(
        [dimensions_mm["depth"], dimensions_mm["width"], dimensions_mm["height"]],
        dtype=float,
    )
    local *= target / np.ptp(local, axis=0)
    return TriangleMesh(local, faces, mesh.source_uid, mesh.source_sha256)


def rasterize_projected_triangles(
    vertices_uv: np.ndarray, faces: np.ndarray, *, width: int, height: int
) -> np.ndarray:
    """Rasterize the union of projected triangles with NumPy scanline intervals."""
    vertices = np.asarray(vertices_uv, dtype=float)
    triangles = vertices[np.asarray(faces, dtype=np.int32)]
    finite = np.all(np.isfinite(triangles), axis=(1, 2))
    triangles = triangles[finite]
    mask = np.zeros((height, width), dtype=bool)
    if not len(triangles):
        return mask
    min_y = np.min(triangles[:, :, 1], axis=1)
    max_y = np.max(triangles[:, :, 1], axis=1)
    edges_a = triangles[:, [0, 1, 2]]
    edges_b = triangles[:, [1, 2, 0]]
    for row in range(height):
        y = row + 0.5
        active = (min_y <= y) & (max_y >= y)
        if not np.any(active):
            continue
        a = edges_a[active]
        b = edges_b[active]
        dy = b[:, :, 1] - a[:, :, 1]
        crosses = (np.abs(dy) > 1e-12) & (
            (y >= np.minimum(a[:, :, 1], b[:, :, 1])) & (y <= np.maximum(a[:, :, 1], b[:, :, 1]))
        )
        safe_dy = np.where(crosses, dy, 1.0)
        intersections = a[:, :, 0] + (y - a[:, :, 1]) * (b[:, :, 0] - a[:, :, 0]) / safe_dy
        intersections = np.where(crosses, intersections, np.nan)
        with np.errstate(all="ignore"):
            left = np.nanmin(intersections, axis=1)
            right = np.nanmax(intersections, axis=1)
        valid = np.isfinite(left) & np.isfinite(right)
        starts = np.maximum(0, np.ceil(left[valid] - 0.5).astype(int))
        ends = np.minimum(width - 1, np.floor(right[valid] - 0.5).astype(int))
        visible = starts <= ends
        difference = np.zeros(width + 1, dtype=np.int32)
        np.add.at(difference, starts[visible], 1)
        np.add.at(difference, ends[visible] + 1, -1)
        mask[row] = np.cumsum(difference[:-1]) > 0
    return mask


def render_mesh_mask(
    mesh: TriangleMesh, center_world: np.ndarray, roll_rad: float, preset_name: str
) -> np.ndarray:
    """Project local mesh triangles through an existing explicit camera preset."""
    axis_u, axis_v = _face_axes(roll_rad)
    local = mesh.vertices_local_mm
    world = (
        np.asarray(center_world, dtype=float)[None, :]
        + local[:, 0, None] * FACE_NORMAL[None, :]
        + local[:, 1, None] * axis_u[None, :]
        + local[:, 2, None] * axis_v[None, :]
    )
    uv, front = _project(world, camera_presets()[preset_name])
    faces = mesh.faces[np.all(front[mesh.faces], axis=1)]
    camera = camera_presets()[preset_name]
    return rasterize_projected_triangles(uv, faces, width=camera.width, height=camera.height)


def save_normalized_mesh(path: Path | str, mesh: TriangleMesh, metadata: dict[str, Any]) -> str:
    """Write a deterministic local cache and return its content SHA-256."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        path,
        vertices_local_mm=mesh.vertices_local_mm,
        faces=mesh.faces,
        source_uid=np.asarray(mesh.source_uid),
        source_sha256=np.asarray(mesh.source_sha256),
        metadata_json=np.asarray(json.dumps(metadata, sort_keys=True, separators=(",", ":"))),
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


@lru_cache(maxsize=8)
def load_normalized_mesh(path: str) -> tuple[TriangleMesh, dict[str, Any], str]:
    payload = np.load(path, allow_pickle=False)
    mesh = TriangleMesh(
        payload["vertices_local_mm"],
        payload["faces"],
        str(payload["source_uid"]),
        str(payload["source_sha256"]),
    )
    metadata = json.loads(str(payload["metadata_json"]))
    return mesh, metadata, hashlib.sha256(Path(path).read_bytes()).hexdigest()


def default_mesh_asset_root() -> Path:
    return Path(__file__).resolve().parents[1] / "meshes" / "assets"
