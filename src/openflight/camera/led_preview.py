"""Make a local, offline ROI picker from a declared fit capture."""

from __future__ import annotations

import argparse
import base64
import html
import json
import struct
import zlib
from pathlib import Path

import numpy as np


def _png(image: np.ndarray) -> bytes:
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    height, width = image.shape
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    pixels = b"".join(b"\0" + row.tobytes() for row in image)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )


def write_preview(capture_dir: Path, output: Path, frame_index: int | None = None) -> int:
    """Select only from fit data; the picker never edits captures or the manifest."""
    metadata = json.loads((capture_dir / "metadata.json").read_text(encoding="utf-8"))
    if metadata.get("tester_setup", {}).get("led_timing", {}).get("split") != "fit":
        raise ValueError("choose an LED fit capture; validation frames cannot select the ROI")
    with np.load(capture_dir / "frames.npz", allow_pickle=False) as archive:
        frames = np.asarray(archive["frames"])
    if frames.ndim != 3 or frames.dtype != np.uint8 or len(frames) < 3:
        raise ValueError("expected at least three monochrome uint8 frames")
    if frame_index is None:
        baseline = np.median(frames[:3], axis=0)
        frame_index = int(np.argmax(np.max(frames.astype(np.float32) - baseline, axis=(1, 2))))
    if not 0 <= frame_index < len(frames):
        raise ValueError("frame index lies outside the capture")
    image = frames[frame_index]
    height, width = image.shape
    encoded = base64.b64encode(_png(image)).decode("ascii")
    caption = html.escape(f"{capture_dir.name}: fit frame {frame_index}")
    document = f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><title>Camera LED region selection</title>
<style>body{{font:16px system-ui;max-width:1000px;margin:2rem auto;padding:0 1rem}}
canvas{{display:block;width:100%;max-width:{width}px;touch-action:none;cursor:crosshair;
image-rendering:pixelated;outline:1px solid #888}}output{{font-family:monospace;display:block;margin:1rem 0}}
</style><h1>Camera LED region selection</h1><p>{caption}</p>
<p>Drag a box around the LED. Coordinates refer to the saved image, starting at zero;
the right and bottom edges are exclusive. Copy the result into the next recording command.</p>
<canvas width="{width}" height="{height}"></canvas><output id="roi">Select the LED region.</output>
<p>This preview uses fit data only and changes no recording or calibration.</p>
<script>
const canvas=document.querySelector('canvas'), context=canvas.getContext('2d');
const image=new Image(); let anchor=null, box=null;
function point(event){{const r=canvas.getBoundingClientRect();return [
 Math.max(0,Math.min(canvas.width-1,Math.floor((event.clientX-r.left)*canvas.width/r.width))),
 Math.max(0,Math.min(canvas.height-1,Math.floor((event.clientY-r.top)*canvas.height/r.height)))];}}
function draw(){{context.drawImage(image,0,0);if(box){{context.strokeStyle='#00ff66';
 context.lineWidth=canvas.width/canvas.getBoundingClientRect().width;
 context.strokeRect(box[0]+.5,box[1]+.5,box[2]-box[0]-1,box[3]-box[1]-1);}}}}
function update(event){{const p=point(event);box=[Math.min(anchor[0],p[0]),Math.min(anchor[1],p[1]),
 Math.max(anchor[0],p[0])+1,Math.max(anchor[1],p[1])+1];draw();
 document.querySelector('#roi').textContent='--roi '+box.join(',');}}
canvas.addEventListener('pointerdown',event=>{{anchor=point(event);canvas.setPointerCapture(event.pointerId);update(event);}});
canvas.addEventListener('pointermove',event=>{{if(anchor)update(event);}});
canvas.addEventListener('pointerup',event=>{{if(anchor)update(event);anchor=null;}});
canvas.addEventListener('pointercancel',()=>{{anchor=null;}});
image.onload=draw;image.src='data:image/png;base64,{encoded}';
</script></html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        handle.write(document + "\n")
    return frame_index


def preview_main(argv: list[str] | None = None) -> int:
    """Export a standalone picker; no browser, server or network is started."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture_dir", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--frame-index", type=int)
    args = parser.parse_args(argv)
    try:
        index = write_preview(args.capture_dir, args.out, args.frame_index)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    print(f"Fit frame {index}: open {args.out} locally and select the LED region")
    return 0
