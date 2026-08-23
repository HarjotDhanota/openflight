# Silhouette POC — existing free tools survey (2026-08-22)

*Companion to `2026-08-22-silhouette-poc-design.md`. What we can reuse instead of build. All items free/open-source unless noted. Codex: treat this as pre-seeded input to audit item A9 — verify licenses and current state before depending on any of them.*

## Adopt (changes what we build)

| Tool | What it gives us | Maps to spec | Notes |
|---|---|---|---|
| **[GolfDB / SwingNet](https://github.com/wmcnally/golfdb)** ([Kaggle mirror](https://www.kaggle.com/datasets/marcmarais/videos-160)) | **1,400 labeled real golf-swing videos** incl. event frames (impact!), bounding boxes, club type, view type — including down-the-line views near our vantage | §6 ML training/eval data; real-footage sanity checks for the classical extractor **without shooting any proxy footage ourselves** | CVPR-W 2019 dataset; 160×160 preprocessed clips provided. Low-res, but impact-frame labels + DTL views are exactly the free real-world test set we lacked. Check license for redistribution — link, don't commit. |
| **[Roboflow Universe golf datasets](https://universe.roboflow.com/search?q=class%3Agolf+club-handle)** | Pre-labeled club/head images: [golf-club segmentation, 9,211 imgs](https://universe.roboflow.com/fp-cdzly/golf-club-segmentation-batch-10), [club+head detection, 300 imgs, public domain](https://universe.roboflow.com/public-bezoe/golf-club---head-object-detection), [club tracking, 6,750 imgs](https://universe.roboflow.com/club-head-tracking/golf-club-tracking/dataset/2) | §6 ML leg — seed/augment training data; possibly skip most hand-labeling | Mixed licenses per dataset — Codex must check each. Quality varies; audit before trusting labels. |
| **[SAM 2/3 via CVAT](https://www.cvat.ai/resources/changelog/video-annotation-sam-2) or [AnyLabeling](https://anylabeling.nrl.ai/docs/sam)** | Prompt-click → tracked segmentation masks across video frames | §6 — hand-labeling the proxy/GolfDB eval sets becomes minutes, not days; also candidate **teacher model** to auto-label training data for the small student net | CVAT self-hosted is free; AnyLabeling fully local. This likely replaces `ml/dataset.py`'s hardest part. |
| **[GrabCAD](https://grabcad.com/library?sort=most_downloaded&tags=golf) / [CGTrader](https://www.cgtrader.com/3d-print-models/golf-club) club meshes** | Real driver + [Titleist 7-iron](https://grabcad.com/library/titleist-7-iron-golf-club-1) CAD/STL meshes | §2/§5 — upgrade from our analytic `driverhead.py` blob to true club geometry for rendering AND as the pose-fit template | Free accounts; check per-model license (personal-use is fine for research, flag before redistribution in fixtures). |

## Consider (saves work, not required)

| Tool | What it gives us | Maps to spec | Notes |
|---|---|---|---|
| **[Rerun](https://github.com/rerun-io/rerun)** (MIT) | Drop-in multimodal viewer: log frames, masks, 3D tracks, time series from Python; web viewer built in | §5 diagnostics + a **developer-grade debug view for free**, before Studio exists — during Phases 1–3 it gives us frame stepping + overlay inspection with ~10 lines of logging | Does NOT replace Studio (custom controls, clubface heatmap, criteria table stay ours) but removes all pressure to build debug UI early. Recommend adopting for `fusion/diagnostics.py` output. |
| **[BlenderProc](https://github.com/DLR-RM/BlenderProc)** (GPL) or **[Kubric](https://github.com/google-research/kubric)** (Apache-2) | Photoreal renders with free GT masks/depth/flow; physics; motion blur | §6 stretch goal (photoreal training data) — was already in Stage 0B roadmap | Only if compositing (SAM-teacher + GolfDB) proves insufficient. Kubric's license is friendlier; BlenderProc more mature for stills. |
| **[Ultralytics SAM2/YOLO-seg](https://docs.ultralytics.com/models/sam-2)** (AGPL) | One-line inference/fine-tune for the student net | §6 `ml/train.py` | AGPL is fine for our AGPL repo. |
| **[OpenShotGolf](https://github.com/jhauck2/OpenShotGolf)** (Godot) | Free GSPro-style sim that accepts launch-monitor data | Demo garnish: pipe POC shots into an actual sim visual | Zero-effort wow factor for the README GIF, strictly optional. |

## Looked at, not useful for this POC

- **[PiTrac](https://github.com/PiTracLM/PiTrac)** — closest DIY relative, but measures ball launch/spin via strobe from a side-ish vantage; no silhouette pose, no radar fusion. Its **strobe driver design and calibration tooling** stay relevant to the future spin camera (hardware phase), not this POC. Watch for its test images as extractor sanity inputs.
- **[ronheywood/opencv](https://github.com/ronheywood/opencv)** — early-stage YOLO ball-detection experiments; nothing beyond what PR #215 already has.
- **Commercial sim SDKs (GSPro API etc.)** — repo already has `sim/` connectors; out of POC scope.
- **No off-the-shelf "golf club pose from behind" model exists** (searched; confirms spec assumption A9 — Codex to re-verify).

## Net effect on the spec

1. **§6 ML leg gets cheaper and better**: teacher–student (SAM2/3 auto-labels → small student net) over composites + Roboflow data + GolfDB real frames; hand-labeling shrinks to QC.
2. **§6/§10 gains a real-footage eval set we didn't plan for**: GolfDB impact-frame clips as a no-hardware sanity gate for the classical extractor.
3. **§5 template upgrade**: real club meshes from GrabCAD replace/augment the analytic driver head — better template realism at zero cost.
4. **Phases 1–3 get instant debug UI** via Rerun logging, decoupling fusion development from Studio's timeline.
5. Nothing found replaces the core POC (generator, fusion, Studio, eval) — the build list stands.
