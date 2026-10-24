"""The tester's lens choice, per-lens settings, listing and tilt calibration file."""

import pytest

from openflight.camera import lens_settings
from tests.test_lens_profiles import RIG, _catalogue, _rig  # noqa: F401  (fixtures)


def test_settings_default_validate_and_persist(tmp_path, catalogue):
    path = tmp_path / "unit" / "camera-lens.json"
    store = lens_settings.LensSettingsStore(path, catalogue)

    assert store.selected() is None
    assert store.settings("6mm") == {
        "modes": ["arm5", "arm6"],
        "default_arm": "arm5",
        "ladder": {
            "arm5": [300, 200, 150, 100, 75, 50, 30, 20, 10, 7, 5],
            "arm6": [300, 150, 75, 30, 15, 10, 7, 5, 3],
        },
    }
    saved = store.save_settings(
        "6mm", {"modes": ["arm6"], "default_arm": "arm6", "ladder": {"arm6": [75, 300]}}
    )
    assert saved["ladder"]["arm6"] == [300, 75]
    with pytest.raises(ValueError):
        store.save_settings("6mm", {"modes": ["arm5"], "ladder": {"arm5": [123]}})
    with pytest.raises(ValueError):
        store.save_settings("6mm", {"modes": ["arm5"], "default_arm": "arm6"})
    with pytest.raises(ValueError):
        store.save_settings("6mm", {"modes": []})

    previous, changed = store.select("6mm")
    assert (previous, changed) == (None, True)
    assert store.select("6mm") == ("6mm", False)

    # a new server reads the same unit file
    again = lens_settings.LensSettingsStore(path, catalogue)
    assert again.selected() == "6mm"
    assert again.settings("6mm")["modes"] == ["arm6"]
    assert again.settings("8mm")["default_arm"] == "arm5"


def test_the_listing_carries_every_contract_field(catalogue, rig):
    store = lens_settings.LensSettingsStore(None, catalogue)

    listing = lens_settings.lens_listing(rig, catalogue, store)

    assert [item["lens_id"] for item in listing] == ["stock-2.8mm", "6mm", "8mm"]
    for item in listing:
        assert {
            "lens_id",
            "label",
            "focal_mm",
            "f_number",
            "hfov_deg",
            "intrinsics",
            "focal_px_by_arm",
            "settings",
        } <= set(item)
        assert set(item["focal_px_by_arm"]) == {"arm5", "arm6"}
    assert listing[0]["hfov_deg"] == 118.0
    # 6 mm: the pinhole's 2 atan(640 / 2000)
    assert listing[1]["hfov_deg"] == pytest.approx(35.5, abs=0.1)


def test_each_lens_keeps_its_own_tilt_calibration_file(tmp_path, catalogue):
    base = tmp_path / "camera-vertical-offset.json"

    assert lens_settings.tilt_calibration_path(base, None, catalogue) == base
    assert lens_settings.tilt_calibration_path(base, "stock-2.8mm", catalogue) == base
    assert lens_settings.tilt_calibration_path(base, "6mm", catalogue) == (
        tmp_path / "camera-vertical-offset-6mm.json"
    )
    assert lens_settings.tilt_calibration_path(None, "6mm", catalogue) is None
