"""The sphere fit's compiled residual, built from source on first use.

No binary is kept in the repository. The C source is compiled once with the
system compiler (``$CC``, else ``cc``) into a per-user cache keyed by the
source's SHA-256 and loaded with ctypes. When it cannot be built or loaded the
preview is unavailable and says why; it never falls back to another solver.
"""

# pylint: disable=no-member  # OpenCV's members are generated at import

from __future__ import annotations

import ctypes
import hashlib
import os
import shlex
import subprocess
import threading
import uuid
from pathlib import Path

import cv2
import numpy as np

SOURCE = Path(__file__).with_name("sphere_residual.c")
COMPILE_FLAGS = ("-O2", "-fno-fast-math", "-shared", "-fPIC")
COMPILE_TIMEOUT_S = 120.0


class NativeUnavailable(RuntimeError):
    """The compiled residual cannot be built or loaded on this machine."""


def default_cache_dir() -> Path:
    """Where compiled libraries are kept for this user."""
    return Path.home() / ".cache" / "openflight" / "native"


def compiler() -> list[str]:
    """The C compiler command: ``$CC``, else ``cc``."""
    return shlex.split(os.environ.get("CC") or "cc")


def library_path(source: Path = SOURCE, cache_dir: Path | None = None) -> Path:
    """The cached library for this exact source."""
    digest = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    suffix = ".dll" if os.name == "nt" else ".so"
    return (cache_dir or default_cache_dir()) / f"sphere-residual-{digest}{suffix}"


def build(
    source: Path = SOURCE,
    cache_dir: Path | None = None,
    command: list[str] | None = None,
) -> Path:
    """The compiled library for this exact source, compiling it once if needed."""
    target = library_path(source, cache_dir)
    if target.is_file():
        return target
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise NativeUnavailable(f"cannot create the library cache {target.parent}: {exc}") from exc
    partial = target.with_name(f"{target.stem}.{os.getpid()}-{uuid.uuid4().hex}{target.suffix}")
    # MinGW-style Windows linkers export nothing from a DLL unless told to
    exports = ["-Wl,--export-all-symbols"] if os.name == "nt" else []
    arguments = [
        *(command or compiler()),
        *COMPILE_FLAGS,
        *exports,
        str(source),
        "-o",
        str(partial),
    ]
    try:
        try:
            subprocess.run(
                arguments, check=True, capture_output=True, text=True, timeout=COMPILE_TIMEOUT_S
            )
        except FileNotFoundError as exc:
            raise NativeUnavailable(f"no C compiler: {arguments[0]} was not found") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip().splitlines()[-1:] or ["no output"]
            raise NativeUnavailable(f"compiling {source.name} failed: {detail[0]}") from exc
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise NativeUnavailable(f"compiling {source.name} failed: {exc}") from exc
        try:
            os.replace(partial, target)
        except OSError as exc:
            # another process built the same source meanwhile, and Windows will not
            # replace a library that is loaded: that copy is this library
            if not target.is_file():
                raise NativeUnavailable(f"storing {target.name} failed: {exc}") from exc
    finally:
        partial.unlink(missing_ok=True)
    return target


def load(path: Path) -> ctypes.CDLL:
    """The library with its two functions' argument types declared."""
    try:
        loaded = ctypes.CDLL(str(path))
        array = np.ctypeslib.ndpointer(dtype=np.float64, flags="C_CONTIGUOUS")
        loaded.sphere_geometry.argtypes = [array] * 5
        loaded.sphere_geometry.restype = None
        loaded.conditional_residual.argtypes = (
            [array] * 4 + [ctypes.c_size_t, ctypes.c_double] + [array] * 3
        )
        loaded.conditional_residual.restype = None
    except (OSError, AttributeError) as exc:
        raise NativeUnavailable(f"loading {path.name} failed: {exc}") from exc
    return loaded


_LOCK = threading.Lock()
_LOADED: dict = {"library": None, "error": None}


def library() -> ctypes.CDLL:
    """This process's residual library; a failure is remembered, never retried."""
    with _LOCK:
        if _LOADED["library"] is None and _LOADED["error"] is None:
            try:
                _LOADED["library"] = load(build())
            except NativeUnavailable as exc:
                _LOADED["error"] = str(exc)
        if _LOADED["error"] is not None:
            raise NativeUnavailable(_LOADED["error"])
        return _LOADED["library"]


