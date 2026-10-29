# Experimental setup-ball model hints

The tester can optionally attach golf-model object hints to placement-box search
evidence. Enable with `--ball-hint-model models/golf_ball_yolo11n_new_320.onnx`
when invoking `openflight.camera.tester_server`; leave search workers enabled.
The default is off. No new inference package is required beyond the camera
extra's OpenCV. The adapter checks the existing model's SHA-256 before loading.

Each worker loads one model lazily. Current contrast seeds, including rivals,
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

The worker completes the model task first, publishes its result without waiting
for geometry, then runs the unchanged full geometry search. Tasks are sequential;
the existing analysis thread and worker pool are reused without an extra queue.
The early API result can be inspected while fitting continues. This does not
accelerate confirmation or change the preview overlay, exposure metering, metric diameter,
distance, height, identity, watcher or Save gates. A proposed hint always carries
`metric_eligible=false` and `confirmable=false`. Model boxes never become physical
diameters. Follow checks and independent Save searches do not run the model.

Missing/wrong weights, unsupported model outputs or inference errors retain full
geometry fallback and label the hint unavailable. With workers disabled or broken,
the existing geometry fallback runs but inference never moves into the server
process. No model assets or default settings are changed.

Saved recent exposure/marked-ball cases support further experiments, but older
dim cases remain missed. Live movement, occlusion, multiple balls, exposure changes
and sustained load still need qualification. Cold worker/model startup can exceed
the freshness limit and must be reported separately from warm inference. An early
API hint is not a measured reduction in live acquisition or screen latency.
