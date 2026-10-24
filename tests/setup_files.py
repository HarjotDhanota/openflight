"""Setup records for tests that start the kiosk.

The record is the only way a box, aim, ball range, net range or scene reaches the kiosk,
so a test that needs one writes it here, stamped for the start it runs.
"""

from __future__ import annotations

from pathlib import Path

from openflight.camera import placement_box
from openflight.rig_geometry import RigGeometry
from openflight.setup_record import load, save, section_inputs

RIG = Path(__file__).resolve().parents[1] / "config" / "enclosure_v3_rig_geometry.json"
BOX_ID = "box-test"
BOX_PX = (480, 380, 800, 520)
BALL_PX = (640.0, 450.0)
BALL_DIAMETER_PX = 20.0


def rig_sha256(rig: Path = RIG) -> str:
    """The rig file's parameter hash, as the kiosk stamps a box with it."""
    return RigGeometry.from_json(rig).snapshot()["sha256"]


def write_setup(  # pylint: disable=too-many-arguments
    path: Path,
    *,
    lens_id: str | None = "stock-2.8mm",
    rig: str | None = None,
    tilt_deg: float | None = None,
    image_size_px: tuple[int, int] = (1280, 800),
    aim_yaw_deg: float | None = None,
    ball_range: dict | None = None,
    net_range_m: float | None = None,
    scene_lens_height_m: float | None = None,
    scene_uncertainty_m: float | None = None,
) -> dict:
    """Write a record with a confirmed box (everything else builds on it) and the sections
    asked for. ``rig`` is the rig hash; None is the default rig file's.

    ``ball_range`` holds ``range_m``, ``uncertainty_m`` and optionally ``side_offset_m``,
    ``source``, ``qualification`` and ``candidate_id``.
    """
    record = load(path)
    record["box"] = {
        "state": "confirmed",
        "id": BOX_ID,
        "box_px": [float(value) for value in BOX_PX],
        "ball_px": list(BALL_PX),
        "diameter_px": BALL_DIAMETER_PX,
        "image_size_px": list(image_size_px),
        "source": placement_box.SOURCE,
        "uncertainty": {"tilt_pad_deg": 1.0, "roll_pad_deg": 2.0},
        "inputs": section_inputs(
            "box", lens_id=lens_id, rig_sha256=rig or rig_sha256(), tilt_deg=tilt_deg
        ),
    }
    if aim_yaw_deg is not None:
        record["aim"] = {
            "state": "set",
            "aim_px": [640.0, 300.0],
            "floor_line": None,
            "yaw_deg": float(aim_yaw_deg),
            "iwr_yaw_deg": float(aim_yaw_deg),
            "uncertainty_deg": 0.8,
            "method": "direction",
            "source": "aim_point",
            "inputs": section_inputs("aim", box_id=BOX_ID),
        }
    if ball_range is not None:
        record["ball_range"] = {
            "state": "set",
            "side_offset_m": None,
            "ball_px": list(BALL_PX),
            "diameter_px": BALL_DIAMETER_PX,
            "source": "camera_prior",
            "qualification": None,
            "candidate_id": None,
            **ball_range,
            "inputs": section_inputs("ball_range", box_id=BOX_ID),
        }
    if net_range_m is not None:
        if aim_yaw_deg is None:
            raise ValueError("a net range builds on the aim: give aim_yaw_deg too")
        record["net_range"] = {
            "state": "set",
            "value_m": float(net_range_m),
            "uncertainty_m": None,
            "source": "empty_static_capture",
            "inputs": section_inputs(
                "net_range", floor_line=None, lens_id=lens_id, tilt_deg=tilt_deg
            ),
        }
    if scene_lens_height_m is not None:
        record["scene"] = {
            "state": "set",
            "lens_height_m": float(scene_lens_height_m),
            "uncertainty_m": scene_uncertainty_m,
            "source": "radar_solved_height",
            "inputs": section_inputs("scene", box_id=BOX_ID),
        }
    save(path, record)
    return record
