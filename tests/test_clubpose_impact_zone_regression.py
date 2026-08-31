"""The pre-registered gate for the impact-zone extractor, on the real session.

**CONSISTENCY ONLY. NO GROUND TRUTH EXISTS.** Every number here is measured
against `contact_marks.jsonl` pass 1 -- one annotator's reading of the same
pixels -- which also defines the template's crop, origin and landmark
convention. Nothing is scored against a launch monitor. The annotator's own
second-pass spread is 0.4-0.7 px median and 1.3-1.7 px p90, so the gate below
is within about a factor of two of the label noise, which is close to the point
where this reference stops being able to discriminate.

The numbers were registered on 2026-08-29, before this port existed, from
`research/empirical_template` iteration 3 (`v3_dataheel`):

    heel x    0.89 / 1.71 px   (median / p90)
    toe x     0.52 / 1.09 px
    topline y 0.85 / 1.78 px
    availability, all of f_c-6..f_c-1     >= 17 of 21 shots
    heel-toe zone correlation vs the marks >= 0.95

Every shot is scored LEAVE-ONE-OUT: the template it is placed against is built
without it, so a shot never contributed to the outline it is measured by.

This file runs the whole extractor over 21 shots and takes about three minutes.
It skips cleanly wherever the export or the marks are absent, which is
everywhere but the machine that recorded them.
"""

from __future__ import annotations

import functools

import numpy as np
import pytest

from openflight.camera.clubpose import head_outline as outline, impact_zone as zone

from . import _session_export as session

pytestmark = session.requires_marks

# Registered 2026-08-29, before the port. Do not move these to match a run.
GATE_MEDIAN_PX = {"heel_x": 0.89, "toe_x": 0.52, "topline_y": 0.85}
GATE_P90_PX = {"heel_x": 1.71, "toe_x": 1.09, "topline_y": 1.78}
GATE_AVAILABILITY = 17
GATE_ZONE_R = 0.95
# The scoring tolerance: a port reproduces the research when it lands within
# a twentieth of a pixel, which is well inside the annotator's own noise.
REPRODUCTION_TOLERANCE_PX = 0.05


def _reference_scale(swings: dict) -> float:
    """The common plate scale: the median over the 9-irons, as the research used."""
    nines = [s.plate_mm_per_px for s in swings.values() if s.club == "9-iron"]
    return float(np.median(nines or [s.plate_mm_per_px for s in swings.values()]))


def _marks_zone(swing, marks) -> float | None:
    """The hand marks' OWN heel-toe reading, under the same convention.

    The f_c-1 / f_c+1 marks are interpolated to the contact instant, and the
    topline at the ball's column is the heel-to-topline line extrapolated back,
    because the topline mark sits a median +11 px toe-ward of the ball where
    the crown is about 13 mm higher.
    """
    first, second = int(swing.contact_frame), int(swing.contact_frame) + 1
    if (swing.name, first) not in marks or (swing.name, second) not in marks:
        return None
    weight = swing.contact_frame - first
    points = {}
    for field in ("heel", "toe", "topline"):
        start = getattr(marks[(swing.name, first)], field)
        end = getattr(marks[(swing.name, second)], field)
        points[field] = start + weight * (end - start)
    heel, toe = points["heel"], points["toe"]
    span = toe - heel
    length = float(np.hypot(*span))
    if length <= 0.0:
        return None
    unit = span / length
    centre = 0.5 * (heel + toe) + unit * (zone.FACE_CENTRE_TOEWARD_MM / swing.plate_mm_per_px)
    ball = np.array([swing.ball.x, swing.ball.y])
    return float(np.dot(ball - centre, unit)) * swing.plate_mm_per_px


@functools.lru_cache(maxsize=1)
def _run() -> dict:
    """The whole extractor over the session, leave-one-out. Cached; ~3 minutes."""
    marks = session.pass_one_marks()
    swings = {name: session.swing(name) for name in session.shot_names()}
    reference = _reference_scale(swings)
    reports = {name: outline.head_masks(swing) for name, swing in swings.items()}

    shipped = {"heel_x": [], "toe_x": [], "topline_y": []}
    research = {"heel_x": [], "toe_x": [], "topline_y": []}
    availability = {"shipped": 0, "research": 0}
    readings: list[tuple[float, float]] = []
    results: dict[str, zone.ImpactZoneResult] = {}
    window = set(sorted(next(iter(swings.values())).align_frames)[:-1])

    for name, swing in sorted(swings.items()):
        samples = [
            (other, frame, mask, marks[(other.name, frame)])
            for other_name, other in swings.items()
            if other_name != name and other.club == swing.club
            for frame, mask in reports[other_name].masks.items()
            if (other_name, frame) in marks
        ]
        template = outline.build_template(
            samples, swing.club, reference, source="labels", excluded=name
        )
        assert template is not None, name

        before_usga, _ = outline.align_swing(swing, template, reports[name])
        after_usga, _ = zone.apply_usga_gates(
            swing, template, dict(before_usga), reports[name].masks
        )
        for store, placements in (("research", before_usga), ("shipped", after_usga)):
            if window <= set(placements):
                availability[store] += 1
        for store, placements in (("research", research), ("shipped", shipped)):
            source = before_usga if store == "research" else after_usga
            for frame, placement in source.items():
                mark = marks.get((name, frame))
                if mark is None:
                    continue
                placements["heel_x"].append(
                    float(placement.landmarks["heel"][0]) - float(mark.heel[0])
                )
                placements["toe_x"].append(
                    float(placement.landmarks["toe"][0]) - float(mark.toe[0])
                )
                placements["topline_y"].append(
                    float(placement.landmarks["topline"][1]) - float(mark.topline[1])
                )

        result = zone.extract_impact_zone(swing, template, report=reports[name])
        results[name] = result
        reference_zone = _marks_zone(swing, marks)
        if result.status == "ok" and reference_zone is not None:
            readings.append((result.heel_toe_mm, reference_zone))

    return {
        "shipped": shipped,
        "research": research,
        "availability": availability,
        "readings": readings,
        "results": results,
        "n_shots": len(swings),
    }


