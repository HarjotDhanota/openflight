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

The hint appears in `ball_observation.object_hint` with its ending frame sequence,
window size, mode, camera/search context, applied controls and host delivery time.
Old hints lose their selection. LiveView also discards completed associations
from superseded control/mode generations. Hints do not persist across calls.
The model cache retains weights only; it retains no scene pixels or decisions.

This first integration is diagnostic: it runs before the unchanged full geometry
search in the worker, and both results are delivered together. It adds processing
time and can already be stale when that search finishes. It does not accelerate
confirmation or change the preview overlay, exposure metering, metric diameter,
distance, height, identity, watcher or Save gates. A proposed hint always carries
`metric_eligible=false` and `confirmable=false`. Model boxes never become physical
diameters. Follow checks and independent Save searches do not run the model.

Missing/wrong weights, unsupported model outputs or inference errors retain full
geometry fallback and label the hint unavailable. With workers disabled or broken,
the existing geometry fallback runs but inference never moves into the server
process. No model assets or default settings are changed.

Saved recent exposure/marked-ball cases support further experiments, but older
dim cases remain missed. Live movement, occlusion, multiple balls, exposure changes
and sustained load still need qualification. A future scheduling or metering
change should be measured separately before claiming faster acquisition.
