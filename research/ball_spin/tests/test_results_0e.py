from pathlib import Path


def test_results_0e_contains_headline_answers():
    text = Path("research/ball_spin/RESULTS_0E.md").read_text(encoding="utf-8")

    for heading in [
        "## Headline Numbers",
        "## Capture Spec",
        "## Vantage And Stereo",
        "## Iron And Wedge Wrap Limits",
        "## Detector Quality Gate",
    ]:
        assert heading in text
