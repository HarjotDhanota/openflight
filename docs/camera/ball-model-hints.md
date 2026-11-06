# Experimental setup-ball model hints

The tester can optionally attach golf-model object hints to placement-box search
evidence. Enable with `--ball-hint-model models/golf_ball_yolo11n_new_320.onnx`
when invoking `openflight.camera.tester_server`; leave search workers enabled.
The default is off. No new inference package is required beyond the camera
extra's OpenCV. The adapter checks the existing model's SHA-256 before loading.

With the tester CLI flag, workers load and compile the model during server
startup, before opening the preview. This startup cost is reported separately;
the synthetic warmup output never becomes an observation. Direct programmatic
users can request the same preheat through `configure_ball_search_workers`.
Missing/wrong weights leave full geometry available. Current contrast seeds, including rivals,
generate two shifted 256px crops for the 320px model. Two distinct crop origins
must overlap by more than 0.6 IoU and score at least 0.6. These experimental
thresholds came from saved-image development. Overlapping crops use the same
pixels and are not independent observations. Multiple qualifying objects remain
ambiguous. Unknown, mixed or invalid applied exposure/gain suppresses inference.

The hint appears early in `GET /api/tester/live` as the separate `object_hint`
field. It includes its ending frame sequence, window size, mode, camera/search
context, applied controls and host delivery time. It expires two seconds after
input delivery, even while geometry is still busy. Control/mode/analyzer changes
and stopping clear it; superseded callbacks cannot republish it. Snapshots copy
the evidence and never refresh its timestamp. Completed geometry also retains
the hint in `ball_observation.object_hint`, with stale selections removed.
The model cache retains weights only; it retains no scene pixels or decisions.

With the additional `--ball-hint-independent` experimental flag, the live preview
uses a separate analysis thread for model observations, sharing
the existing process pool with geometry. It samples the latest frames again when
the previous model task finishes, even if geometry is still fitting or following
an earlier ball position. Only one model task can be pending; old runs cannot
queue more. Two workers allow independent progress. One worker serializes tasks.
The geometry loop skips its old inline model task when this loop is active.
Direct one-shot searches can still deliver a hint callback before geometry.
This switch is off by default: a paced Pi replay showed more model updates but
slower geometry through CPU contention. Without it, the serialized callback path
is retained. Neither mode accelerates the geometry solver or changes the preview overlay, exposure metering, metric diameter,
distance, height, identity, watcher or Save gates. A proposed hint always carries
`metric_eligible=false` and `confirmable=false`. Model boxes never become physical
diameters. Follow checks and independent Save searches do not run the model.

Missing/wrong weights, unsupported model outputs or inference errors retain full
geometry fallback and label the hint unavailable. With workers disabled or broken,
the existing geometry fallback runs but inference never moves into the server
process. No model assets or default settings are changed.

Saved recent exposure/marked-ball cases support further experiments, but older
dim cases remain missed. Live movement, occlusion, multiple balls, exposure changes
and sustained load still need qualification. A subsequent movement recording
exposed missed relocated balls and an occluder mistaken for a ball. Faster refresh
does not fix classification. This remains an experimental API, off by default;
no live tracking, size accuracy or Ready-time improvement is qualified.

## Audit data before retraining

Run the read-only preflight on each source dataset before combining it, and on
the combined dataset before training:

```bash
uv run python scripts/vision/audit_golf_training.py /path/to/source-dataset
uv run python scripts/vision/audit_golf_training.py /path/to/combined_dataset
```

The JSON report lists errors, image/label hashes, explicit empty annotations and
byte-identical images shared across splits. A nonzero exit means the data needs
review. The tool never rewrites labels, deletes files or starts training.

The YOLO11 training notebook calls this check before copying each downloaded
dataset and immediately before training. Upload `audit_golf_training.py` beside
the notebook in Colab, or place it in the notebook's working directory. It uses
the notebook's existing PyYAML dependency. Classes must already describe one
golf-ball class with ID zero; other objects must not be silently renamed.

The supported layout has `data.yaml` pointing to paired `images` and `labels`
directories for train/validation and optional test splits. Empty label files
explicitly mark negative images; missing labels require review. Segmentation
rows and image-list manifests are not accepted as detection data. This checks
annotation format and exact file duplication, not annotation truth, image
decoding, recompressed duplicates or independence between adjacent frames.
Keep complete recording sessions and setups separate when evaluating a model.
Parent-relative Roboflow paths use the existing-directory-first fallback used
by the trainer; the audit records the files it actually read.
It scans JPEG, PNG, BMP, WebP and TIFF images; other image formats need separate
review.
