"""Product and fusion code never imports tester or research code.

``config/module_buckets.json`` gives every tracked file under src/, ui/,
scripts/, config/, docs/ and tests/ one bucket:

- FUSION and PRODUCT: code that can go upstream (its tests too);
- DOCS-USER: user documentation that can go upstream;
- TESTER and RESEARCH: the fork's tester tooling and research, never merged.

The dependencies point one way: TESTER and RESEARCH may import anything, a
FUSION or PRODUCT Python file may import only FUSION and PRODUCT modules. The
check walks every import statement (lazy ones inside functions included). An
extension the kiosk loads by entry point is not an import, and is not checked.
"""

from __future__ import annotations

import ast
import json
import posixpath
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "config" / "module_buckets.json"
SHIPPED = {"FUSION", "PRODUCT"}
NEVER_MERGED = {"TESTER", "RESEARCH"}
BUCKETS = SHIPPED | NEVER_MERGED | {"DOCS-USER"}


@lru_cache(maxsize=1)
def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _files() -> dict[str, str]:
    return _manifest()["files"]


def _module_file(module: str) -> str | None:
    """The repo file a dotted module name loads, or None for anything outside the repo."""
    parts = module.split(".")
    if parts[0] == "openflight":
        base = Path("src", *parts)
    elif parts[0] == "tests":
        base = Path(*parts)
    else:
        return None
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if (REPO / candidate).is_file():
            return candidate.as_posix()
    return None


def _package_of(path: str) -> list[str]:
    parts = list(Path(path).with_suffix("").parts)
    if parts[0] == "src":
        parts = parts[1:]
    if parts[-1] == "__init__":
        return parts[:-1]
    return parts[:-1]


def _imported_files(path: str) -> set[tuple[int, str]]:
    """(line, repo file) for every import in this Python file that resolves into the repo."""
    tree = ast.parse((REPO / path).read_text(encoding="utf-8"), filename=path)
    found = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                package = _package_of(path)[: len(_package_of(path)) - (node.level - 1)]
                base = ".".join(package + ([base] if base else []))
            names = [base] + [f"{base}.{alias.name}" for alias in node.names]
        for name in names:
            target = _module_file(name)
            if target is not None:
                found.add((node.lineno, target))
    return found


def _tracked() -> list[str]:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not available to list the tracked files")
    result = subprocess.run(
        [git, "-C", str(REPO), "ls-files", *_manifest()["roots"]],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("not a git checkout")
    return sorted(line for line in result.stdout.splitlines() if line)


def test_every_tracked_file_has_exactly_one_known_bucket():
    files = _files()
    tracked = _tracked()
    missing = [path for path in tracked if path not in files]
    stale = [path for path in files if path not in set(tracked)]
    unknown = {path: bucket for path, bucket in files.items() if bucket not in BUCKETS}
    assert not missing, f"add these files to {MANIFEST.name}: {missing}"
    assert not stale, f"these files are gone; remove them from {MANIFEST.name}: {stale}"
    assert not unknown, f"unknown buckets: {unknown}"


def test_part_e_files_are_fusion_files_in_the_manifest():
    files = _files()
    for path in _manifest()["part_e"]:
        assert files.get(path) == "FUSION", path


def test_shipped_python_never_imports_tester_or_research_code():
    files = _files()
    crossings = []
    for path, bucket in sorted(files.items()):
        if bucket not in SHIPPED or not path.endswith(".py"):
            continue
        for line, target in sorted(_imported_files(path)):
            if files.get(target) in NEVER_MERGED:
                crossings.append(f"{path}:{line} imports {target} ({files[target]})")
    assert not crossings, "shipped code imports never-merged code:\n" + "\n".join(crossings)


UI_SOURCE = re.compile(r"\.(ts|tsx|js|jsx)$")
UI_IMPORT = re.compile(r"""(?:import|from)\s*(?:[^'"]*?from\s*)?['"](\.{1,2}/[^'"]+)['"]""")


def _ui_imported_files(path: str) -> set[str]:
    """The repo files one UI source file imports by a relative path."""
    files = _files()
    found = set()
    text = (REPO / path).read_text(encoding="utf-8")
    for spec in UI_IMPORT.findall(text):
        base = posixpath.normpath(posixpath.join(posixpath.dirname(path), spec))
        for suffix in ("", ".ts", ".tsx", ".js", ".jsx", ".css", "/index.ts", "/index.tsx"):
            if base + suffix in files:
                found.add(base + suffix)
                break
    return found


def test_shipped_ui_imports_never_merged_ui_only_in_the_known_mixed_files():
    """The UI's trace and free-play imports are known; a new one fails here."""
    files = _files()
    mixed = set(_manifest()["mixed"])
    crossings = []
    for path, bucket in sorted(files.items()):
        if bucket not in SHIPPED or not path.startswith("ui/") or not UI_SOURCE.search(path):
            continue
        for target in sorted(_ui_imported_files(path)):
            if files[target] in NEVER_MERGED and path not in mixed:
                crossings.append(f"{path} imports {target} ({files[target]})")
    assert not crossings, "shipped UI imports never-merged UI:\n" + "\n".join(crossings)


def test_the_kiosk_starts_without_any_extension_module():
    """server.py and what it imports at module level name no extension module."""
    files = _files()
    extensions = {
        path
        for path, bucket in files.items()
        if bucket in NEVER_MERGED or path in set(_manifest()["part_e"])
    }
    seen, queue = set(), ["src/openflight/server.py"]
    while queue:
        path = queue.pop()
        if path in seen:
            continue
        seen.add(path)
        for _line, target in _imported_files(path):
            assert target not in extensions, f"{path} imports {target}"
            queue.append(target)
