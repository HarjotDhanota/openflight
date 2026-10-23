"""The lens on the unit: profiles, focal length per mode, calibration lookup and settings."""

import json

import pytest

from openflight.camera import lens_profiles
from openflight.rig_geometry import RigGeometry

RIG = lens_profiles.DEFAULT_LENSES_PATH.parents[0] / "enclosure_v3_rig_geometry.json"


@pytest.fixture(name="catalogue")
def _catalogue():
    return lens_profiles.load_catalogue()


@pytest.fixture(name="rig")
def _rig():
    return RigGeometry.from_json(RIG)


def test_the_catalogue_lists_the_three_lenses_with_the_listing_facts(catalogue):
    assert catalogue.ids() == ["stock-2.8mm", "6mm", "8mm"]
    stock = catalogue.get("stock-2.8mm")
    assert (stock.focal_mm, stock.f_number, stock.hfov_deg) == (2.8, 2.2, 118.0)
    assert stock.tv_distortion_pct == pytest.approx(-17.0)
    assert catalogue.get("6mm").f_number == 2.0
    assert catalogue.get("8mm").f_number is None
    # None is the rig file's own lens
    assert catalogue.get(None).lens_id == "stock-2.8mm"
    with pytest.raises(ValueError):
        catalogue.get("12mm")


def test_nominal_focal_scales_the_rig_s_by_focal_length(catalogue, rig):
    stock = lens_profiles.nominal_focal_px_by_arm(rig, catalogue, "stock-2.8mm")
    six = lens_profiles.nominal_focal_px_by_arm(rig, catalogue, "6mm")
    eight = lens_profiles.nominal_focal_px_by_arm(rig, catalogue, "8mm")

    assert stock["arm5"] == pytest.approx(933.3334, abs=1e-3)
    assert stock["arm6"] == pytest.approx(466.6667, abs=1e-3)
    assert six["arm5"] == pytest.approx(933.3334 * 6.0 / 2.8, abs=1e-3)
    assert eight["arm6"] == pytest.approx(466.6667 * 8.0 / 2.8, abs=1e-3)


def test_an_unselected_lens_is_the_rig_file_s_and_says_so(catalogue, rig):
    lens = lens_profiles.active_lens(rig, catalogue, None)

    assert lens.lens_id is None
    assert lens.profile.lens_id == "stock-2.8mm"
    assert lens.intrinsics == "nominal"
    assert lens.basis == lens_profiles.NOT_SELECTED_BASIS
    assert "nominal intrinsics" in lens.label()
    assert lens.focal_px(1280) == pytest.approx(933.3334, abs=1e-3)
    assert lens.focal_px(320) == pytest.approx(466.6667, abs=1e-3)


def _artifact(lens_id, width=1280, height=800, fx=2010.0, status="candidate"):
    return {
        "version": 1,
        "status": status,
        "mode_profile": {
            "lens_id": lens_id,
            "saved_image": {"width": width, "height": height},
        },
        "mode_profile_sha256": "a" * 64,
        "camera_matrix": [[fx, 0.0, width / 2], [0.0, fx, height / 2], [0.0, 0.0, 1.0]],
        "distortion_coefficients": {"k1": -0.1, "k2": 0.0, "p1": 0.0, "p2": 0.0, "k3": 0.0},
    }


def test_a_checkerboard_calibration_for_the_lens_replaces_the_nominal_focal(
    tmp_path, catalogue, rig
):
    folder = tmp_path / "optical"
    folder.mkdir()
    (folder / "six.json").write_text(json.dumps(_artifact("6mm", fx=2010.0)), encoding="utf-8")
    (folder / "other.json").write_text(json.dumps(_artifact("8mm", fx=9.0e3)), encoding="utf-8")
    (folder / "failed.json").write_text(
        json.dumps(_artifact("stock-2.8mm", status="failed")), encoding="utf-8"
    )

    six = lens_profiles.active_lens(rig, catalogue, "6mm", [folder])
    stock = lens_profiles.active_lens(rig, catalogue, "stock-2.8mm", [folder])

    assert six.intrinsics == "calibrated"
    assert six.focal_px_by_arm["arm5"] == pytest.approx(2010.0)
    # 640x400 bins 2x2: half the focal length
    assert six.focal_px_by_arm["arm6"] == pytest.approx(1005.0)
    assert "checkerboard" in six.label()
    # a failed fit never counts
    assert stock.intrinsics == "nominal"


def test_the_rig_takes_the_lens_s_focal_in_its_own_mode(catalogue, rig):
    lens = lens_profiles.active_lens(rig, catalogue, "6mm")

    scaled = lens_profiles.rig_with_lens(rig, lens)

    assert scaled.image_width == rig.image_width
    assert scaled.focal_px == pytest.approx(rig.focal_px * 6.0 / 2.8)
