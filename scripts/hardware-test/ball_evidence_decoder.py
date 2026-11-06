"""Strict offline OFBLTR01 framing and structure; never a C replay verifier."""

from __future__ import annotations

import hashlib
import re
import struct
import zlib

MAGIC = b"OFBLTR01"
VERSION = 1
SUFFIX = b"Done\nDone\nl3dump:/>"
HEADER = struct.Struct("<8sIII")
STATE_WORDS = 165
ROWS_PER_CHUNK = 24
MAX_BINS = 64
META_WORDS = 40
MAX_RETAINED_ROWS = 2 * ROWS_PER_CHUNK - 1
MIN_PAYLOAD_WORDS = META_WORDS + 3 + STATE_WORDS + STATE_WORDS
MAX_PAYLOAD_WORDS = (
    META_WORDS + 2 * (3 + STATE_WORDS) + MAX_RETAINED_ROWS * (8 + MAX_BINS) + STATE_WORDS
)
MIN_PACKET_BYTES = HEADER.size + 4 * MIN_PAYLOAD_WORDS + len(SUFFIX)
MAX_PACKET_BYTES = HEADER.size + 4 * MAX_PAYLOAD_WORDS + len(SUFFIX)
UINT32_MAX = (1 << 32) - 1
META_NAMES = (
    "state_words",
    "rows_per_chunk",
    "max_bins",
    "total_calls",
    "context_epoch",
    "config_epoch",
    "rf_start_epoch",
    "origin",
    "overwritten_calls",
    "discarded_calls",
    "invalid_flags",
    "frozen",
    "chunk_count",
    "first_retained_call",
    "last_call",
    "pending",
    "cadence_skips",
    "behind_skips",
    "stale_skips",
    "observed_detect_queue_dropped",
)
STATE_FLOAT_INDICES = (2, 5, *range(13, 141), 143, 146, 147, 148, 160, 161, 162, 163)


