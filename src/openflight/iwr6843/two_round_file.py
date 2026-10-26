"""A two-round shot saved beside the session's ``.l3dump`` files, and the loader for both.

Every payload is kept exactly as it crossed the link (each still CRC-sealed), so a replay rebuilds the same sparse dump
the live estimators ran on. Layout: SPARSE_FILE_MAGIC, a u32 length and a JSON header
(schema, session id, per-round cells, the transfer record), then records of
(u8 kind, u32 length, payload). A local file format, not a wire format.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

from openflight.iwr6843.two_round import (
    CellRequest,
    TwoRoundCapture,
    TwoRoundError,
    TwoRoundTransfer,
    decode_request,
    parse_cells,
    parse_overview,
)

SPARSE_SUFFIX = ".l3sparse"
SPARSE_FILE_MAGIC = b"OFL3SPARSE\n"
SPARSE_SCHEMA = "openflight.l3sparse.v1"
_SPARSE_KINDS = {"overview": 1, "request": 2, "cells": 3}
_SPARSE_NAMES = {code: name for name, code in _SPARSE_KINDS.items()}
_U32 = struct.Struct("<I")
_RECORD = struct.Struct("<BI")


def pack_sparse_capture(transfer: TwoRoundTransfer) -> bytes:
    """One shot's two-round payloads as a ``.l3sparse`` file."""
    header = json.dumps(
        {"schema": SPARSE_SCHEMA, "transfer": transfer.to_dict()}, sort_keys=True
    ).encode("utf-8")
    parts = [SPARSE_FILE_MAGIC, _U32.pack(len(header)), header]
    for kind, data in transfer.payloads:
        parts += [_RECORD.pack(_SPARSE_KINDS[kind], len(data)), data]
    return b"".join(parts)


def read_sparse_capture(data: bytes) -> tuple[dict, TwoRoundCapture]:
    """A ``.l3sparse`` file's header and the capture its payloads rebuild (every CRC checked)."""
    if not data.startswith(SPARSE_FILE_MAGIC):
        raise TwoRoundError("not a .l3sparse capture")
    at = len(SPARSE_FILE_MAGIC)
    (header_nbytes,) = _U32.unpack_from(data, at)
    at += _U32.size
    header = json.loads(data[at : at + header_nbytes])
    at += header_nbytes
    if header.get("schema") != SPARSE_SCHEMA:
        raise TwoRoundError(f"unsupported .l3sparse schema {header.get('schema')!r}")
    records = []
    while at < len(data):
        if at + _RECORD.size > len(data):
            raise TwoRoundError(".l3sparse truncated in a record header")
        code, nbytes = _RECORD.unpack_from(data, at)
        at += _RECORD.size
        if code not in _SPARSE_NAMES or at + nbytes > len(data):
            raise TwoRoundError(".l3sparse record is unknown or truncated")
        records.append((_SPARSE_NAMES[code], data[at : at + nbytes]))
        at += nbytes
    if not records or records[0][0] != "overview":
        raise TwoRoundError(".l3sparse has no overview")
    overview = parse_overview(records[0][1])
    capture = TwoRoundCapture(overview)
    meta = overview.meta
    pending: CellRequest | None = None
    for kind, payload in records[1:]:
        if kind == "request":
            pending = decode_request(
                payload, range_bin_counts=meta["range_bin_counts"], n_samples=meta["n_samples"]
            )
        elif kind == "cells" and pending is not None:
            capture.add(pending, parse_cells(payload, pending, meta))
            pending = None
        else:
            raise TwoRoundError(".l3sparse records are out of order")
    return header, capture


def load_iwr_capture(path) -> bytes:
    """A saved IWR capture's bytes: a ``.l3dump`` as is, a ``.l3sparse`` as its sparse dump."""
    path = Path(path)
    data = path.read_bytes()
    if path.suffix.lower() == SPARSE_SUFFIX:
        return read_sparse_capture(data)[1].sparse_dump()
    return data


__all__ = [
    "SPARSE_SCHEMA",
    "SPARSE_SUFFIX",
    "load_iwr_capture",
    "pack_sparse_capture",
    "read_sparse_capture",
]
