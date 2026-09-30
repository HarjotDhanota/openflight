"""The TX2 phase helper is shared by the ball's horizontal proxy and club path.

Extracting it must not move the horizontal number: this test pins the helper's
contract, and test_iwr6843_pipeline.py pins the proxy's output.
"""

import numpy as np

from openflight.iwr6843 import doa


def _tdm(phase_offset_rad, *, n_frames=2, loops=2, n_rx=4, n_samples=8, n_tx=3):
    """TDM-split cube where TX2 leads the TX1/TX3 reference by a known phase."""
    tdm = np.zeros((n_frames, loops, n_tx, n_rx, n_samples), dtype=complex)
    tdm[:, :, 0, :, :] = 1.0 + 0j
    tdm[:, :, 2, :, :] = 1.0 + 0j
    tdm[:, :, 1, :, :] = np.exp(1j * phase_offset_rad)
    return tdm


def test_recovers_a_known_tx2_phase_offset():
    tdm = _tdm(0.4)
    result = doa.tx2_phase_at(tdm, 0, 0, 3, velocity_ms=0.0, tdm_sign=1, n_rx=4)
    assert result is not None
    phase, weight = result
    np.testing.assert_allclose(phase, 0.4, atol=1e-6)
    assert weight > 0.0


def test_zero_amplitude_returns_none():
    tdm = np.zeros((1, 1, 3, 4, 8), dtype=complex)
    assert doa.tx2_phase_at(tdm, 0, 0, 3, velocity_ms=0.0, tdm_sign=1, n_rx=4) is None


def test_circular_median_wraps():
    values = [3.0, -3.0, 3.1]
    median = doa.circular_median(values)
    assert abs(abs(median) - 3.05) < 0.2, "median must wrap, not average through zero"


def test_circular_median_does_not_degrade_to_a_linear_median():
    """Pins the wrap handling against a regression to plain ``np.median``.

    The test above uses [3.0, -3.0, 3.1], whose *linear* median is 3.0 -- well
    inside its 0.2 tolerance of 3.05. So replacing the whole implementation
    with ``np.median`` passes it, and the only real property being asserted
    goes unguarded.

    These values separate the two: they straddle +/-pi evenly, so the linear
    median collapses to ~0.0 -- diametrically opposite the actual cluster --
    while a circular median stays on the cluster near +/-pi.
    """
    values = [3.10, 3.13, -3.13, -3.10]
    assert abs(float(np.median(values))) < 0.1, "fixture must separate the two definitions"
    median = doa.circular_median(values)
    assert abs(median) > 3.0, "collapsed toward zero, i.e. averaged through the wrap"
    assert median in values, "a median returns one of its inputs, never a synthesised value"


def test_only_a_coherent_iwr_horizontal_is_handed_to_the_camera():
    """P8-7: kiosk and replay share one rule; a low-coherence value is display only."""
    from types import SimpleNamespace

    from openflight.iwr6843.runtime import camera_horizontal_input

    coherent = SimpleNamespace(
        accepted=True,
        horizontal_deg=2.0,
        horizontal_confidence=0.9,
        horizontal_status="hlcmf_v1_accepted",
    )
    assert camera_horizontal_input(coherent) == (2.0, 0.9)
    low = SimpleNamespace(**{**vars(coherent), "horizontal_status": "hlcmf_v1_low_coherence"})
    assert camera_horizontal_input(low) == (None, None)
    refused = SimpleNamespace(**{**vars(coherent), "accepted": False})
    assert camera_horizontal_input(refused) == (None, None)
    assert camera_horizontal_input(None) == (None, None)
