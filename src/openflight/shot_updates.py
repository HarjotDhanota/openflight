"""Apply late ``shot_updated`` log entries to the shot they update.

A shot published before its IWR or camera enrichment finished is logged twice:
``shot_detected`` with the first result, then ``shot_updated`` (same schema,
same ``shot_number``) once the enrichment lands. Readers that want each shot's
latest values, counted once, fold the updates in with :func:`apply_shot_updates`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

SHOT_DETECTED = "shot_detected"
SHOT_UPDATED = "shot_updated"
# Keys of a shot_updated entry that describe the update, not the shot.
_UPDATE_ONLY_KEYS = ("ts", "type", "update")


def _shot_number(event: Any) -> int | None:
    if not isinstance(event, Mapping):
        return None
    number = event.get("shot_number")
    if isinstance(number, bool) or not isinstance(number, int):
        return None
    return number


def merge_shot_update(detected: Mapping[str, Any], update: Mapping[str, Any]) -> dict[str, Any]:
    """The shot's latest values in the ``shot_detected`` schema and detection time."""
    merged = dict(detected)
    merged.update({key: value for key, value in update.items() if key not in _UPDATE_ONLY_KEYS})
    return merged


def apply_shot_updates(events: Iterable[Any]) -> list[Any]:
    """Return ``events`` with each shot's updates folded into its ``shot_detected``.

    The folded entry keeps its position and ``ts``, so each shot is still
    counted once and in detection order; ``shot_updated`` entries are removed.
    The latest update wins. An update whose ``shot_detected`` is missing from
    the same session stands in for it, once, at the latest update's position.
    Updates never cross a ``session_start`` (shot numbers restart per session).
    Anything that is not a well-formed shot entry passes through unchanged, and
    the input entries are never mutated.
    """
    events = list(events)
    # Last update per (session segment, shot number), and where it sits.
    latest: dict[tuple[int, int], tuple[int, Mapping[str, Any]]] = {}
    detected: set[tuple[int, int]] = set()
    segment = 0
    segments = []
    for index, event in enumerate(events):
        if isinstance(event, Mapping) and event.get("type") == "session_start":
            segment += 1
        segments.append(segment)
        number = _shot_number(event)
        if number is None:
            continue
        if event.get("type") == SHOT_UPDATED:
            latest[(segment, number)] = (index, event)
        elif event.get("type") == SHOT_DETECTED:
            detected.add((segment, number))

    folded = []
    for index, event in enumerate(events):
        number = _shot_number(event)
        key = (segments[index], number)
        if number is None or event.get("type") not in (SHOT_DETECTED, SHOT_UPDATED):
            folded.append(event)
        elif event.get("type") == SHOT_DETECTED:
            update = latest.get(key)
            folded.append(merge_shot_update(event, update[1]) if update else event)
        elif key not in detected and latest[key][0] == index:
            folded.append(merge_shot_update({"type": SHOT_DETECTED, "ts": event.get("ts")}, event))
    return folded


def read_session_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """A session JSONL file's entries with shot updates folded in.

    Blank, unparseable and non-object lines are skipped, as the analysis
    readers always have.
    """
    entries = []
    with Path(path).open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
    return apply_shot_updates(entries)
