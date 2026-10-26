"""A capture the radar sent only in part: which cells are real, and what stands for the rest.

The two-round transfer (``two_round``) sends a whole-window overview first, then only the
(frame, range bin) cells the estimators read. The host rebuilds a production-format dump
from what arrived: every sent cell bit exact, every other cell an integer fill whose
per-bin sums equal the overview's, so every window-scope MTI mean is the full dump's.

Three things cannot come from fills, so a ``SparseDump`` carries them from the overview:

* the per-loop MTI power of unsent cells (``tracking.loop_power``): the overview's map;
* each MTI scope's median noise power (``PreparedShotDump.noise_power`` and ``doa``'s
  noise input): the overview's two scalars;
* a record of every (frame, local bin) cell an estimator reads, so the host can ask for
  a cell it read but was not sent.

``parse_dump`` of a ``SparseDump`` returns a ``SparseCube``; arrays derived from it with
the frame axis first and the bin axis last stay sparse. A plain ``bytes`` dump never takes
any of these paths: every hook is a type check that leaves full-dump arrays untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

MTI_SCOPES = ("burst", "window")


@dataclass
class SparseEvidence:
    """What a partly sent capture knows about its unsent cells, and what was read."""

    range_bin_counts: tuple[int, ...]
    n_samples: int
    # [n_frames, n_samples] local bins: True where the cell's samples were sent
    sent: np.ndarray
    # scope -> [n_frames * n_loops, n_samples] per-loop power (TX1+TX3, every RX)
    power_maps: dict[str, np.ndarray]
    # scope -> median MTI power per element
    noise_power: dict[str, float]
    reads: set[tuple[int, int]] = field(default_factory=set)

    @property
    def n_frames(self) -> int:
        """Frames in the capture."""
        return len(self.range_bin_counts)

    def record(self, frame: int, local_bin: int) -> None:
        """Note one cell an estimator read (padding beyond a frame's bins is no cell)."""
        if frame < 0:
            frame += self.n_frames
        if local_bin < 0:
            local_bin += self.n_samples
        if 0 <= frame < self.n_frames and 0 <= local_bin < self.range_bin_counts[frame]:
            self.reads.add((frame, local_bin))

    def read_mask(self) -> np.ndarray:
        """Every cell read so far, as a [n_frames, n_samples] mask."""
        mask = np.zeros(self.sent.shape, dtype=bool)
        for frame, local_bin in self.reads:
            mask[frame, local_bin] = True
        return mask

    def unsent_reads(self) -> np.ndarray:
        """The cells an estimator read that the radar has not sent."""
        return self.read_mask() & ~self.sent

    def attach(self, array: np.ndarray, scope: str | None = None) -> SparseCube:
        """``array`` (frames first, bins last) as a cube of this capture."""
        cube = np.asarray(array).view(SparseCube)
        cube.evidence = self
        cube.scope = scope
        return cube

    def fill_power(self, power: np.ndarray, scope: str) -> np.ndarray:
        """Per-loop power with every unsent cell taken from the overview's map."""
        power = np.array(power, dtype=float)
        n_loops = power.shape[0] // self.n_frames
        unsent = np.repeat(~self.sent, n_loops, axis=0)[:, : power.shape[1]]
        for frame, count in enumerate(self.range_bin_counts):
            unsent[frame * n_loops : (frame + 1) * n_loops, count:] = False
        power[unsent] = self.power_maps[scope][unsent]
        return power


class SparseDump(bytes):
    """Production-format dump bytes whose unsent cells are fills (``SparseEvidence``)."""

    evidence: SparseEvidence

    def __new__(cls, data: bytes, evidence: SparseEvidence):
        dump = super().__new__(cls, data)
        dump.evidence = evidence
        return dump

    def derived(self, data: bytes) -> SparseDump:
        """Another view of the same capture (a TX projection) with the same evidence."""
        return SparseDump(data, self.evidence)


def _is_index(value) -> bool:
    return isinstance(value, (int, np.integer)) and not isinstance(value, (bool, np.bool_))


class SparseCube(np.ndarray):
    """A cube of a sparse capture: records (frame, bin) cell reads, knows its MTI scope.

    Only an array whose first axis is the frame and whose last is the local range bin
    stays sparse; a reshape or slice that moves either axis becomes a plain view.
    """

    evidence: SparseEvidence | None = None
    scope: str | None = None

    def __array_finalize__(self, obj):
        evidence = getattr(obj, "evidence", None)
        keep = (
            evidence is not None
            and self.ndim >= 2
            and self.shape[0] == evidence.n_frames
            and self.shape[-1] == evidence.n_samples
        )
        self.evidence = evidence if keep else None
        same_shape = keep and getattr(obj, "shape", None) == self.shape
        self.scope = getattr(obj, "scope", None) if same_shape else None

    def __getitem__(self, key):
        evidence = self.evidence
        if evidence is not None and isinstance(key, tuple) and len(key) >= 2:
            frame, local_bin = key[0], key[-1]
            if (
                _is_index(frame)
                and _is_index(local_bin)
                and (len(key) == self.ndim or any(part is Ellipsis for part in key))
            ):
                evidence.record(int(frame), int(local_bin))
        return super().__getitem__(key)


def evidence_of(array) -> SparseEvidence | None:
    """The sparse capture an array or dump belongs to, else None."""
    return getattr(array, "evidence", None) if isinstance(array, (SparseCube, SparseDump)) else None


def carry(source: bytes, data: bytes) -> bytes:
    """``data`` derived from ``source``: sparse when ``source`` is, else unchanged."""
    return source.derived(data) if isinstance(source, SparseDump) else data


def sparse_parse(raw: bytes, cube: np.ndarray) -> np.ndarray:
    """``parse_dump``'s cube, sparse when the dump is (unchanged for plain bytes)."""
    return raw.evidence.attach(cube) if isinstance(raw, SparseDump) else cube


def scoped_mti(cube: np.ndarray, mti: np.ndarray, scope: str) -> np.ndarray:
    """``mti_filter``'s output, tagged with its scope when the cube is sparse."""
    evidence = evidence_of(cube)
    return mti if evidence is None else evidence.attach(mti, scope)


def mapped_loop_power(mti: np.ndarray, power: np.ndarray) -> np.ndarray:
    """``loop_power``'s output, unsent cells from the overview map (plain input: unchanged)."""
    evidence = evidence_of(mti)
    scope = getattr(mti, "scope", None)
    if evidence is None or scope not in evidence.power_maps:
        return power
    return evidence.fill_power(power, scope)


def noise_override(mti: np.ndarray) -> float | None:
    """The overview's median noise power for a sparse MTI scope, else None."""
    evidence = evidence_of(mti)
    scope = getattr(mti, "scope", None)
    if evidence is None or scope not in evidence.noise_power:
        return None
    return float(evidence.noise_power[scope])


__all__ = [
    "MTI_SCOPES",
    "SparseCube",
    "SparseDump",
    "SparseEvidence",
    "carry",
    "evidence_of",
    "mapped_loop_power",
    "noise_override",
    "scoped_mti",
    "sparse_parse",
]
