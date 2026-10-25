"""A Shot's source text names its value's label; the predicates read it back."""

import pytest

from openflight import source_labels as labels


@pytest.mark.parametrize(
    ("source", "labels_read"),
    [
        ("measured (camera)", {"measured"}),
        ("calculated (D-plane tilt)", {"calculated"}),
        ("model geometry (D-plane tilt; club path from the arc prior)", {"model_geometry"}),
        ("estimated (D-plane tilt; launch direction weak)", {"estimated"}),
        ("radar", set()),
        ("", set()),
        (None, set()),
    ],
)
def test_the_predicates_read_the_label_from_the_source_text(source, labels_read):
    assert labels.is_measured_source(source) is ("measured" in labels_read)
    assert labels.is_calculated_source(source) is ("calculated" in labels_read)
    assert labels.is_model_geometry_source(source) is ("model_geometry" in labels_read)
    assert labels.is_estimated_source(source) is ("estimated" in labels_read)


def test_the_label_words_are_the_shared_vocabulary():
    assert (
        labels.LABEL_MEASURED,
        labels.LABEL_CALCULATED,
        labels.LABEL_MODEL_GEOMETRY,
        labels.LABEL_ESTIMATED,
    ) == ("measured", "calculated", "model_geometry", "estimated")


def test_experimental_is_a_label_word_but_never_a_model_label():
    assert labels.LABEL_EXPERIMENTAL == "experimental"
    assert labels.LABEL_EXPERIMENTAL not in labels.MODEL_LABELS