def availability() -> dict:
    """Whether the compiled residual can run here, and why not."""
    try:
        library()
    except NativeUnavailable as exc:
        return {"available": False, "reason": str(exc)}
    return {"available": True, "reason": None}


class Residual:
    """The fit's robust residual with the sphere geometry computed natively."""

    def __init__(self, back, observed, use, scale):
        if np.shape(back) != (60, 60) or np.shape(use) != (60, 60) or use.dtype != bool:
            raise ValueError("expected 60 by 60 background and boolean mask")
        if np.shape(observed) != (int(np.count_nonzero(use)),):
            raise ValueError("observation count differs from mask")
        self.back = np.ascontiguousarray(back, dtype=np.float64)
        self.observed = np.ascontiguousarray(observed, dtype=np.float64)
        self.use = use
        self.scale = scale
        self.geometry = [np.empty((60, 60), dtype=np.float64) for _ in range(3)]
        self.weights = np.empty(len(observed), dtype=np.float64)
        self.coefficients = np.empty(2, dtype=np.float64)

    def __call__(self, parameters, details=False):
        if np.shape(parameters) != (5,):
            raise ValueError("expected five parameters")
        geometry = self.geometry
        library().sphere_geometry(
            np.ascontiguousarray(parameters, dtype=np.float64), self.back, *geometry
        )
        a, b, base = [
            np.ascontiguousarray(cv2.GaussianBlur(g, (0, 0), 0.45)[self.use]) for g in geometry
        ]
        residual = np.empty(len(self.observed), dtype=np.float64)
        library().conditional_residual(
            a,
            b,
            base,
            self.observed,
            len(self.observed),
            self.scale,
            self.weights,
            residual,
            self.coefficients,
        )
        if details:
            return residual, float(self.coefficients[0]), float(self.coefficients[1])
        return residual


def sphere_geometry(parameters, back) -> list[np.ndarray]:
    """The C geometry in numpy: ball cover, lit cover and uncovered background."""
    yy, xx = np.mgrid[:60, :60] * 2 + 0.5
    nx = (xx - parameters[0]) / parameters[2]
    ny = (yy - parameters[1]) / parameters[2]
    rho = np.hypot(nx, ny)
    nz = np.sqrt(np.maximum(1 - rho * rho, 0))
    light = np.maximum(
        np.sin(parameters[4]) * (np.cos(parameters[3]) * nx + np.sin(parameters[3]) * ny)
        + np.cos(parameters[4]) * nz,
        0,
    )
    cover = np.clip((1 - rho) * parameters[2] / 2 + 0.5, 0, 1)
    return [cover, cover * light, (1 - cover) * back]


def conditional_residual(geometry, observed, use, scale):
    """The C robust residual in numpy, in double precision."""
    a, b, base = [cv2.GaussianBlur(g, (0, 0), 0.45)[use] for g in geometry]
    value = observed - base
    weights = np.ones(len(observed))
    for _ in range(3):
        aa = np.sum(weights * a * a) + 1e-8
        bb = np.sum(weights * b * b) + 1e-8
        ab = np.sum(weights * a * b)
        av = np.sum(weights * a * value)
        bv = np.sum(weights * b * value)
        denominator = max(aa * bb - ab * ab, 1e-8)
        ambient = np.clip((av * bb - bv * ab) / denominator, 0, 255)
        diffuse = np.clip((bv * aa - av * ab) / denominator, 0, 400)
        residual = np.clip(base + ambient * a + diffuse * b, 0, 255) - observed
        weights = np.minimum(1, scale / np.maximum(np.abs(residual), 1e-8))
    return residual, ambient, diffuse


class ReferenceResidual(Residual):
    """The same residual in numpy, for tests on machines with no C compiler only.

    The preview never selects it: it agrees with the compiled residual to about
    1e-7 DN, not bit for bit, so a fit through it is not the measured preview.
    """

    def __call__(self, parameters, details=False):
        if np.shape(parameters) != (5,):
            raise ValueError("expected five parameters")
        residual, ambient, diffuse = conditional_residual(
            sphere_geometry(parameters, self.back), self.observed, self.use, self.scale
        )
        if details:
            return residual, float(ambient), float(diffuse)
        return residual
