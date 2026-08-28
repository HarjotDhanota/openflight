"""Locate the capture session these scripts replay.

Every script here reads one filtered session export. That path used to be a
hard-coded absolute path on one machine, which meant nobody else could run any
of them. Resolution order:

    1. the OPENFLIGHT_SESSION environment variable
    2. a --session argument, via `add_session_argument` / `session_from_args`
    3. a few conventional locations next to the repo

The export is not in git: it is roughly 200 MB of frames and radar dumps. Ask
the maintainer for `openflight_session_20260825_181734_filtered`, or point this
at any export with the same layout (`shots.csv` plus `shots/<shot>/frames.npz`).

Fails closed with an actionable message rather than a stack trace, because a
missing capture is a setup problem and not a bug in the analysis.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_VAR = "OPENFLIGHT_SESSION"
DEFAULT_NAME = "openflight_session_20260825_181734_filtered"

# Conventional spots to look before giving up. The export is often unzipped
# with the archive name repeated one level down, so try both shapes.
_SEARCH_ROOTS = (
    Path.home() / "Downloads",
    Path.home(),
    Path(__file__).resolve().parents[3].parent,
)


class SessionNotFound(RuntimeError):
    """The capture export could not be located."""


def _looks_like_session(path: Path) -> bool:
    return (path / "shots.csv").is_file() and (path / "shots").is_dir()


def _descend(path: Path) -> Path | None:
    """Accept either the export root or a wrapper directory containing it."""
    if _looks_like_session(path):
        return path
    nested = path / DEFAULT_NAME
    if _looks_like_session(nested):
        return nested
    return None


def find_session(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Return the session directory, or raise SessionNotFound with guidance."""
    tried: list[str] = []

    for candidate, label in (
        (explicit, "--session"),
        (os.environ.get(ENV_VAR), ENV_VAR),
    ):
        if not candidate:
            continue
        resolved = _descend(Path(candidate).expanduser())
        if resolved is not None:
            return resolved
        tried.append(f"{label}={candidate}")

    for root in _SEARCH_ROOTS:
        probe = root / DEFAULT_NAME
        resolved = _descend(probe) if probe.exists() else None
        if resolved is not None:
            return resolved
        tried.append(str(probe))

    raise SessionNotFound(
        "Could not find the capture session these scripts replay.\n\n"
        f"Set {ENV_VAR} to the export directory, for example:\n"
        f'    export {ENV_VAR}="/path/to/{DEFAULT_NAME}"\n'
        f'    $env:{ENV_VAR} = "C:\\path\\to\\{DEFAULT_NAME}"   # PowerShell\n\n'
        "or pass --session on scripts that accept it.\n\n"
        "The export is not in git (~200 MB of frames and radar dumps). Ask the\n"
        "maintainer for it, or point this at any export containing shots.csv and\n"
        "shots/<shot>/frames.npz.\n\n"
        "Looked in:\n  " + "\n  ".join(tried)
    )


def add_session_argument(parser) -> None:
    """Register --session on an argparse parser."""
    parser.add_argument(
        "--session",
        default=None,
        help=f"capture export directory (default: ${ENV_VAR}, then conventional paths)",
    )


def session_from_args(args) -> Path:
    return find_session(getattr(args, "session", None))


class MeshNotFound(RuntimeError):
    """The club mesh is absent, with instructions for acquiring it."""


MESH_RELATIVE = "research/silhouette_poc/meshes/assets/poc_7iron.npz"

_MESH_HELP = (
    "Could not find the club mesh these scripts project.\n\n"
    "Expected at:\n    {path}\n\n"
    "It is not in git: the model is a GrabCAD community upload admitted for\n"
    "local research use only, with no redistribution. Fetch it and normalise\n"
    "it with:\n"
    "    uv run --group research python \\\n"
    "        research/silhouette_poc/meshes/download_meshes.py --local-iron <STL>\n\n"
    "research/silhouette_poc/meshes/SOURCES.md has the source link, the\n"
    "expected SHA-256, and the licence position."
)


def load_mesh(repo_root=None):
    """Load the 7-iron mesh, or raise MeshNotFound with guidance.

    The mesh is not redistributed, so a fresh clone will not have it. Failing
    with a bare FileNotFoundError on a path nobody recognises sends people into
    the source tree to work out what is missing; this says it outright, exactly
    as find_session does for the capture export.
    """
    import numpy as np  # noqa: PLC0415

    from silhouette_poc.generator.mesh_truth import TriangleMesh  # noqa: PLC0415

    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[3]
    path = root / MESH_RELATIVE
    if not path.is_file():
        raise MeshNotFound(_MESH_HELP.format(path=path))
    data = np.load(path)
    return TriangleMesh(data["vertices_local_mm"], data["faces"], "poc_7iron", "x" * 64)
