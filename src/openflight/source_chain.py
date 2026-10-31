"""The best available source for one shot input, and the record of how it was chosen.

Each upstream input (ball range, vertical and horizontal launch, club path and attack,
full speeds, spin, ...) has a list of routes in quality order: radar, then camera,
then a model. ``resolve`` runs them in that order and takes the first that gives a
finite value; every route before it is kept with the reason it gave nothing. A route
that raises is refused with its error, so one failing stage never takes the input,
or the shot, down with it.

A value carries the label of its weakest input (``weakest_label``): measured >
calculated > model_geometry > estimated > experimental > not_measured. A gap found
later is closed by adding a route, never by a special case in a caller.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence, Union

from .source_labels import (
    LABEL_CALCULATED,
    LABEL_ESTIMATED,
    LABEL_EXPERIMENTAL,
    LABEL_IWR_PARTIAL,
    LABEL_MEASURED,
    LABEL_MODEL_GEOMETRY,
)

LABEL_NOT_MEASURED = "not_measured"
# strongest first; iwr_partial (an IWR estimate from a partial dump's intact frames) is
# weaker than estimated, and its records read "estimated" (source_labels.record_label)
LABEL_ORDER = (
    LABEL_MEASURED,
    LABEL_CALCULATED,
    LABEL_MODEL_GEOMETRY,
    LABEL_ESTIMATED,
    LABEL_IWR_PARTIAL,
    LABEL_EXPERIMENTAL,
    LABEL_NOT_MEASURED,
)


def weakest_label(*labels: Optional[str]) -> Optional[str]:
    """The weakest of the given labels (None ignored); None when none is given."""
    known = [label for label in labels if label is not None]
    for label in known:
        if label not in LABEL_ORDER:
            raise ValueError(f"unknown label {label!r}")
    return max(known, key=LABEL_ORDER.index) if known else None


@dataclass(frozen=True)
class Candidate:
    """One route's value with its label, source, 1-sigma uncertainty and note."""

    value: Any
    label: str
    source: str
    uncertainty: Optional[float] = None
    note: Optional[str] = None
    details: Optional[dict] = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if self.label not in LABEL_ORDER or self.label == LABEL_NOT_MEASURED:
            raise ValueError(f"a candidate needs a value label, not {self.label!r}")


# A route gives a candidate, or the reason it gave nothing (None: no reason given).
RouteResult = Union[Candidate, str, None]


@dataclass(frozen=True)
class Route:
    """One way to get an input, run only when every route before it gave nothing."""

    name: str
    produce: Callable[[], RouteResult]


@dataclass(frozen=True)
class Resolution:
    """The input as resolved: the winning route's value, or not measured with every reason."""

    input: str
    value: Any = None
    label: str = LABEL_NOT_MEASURED
    source: Optional[str] = None
    route: Optional[str] = None
    uncertainty: Optional[float] = None
    note: Optional[str] = None
    tried: tuple = ()
    details: Optional[dict] = field(default=None, compare=False)

    @property
    def present(self) -> bool:
        """Whether a route gave a value."""
        return self.label != LABEL_NOT_MEASURED and self.value is not None

    @property
    def reason(self) -> Optional[str]:
        """Why there is no value: each route's refusal in order; None when there is one."""
        if self.present:
            return None
        return "; ".join(f"{name}: {why}" for name, why in self.tried) or "no route"

    def to_dict(self) -> dict:
        """The record as plain JSON values."""
        return {
            "value": self.value,
            "label": self.label,
            "source": self.source,
            "route": self.route,
            "uncertainty": self.uncertainty,
            "note": self.note,
            "reason": self.reason,
            "tried": [{"route": name, "reason": why} for name, why in self.tried],
        }


def _finite(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    return value is not None


def resolve(input_name: str, routes: Sequence[Route]) -> Resolution:
    """The first route, in order, that gives a finite value; the rest are not run."""
    tried: list[tuple[str, str]] = []
    for route in routes:
        try:
            outcome = route.produce()
        except Exception as error:  # pylint: disable=broad-exception-caught
            tried.append((route.name, f"error: {type(error).__name__}: {error}"))
            continue
        if isinstance(outcome, Candidate):
            if not _finite(outcome.value):
                tried.append((route.name, f"{route.name} gave a non-finite value"))
                continue
            return Resolution(
                input=input_name,
                value=outcome.value,
                label=outcome.label,
                source=outcome.source,
                route=route.name,
                uncertainty=outcome.uncertainty,
                note=outcome.note,
                tried=tuple(tried),
                details=outcome.details,
            )
        tried.append((route.name, outcome or f"{route.name} gave nothing"))
    return Resolution(input=input_name, tried=tuple(tried))


def _shown(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{float(value):.2f}"
    return str(value)


def resolve_strongest(input_name: str, routes: Sequence[Route]) -> Resolution:
    """Every route runs; the value with the strongest label wins, a tie keeps the route
    order. Each refusal is kept as ``resolve`` keeps it, and each value that lost is kept
    in ``tried`` with its own label and why it lost, so the choice is visible. With one
    value (or none) the winner is ``resolve``'s; a later route's refusal is kept too."""
    outcomes: list[tuple[Route, Union[Candidate, str]]] = []
    for route in routes:
        try:
            outcome = route.produce()
        except Exception as error:  # pylint: disable=broad-exception-caught
            outcomes.append((route, f"error: {type(error).__name__}: {error}"))
            continue
        if isinstance(outcome, Candidate) and not _finite(outcome.value):
            outcome = f"{route.name} gave a non-finite value"
        outcomes.append((route, outcome or f"{route.name} gave nothing"))
    given = [(route, outcome) for route, outcome in outcomes if isinstance(outcome, Candidate)]
    if not given:
        return Resolution(input=input_name, tried=tuple((r.name, why) for r, why in outcomes))
    winner, best = min(given, key=lambda pair: LABEL_ORDER.index(pair[1].label))
    tried: list[tuple[str, str]] = []
    for route, outcome in outcomes:
        if route is winner:
            continue
        if not isinstance(outcome, Candidate):
            tried.append((route.name, outcome))
            continue
        gave = f"gave {_shown(outcome.value)} ({outcome.label})"
        why = (
            f"{winner.name} ran first with the same label"
            if outcome.label == best.label
            else f"{winner.name} ({best.label}) outranks it"
        )
        tried.append((route.name, f"{gave}; {why}"))
    return Resolution(
        input=input_name,
        value=best.value,
        label=best.label,
        source=best.source,
        route=winner.name,
        uncertainty=best.uncertainty,
        note=best.note,
        tried=tuple(tried),
        details=best.details,
    )


__all__ = [
    "Candidate",
    "LABEL_NOT_MEASURED",
    "LABEL_ORDER",
    "Resolution",
    "Route",
    "resolve",
    "resolve_strongest",
    "weakest_label",
]
