"""A Shot's source text names its value's label; the predicates read it back."""

import pytest

from openflight import source_labels as labels


@pytest.mark.parametrize(
    ("source", "calculated", "model_geometry", "estimated"),
    [
        ("calculated (D-plane tilt)", True, False, False),
        ("model geometry (D-plane tilt; club path from the arc prior)", False, True, False),
        ("estimated (D-plane tilt; launch direction weak)", False, False, True),
        ("radar", False, False, False),
        ("", False, False, False),
        (None, False, False, False),
    ],
)
def test_the_predicates_read_the_label_from_the_source_text(
    source, calculated, model_geometry, estimated
):
    assert labels.is_calculated_source(source) is calculated
    assert labels.is_model_geometry_source(source) is model_geometry
    assert labels.is_estimated_source(source) is estimated


def test_the_label_words_are_the_shared_vocabulary():
    assert (
        labels.LABEL_MEASURED,
        labels.LABEL_CALCULATED,
        labels.LABEL_MODEL_GEOMETRY,
        labels.LABEL_ESTIMATED,
    ) == ("measured", "calculated", "model_geometry", "estimated")