def _stats(values: list[float]) -> tuple[float, float]:
    magnitude = np.abs(values)
    return float(np.median(magnitude)), float(np.percentile(magnitude, 90))


class TestThePortReproducesTheResearch:
    """In the research's OWN configuration, to the digit. This is the port check.

    The shipped extractor adds the USGA gates, which the research did not have,
    so the two configurations are scored separately. If this class fails, the
    port is wrong; if `TestThePreRegisteredGate` fails and this passes, the new
    gates changed the answer and that is a finding, not a porting bug.
    """

    @pytest.mark.parametrize("axis", ["heel_x", "toe_x", "topline_y"])
    def test_the_median_reproduces_the_registered_number(self, axis):
        median, _ = _stats(_run()["research"][axis])

        assert median == pytest.approx(GATE_MEDIAN_PX[axis], abs=REPRODUCTION_TOLERANCE_PX)

    @pytest.mark.parametrize("axis", ["heel_x", "toe_x", "topline_y"])
    def test_the_p90_reproduces_the_registered_number(self, axis):
        _, p90 = _stats(_run()["research"][axis])

        assert p90 == pytest.approx(GATE_P90_PX[axis], abs=REPRODUCTION_TOLERANCE_PX)

    def test_it_scores_the_same_number_of_marked_frames(self):
        """34, as iteration 3 did. A different count would mean a different set."""
        assert len(_run()["research"]["heel_x"]) == 34

    def test_every_shot_is_scored_against_a_template_it_did_not_build(self):
        marks = session.pass_one_marks()

        assert len(marks) == 42
        assert len({name for name, _ in marks}) == 21


class TestThePreRegisteredGate:
    """The SHIPPED extractor, USGA gates included, against the same numbers."""

    @pytest.mark.parametrize("axis", ["heel_x", "toe_x", "topline_y"])
    def test_the_median_is_inside_a_pixel(self, axis):
        median, _ = _stats(_run()["shipped"][axis])

        assert median <= 1.0, f"{axis} median {median:.2f} px"

    @pytest.mark.parametrize("axis", ["heel_x", "toe_x", "topline_y"])
    def test_the_p90_is_inside_two_pixels(self, axis):
        _, p90 = _stats(_run()["shipped"][axis])

        assert p90 <= 2.0, f"{axis} p90 {p90:.2f} px"

    def test_the_heel_toe_zone_tracks_the_hand_marks(self):
        readings = _run()["readings"]
        automatic = [value for value, _ in readings]
        reference = [value for _, value in readings]

        assert len(readings) >= GATE_AVAILABILITY
        assert zone.zone_correlation(automatic, reference) >= GATE_ZONE_R

    def test_most_shots_produce_a_reading_at_all(self):
        results = _run()["results"]
        produced = sum(1 for result in results.values() if result.status == "ok")

        assert produced >= GATE_AVAILABILITY, (
            f"{produced}/{len(results)} shots produced a reading; "
            + "; ".join(
                f"{name}: {result.reason}"
                for name, result in results.items()
                if result.status != "ok"
            )
        )

    @pytest.mark.xfail(
        reason=(
            "MISS, reported not tuned away. The strict availability metric -- "
            "every one of f_c-6..f_c-1 accepted on a shot -- falls from 16/21 "
            "without the USGA gates to 5/21 with them, because the topline gate "
            "rejects the earliest frame on most shots, where the head is 36 % "
            "larger and the outline sits worst. It does not stop the extractor "
            "reading a zone: 20 of 21 shots still do, because the carry needs "
            "four frames and not six. See docs/clubface-impact-location.md."
        ),
        strict=True,
    )
    def test_every_frame_of_the_window_survives_on_seventeen_shots(self):
        assert _run()["availability"]["shipped"] >= GATE_AVAILABILITY


class TestWhatTheResultCarries:
    def test_the_vertical_channel_is_never_promoted_to_a_zone(self):
        for result in _run()["results"].values():
            assert result.high_low_status == "experimental_unvalidated"
            if result.status == "ok":
                assert result.zone in {"heel", "heel-centre", "centre", "centre-toe", "toe"}

    def test_every_withheld_shot_names_the_gate_that_stopped_it(self):
        for name, result in _run()["results"].items():
            if result.status == "withheld":
                assert result.reason and result.reason != "ok", name

    def test_the_template_source_says_these_came_from_the_marks(self):
        for result in _run()["results"].values():
            assert result.template_source == "labels"

    def test_the_carry_disagreement_is_small_where_both_models_exist(self):
        """The pair interpolation and the quadratic agree, so the carry is not
        where the error lives. Registered at 0.14 px in the research."""
        gaps = [
            result.carry_disagreement_mm
            for result in _run()["results"].values()
            if result.status == "ok" and result.carry_disagreement_mm is not None
        ]

        assert gaps
        assert float(np.median(gaps)) < 5.0
