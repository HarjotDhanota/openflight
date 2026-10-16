"""Local ROI selection preserves pixels and keeps validation data untouched."""

import struct
import zlib

import numpy as np
import pytest

from openflight.camera.led_preview import _png, preview_main, write_preview
from tests.test_camera_led_calibration import write_capture


def test_png_encodes_original_grayscale_pixels():
    image = np.array([[0, 1, 255], [128, 64, 32]], dtype=np.uint8)
    encoded = _png(image)
    assert encoded[:8] == b"\x89PNG\r\n\x1a\n"
    position = 8
    while position < len(encoded):
        length = struct.unpack(">I", encoded[position : position + 4])[0]
        kind = encoded[position + 4 : position + 8]
        data = encoded[position + 8 : position + 8 + length]
        if kind == b"IDAT":
            assert zlib.decompress(data) == b"\0\x00\x01\xff\0\x80\x40\x20"
        position += 12 + length


def test_picker_uses_fit_frames_and_refuses_to_overwrite(tmp_path):
    entry = write_capture(tmp_path, "fit", "fit")
    directory = tmp_path / entry["path"]
    output = tmp_path / "picker.html"
    assert write_preview(directory, output) >= 10
    document = output.read_text(encoding="utf-8")
    assert "data:image/png;base64," in document
    assert "--roi " in document
    assert "https://" not in document and "http://" not in document
    with pytest.raises(FileExistsError):
        write_preview(directory, output)


def test_validation_frames_cannot_be_used_to_choose_roi(tmp_path):
    entry = write_capture(tmp_path, "validation", "validation")
    with pytest.raises(ValueError, match="validation"):
        write_preview(tmp_path / entry["path"], tmp_path / "picker.html")


def test_explicit_preview_frame_and_bad_index(tmp_path):
    write_capture(tmp_path, "fit", "fit")
    assert (
        preview_main(
            [str(tmp_path / "fit"), "--out", str(tmp_path / "frame.html"), "--frame-index", "11"]
        )
        == 0
    )
    with pytest.raises(ValueError):
        write_preview(tmp_path / "fit", tmp_path / "invalid.html", 24)