class DecodeError(ValueError):
    """A packet is incomplete, unrecognized or unsafe as isolated replay input."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise DecodeError(reason)


def float32(word: int) -> float:
    """An optional display/validation view; original uint32 words remain authoritative."""
    return struct.unpack("<f", struct.pack("<I", word))[0]


def _finite(word: int) -> bool:
    return word & 0x7F800000 != 0x7F800000


def _state(words: list[int], where: str) -> list[int]:
    _require(len(words) == STATE_WORDS, f"{where}: wrong state length")
    _require(words[0] in (0, 1) and words[1] in (0, 1), f"{where}: invalid config flags")
    _require(words[8] <= 4 and words[9] <= 4, f"{where}: unknown state/reason")
    _require(
        words[12] <= MAX_BINS and words[11] + words[12] <= 128, f"{where}: invalid state window"
    )
    if words[8] == 4:
        _require(
            words[12] > 0 and words[11] <= words[145] < words[11] + words[12],
            f"{where}: locked ball outside state window",
        )
    _require(words[164] <= 64, f"{where}: invalid history length")
    _require(all(_finite(words[i]) for i in STATE_FLOAT_INDICES), f"{where}: nonfinite state float")
    if words[0]:
        _require(
            float32(words[2]) > 0 and 0 < float32(words[5]) < 1,
            f"{where}: invalid enabled config ratios",
        )
        _require(all(words[i] > 0 for i in (3, 4, 6, 7)), f"{where}: invalid enabled config counts")
    # Restart deliberately retains old candidate/ball bins, ages and counters.
    return words


class _Reader:
    def __init__(self, words: tuple[int, ...]):
        self.words = words
        self.position = 0

    def take(self, count: int) -> list[int]:
        end = self.position + count
        _require(0 <= count and end <= len(self.words), "truncated payload structure")
        result = list(self.words[self.position : end])
        self.position = end
        return result


def decode(raw: bytes, *, expected_build: str) -> dict:
    """Decode one complete CLI reply, preserving every float32 bit word.

    Source matching and structural eligibility do not verify checkpoint closure,
    actual firmware execution, runtime identity or physical synchronization.
    """
    _require(
        isinstance(expected_build, str)
        and re.fullmatch(r"[0-9a-f]{64}", expected_build) is not None,
        "expected_build must be a recognized lowercase 64-hex fingerprint",
    )
    _require(isinstance(raw, (bytes, bytearray, memoryview)), "raw must be bytes")
    view = memoryview(raw)
    _require(view.ndim == 1 and view.itemsize == 1, "raw must be a byte sequence")
    _require(
        MIN_PACKET_BYTES <= len(view) <= MAX_PACKET_BYTES,
        "packet length outside bounded wire limits",
    )
    raw = bytes(view)
    magic, version, payload_words, crc = HEADER.unpack_from(raw)
    _require(magic == MAGIC, "wrong magic; prefixes/console text are not accepted")
    _require(version == VERSION, "unsupported wire version")
    _require(
        MIN_PAYLOAD_WORDS <= payload_words <= MAX_PAYLOAD_WORDS,
        "payload word count outside bounded wire limits",
    )
    payload_end = HEADER.size + payload_words * 4
    _require(len(raw) == payload_end + len(SUFFIX), "declared payload length mismatch")
    _require(raw[payload_end:] == SUFFIX, "missing or malformed complete CLI suffix")
    payload = raw[HEADER.size : payload_end]
    _require(zlib.crc32(payload) == crc, "payload CRC mismatch")
    reader = _Reader(struct.unpack(f"<{payload_words}I", payload))
    meta_words = reader.take(META_WORDS)
    meta = dict(zip(META_NAMES, meta_words[:20], strict=True))
    _require(
        (meta["state_words"], meta["rows_per_chunk"], meta["max_bins"])
        == (STATE_WORDS, ROWS_PER_CHUNK, MAX_BINS),
        "unsupported payload layout",
    )
    fingerprint_bytes = struct.pack("<16I", *meta_words[20:36])
    _require(
        re.fullmatch(rb"[0-9a-f]{64}", fingerprint_bytes) is not None,
        "invalid build fingerprint encoding",
    )
    fingerprint = fingerprint_bytes.decode("ascii")
    _require(fingerprint == expected_build, "unrecognized build fingerprint")
    _require(meta["invalid_flags"] == 0, "recorder invalid flags refuse replay eligibility")
    _require(meta["frozen"] == 1 and meta["pending"] == 0, "export not frozen or pending call")
    _require(meta["chunk_count"] in (1, 2), "invalid chunk count")
    _require(
        meta["context_epoch"] == meta["config_epoch"] + meta["rf_start_epoch"] <= UINT32_MAX,
        "context/config/RF epoch arithmetic mismatch",
    )
    origin = meta["origin"]
    _require(origin in (0, 1, 2), "unknown context origin")
    if origin == 0:
        _require(
            meta["context_epoch"] == 0 and meta["discarded_calls"] == 0,
            "boot origin cannot follow another context",
        )
    elif origin == 1:
        _require(meta["config_epoch"] > 0, "configuration origin lacks configuration epoch")
    else:
        _require(meta["rf_start_epoch"] > 0, "RF origin lacks RF epoch")
    total, discarded = meta["total_calls"], meta["discarded_calls"]
    _require(discarded <= total, "discarded calls exceed global total")
    context_calls = total - discarded
    counts = (
        [context_calls]
        if context_calls < ROWS_PER_CHUNK
        else [ROWS_PER_CHUNK, context_calls % ROWS_PER_CHUNK]
    )
    _require(meta["chunk_count"] == len(counts), "chunk count violates post-commit rotation")
    retained = sum(counts)
    _require(
        meta["overwritten_calls"] == context_calls - retained,
        "overwritten/discarded/retained/global total mismatch",
    )
    first = total - retained + 1
    _require(
        first <= UINT32_MAX and meta["first_retained_call"] == first and meta["last_call"] == total,
        "first/last retained call mismatch",
    )
    meta.update(
        build_fingerprint=fingerprint,
        boot_hi=meta_words[36],
        boot_lo=meta_words[37],
        tick_hz=meta_words[38],
        frame_period_us=meta_words[39],
    )
    chunks, anomalies = [], []
    next_call = first
    skip_sums = [0, 0, 0]
    previous_row = None
    for chunk_index, expected_count in enumerate(counts):
        epoch, chunk_first, count = reader.take(3)
        _require(epoch == meta["context_epoch"], "chunk context epoch mismatch")
        _require(chunk_first == next_call, "chunk first ordinal mismatch")
        _require(count == expected_count, "chunk row count violates post-commit rotation")
        start_state = _state(reader.take(STATE_WORDS), f"chunk {chunk_index} starting state")
        rows = []
        for _ in range(count):
            ordinal, frame, tick, first_bin, bins, *skips = reader.take(8)
            _require(ordinal == next_call, "noncontiguous actual-call ordinal")
            _require(1 <= bins <= MAX_BINS and first_bin + bins <= 128, "invalid row range window")
            powers = reader.take(bins)
            _require(
                all(_finite(w) and float32(w) >= 0 for w in powers),
                "nonfinite or negative static power",
            )
            row = {
                "ordinal": ordinal,
                "frame_epoch": frame,
                "cycle_tick": tick,
                "first_bin": first_bin,
                "count": bins,
                "skips": skips,
                "power_words": powers,
            }
            for i, skipped in enumerate(skips):
                skip_sums[i] += skipped
            if previous_row is not None:
                frame_delta = (frame - previous_row["frame_epoch"]) & UINT32_MAX
                tick_delta = (tick - previous_row["cycle_tick"]) & UINT32_MAX
                if not 0 < frame_delta < 1 << 31 or not 0 < tick_delta < 1 << 31:
                    anomalies.append(
                        {
                            "ordinal": ordinal,
                            "frame_delta_mod32": frame_delta,
                            "tick_delta_mod32": tick_delta,
                            "meaning": "observed metadata anomaly/ambiguity, not inferred missing C calls",
                        }
                    )
            rows.append(row)
            previous_row = row
            next_call += 1
        chunks.append(
            {
                "context_epoch": epoch,
                "first_call": chunk_first,
                "row_count": count,
                "start_state_words": start_state,
                "rows": rows,
            }
        )
    end_state = _state(reader.take(STATE_WORDS), "ending state")
    _require(reader.position == payload_words, "unconsumed/extra payload words")
    _require(next_call == total + 1, "retained interval completeness mismatch")
    _require(
        all(
            actual <= meta[name]
            for actual, name in zip(
                skip_sums, ("cadence_skips", "behind_skips", "stale_skips"), strict=True
            )
        ),
        "retained row skip counts exceed context totals",
    )
    if chunks[-1]["row_count"] == 0:
        _require(
            chunks[-1]["start_state_words"] == end_state, "zero-row checkpoint closure mismatch"
        )
    token = (meta["boot_hi"] << 32) | meta["boot_lo"]
    return {
        "schema": "openflight.ball-evidence-decoded.v1",
        "wire_version": version,
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "payload_crc32": crc,
        "payload_words": payload_words,
        "metadata": meta,
        "chunks": chunks,
        "end_state_words": end_state,
        "structural_valid": True,
        "eligible_for_isolated_replay": True,
        "replay_verified": False,
        "physical_timing_qualified": False,
        "whole_session_reconstruction_qualified": False,
        "runtime_token": token or None,
        "runtime_identity_status": "observed_token_unqualified" if token else "unset",
        "retained_interval": {
            "first_call": first,
            "last_call": total,
            "retained_calls": retained,
            "current_context_calls": context_calls,
            "overwritten_current_context_calls": meta["overwritten_calls"],
            "discarded_prior_context_calls": discarded,
            "complete_current_context_prefix": meta["overwritten_calls"] == 0,
        },
        "chronology_anomalies": anomalies,
        "limits": "Only retained isolated ball-state inputs/checkpoints. Nonzero-row C closure has not been checked; frame/tick metadata is not physical contact or synchronization evidence.",
    }
