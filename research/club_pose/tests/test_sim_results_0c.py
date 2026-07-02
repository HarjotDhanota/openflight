from pathlib import Path


def test_results_0c_contains_required_budget_tables():
    root = Path(__file__).resolve().parents[3]
    text = (root / "research" / "club_pose" / "sim" / "RESULTS_0C.md").read_text(
        encoding="utf-8"
    )

    for phrase in (
        "Driver Mono",
        "Driver Stereo",
        "Iron Mono",
        "Iron Stereo",
        "Dominant source",
        "Hardware requirements",
        "ok_rate",
    ):
        assert phrase in text
