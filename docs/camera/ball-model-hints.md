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
