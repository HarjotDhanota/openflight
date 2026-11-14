"""Build the 3-D viewer page: viewer3d/template.html with viewer3d/scene-shot1.json injected.
Also writes a stand-alone copy with a document skeleton for opening straight from disk. Usage: build_viewer.py"""
from common import AUDIT

folder = AUDIT / "viewer3d"
template = (folder / "template.html").read_text(encoding="utf-8")
scene = (folder / "scene-shot1.json").read_text(encoding="utf-8")
assert template.count("__SCENE__") == 1
page = template.replace("__SCENE__", scene)
(folder / "club-pose-3d.html").write_text(page, encoding="utf-8")
local = "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n" \
        "<style>body{margin:0}[hidden]{display:none!important}</style></head><body>\n" + page + "\n</body></html>\n"
(folder / "club-pose-3d-local.html").write_text(local, encoding="utf-8")
print("wrote club-pose-3d.html (%.0f kB) and club-pose-3d-local.html" % (len(page) / 1e3))
