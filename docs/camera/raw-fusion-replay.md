# Raw radar and camera replay

`scripts/analysis/replay_raw_fusion.py` replays one logged shot without changing
the session or capture files. It runs saved OPS I/Q through the production
`RollingBufferProcessor`, can run the complete production IWR6843 ball and club
estimator from a recorded per-shot snapshot, and can run both the hash-bound
recorded camera context and a context rebuilt from replayed radar evidence.

```bash
uv run --extra camera python scripts/analysis/replay_raw_fusion.py \
  session_logs/session_RUN.jsonl 12 \
  --ops-sample-rate-hz 30000 \
  --club 7-iron \
  --projection-manifest evidence/shot-12-projection.json \
  --camera \
  --iwr \
  --output replay-shot-12.json
```

For a new recording with no projection candidate, the normal command is:

```bash
uv run --extra camera python scripts/analysis/replay_raw_fusion.py \
  session_logs/session_RUN.jsonl 12 \
  --ops-sample-rate-hz 30000 \
  --club 7-iron \
  --iwr \
  --camera \
  --output replay-shot-12.json
```

To produce an input that `benchmark_accuracy.py` can score, add a separate
session candidate output. The selected positional shot still produces the usual
replay report; the candidate replays every positive `shot_number` recorded in
the session, including attempts whose raw stage is absent or fails.

```bash
uv run --extra camera python scripts/analysis/replay_raw_fusion.py \
  session_logs/session_RUN.jsonl 12 \
  --ops-sample-rate-hz 30000 \
  --club 7-iron \
  --iwr \
  --camera \
  --output replay-shot-12.json \
  --benchmark-candidate-output raw-replay-session-candidate.json
```

The candidate is one file per session, so it can be supplied once with
`benchmark_accuracy.py --candidate`. It retains `read` and `no_read` attempts,
the exact session-file SHA-256, and the captured runtime content-manifest hash
when the source snapshot is present and valid. Its grouping identity uses the
same allowlisted content-manifest hash computed over the replay checkout, keyed
by repository-relative path, so equal values mean replay read the same source
the capture snapshotted; loaded module bytes can still differ. Arm (session
location), rig-geometry snapshot hash, applied exposure and gain (median of the
per-frame values in `frames.npz`), setup hash and placement warning are read
from the recording; `source_identity_evidence` names where each came from, and a
value stays null with its reason only when the session did not record it.
Exposure, gain, setup hash and placement are per shot, because the ladder
changes controls per rung. It contains no physical attempt ledger, so
physical coverage remains unavailable until an operator provides a reviewed
reconciliation. OPS and club-speed metrics remain radial; any comparison to a
total-speed reference requires a reviewed conditional metric contract.

The OPS sample rate and club come from each capture's recorded processor
config (club falls back to the shot record); `replay_inputs` records the source.
A capture without either fails its OPS stage rather than guessing.
`--ops-sample-rate-hz` and `--club` are overrides for older captures and apply
uniformly to every replayed attempt; they are not a claim that those captures
used those settings. Captures are located inside the session folder: a recorded
Pi path is matched by its longest trailing part under the folder, so a copied or
extracted session replays unchanged, and nothing outside the folder is read. A one-shot `--camera-capture` override
is rejected with session-candidate output so it cannot be associated with the
wrong shot.

New captures store the IWR calibration, radar configuration, per-shot effective
tilt, OPS speeds, club, and recovery state inside the shot's hash-bound runtime
snapshot; `--iwr` restores those values. The explicit IWR file and geometry
flags (`--iwr-calibration`, `--iwr-config`, `--iwr-runtime-config`, and measured
geometry) remain a legacy/variant path and the report labels their source.
Recorded snapshots bind the canonical runtime payload hash, the exact radar
configuration bytes/hash, the calibration's semantic source payload and
recorded source-file hash, and the per-shot OPS/club/effective-tilt inputs.

The OPS stage hashes the raw saved samples and records the active processor
constants, club identity, spin-prior policy, and resolved spin prior. New live
captures preserve that exact payload. Older captures do not contain it, so
their extraction is reproducible with the stated current configuration but is
not claimed equivalent to the historical live run. The IWR stage calls the
same hardware-free production function as live processing, including OPS-guided
ball recovery, club extraction, impact recovery, and configured corrections.
Its runtime JSON must state every primitive option and the pre-shot recovery
observations. Missing inputs are reported explicitly; no hardware values are
inferred. Matching configuration hashes establish configuration identity only;
the session runtime provenance is needed to assess source-code identity.

The camera stage reports replay of the recorded frozen context separately from
the context rebuilt with replayed OPS scalars, accepted IWR angles/confidence,
and recomputed ball/club range tracks. Missing recomputed range evidence is set
to null and never falls back to an old recorded track. A recorded-context match
proves deterministic replay of those saved inputs; neither result proves that
the radar or camera evidence is physically accurate.

Every `--iwr` replay also writes `stages.moving_iwr_range`. This stage extracts
the moving-ball range fit without a configured tee and stores the fit plus its
range at every captured IWR loop time inside the fitted support. Apparent ranges
remain labelled apparent unless the shot carries a qualified range-bias record
that exactly matches the captured calibration hash and bias. The stage never
promotes tee range.

A moving-track tee-range candidate is produced only when the `shot_detected`
event already contains a `moving_range_evidence` object with both of these
records:

```jsonc
{
  "range_calibration": {
    "source_sha256": "<captured calibration source SHA-256>",
    "bias_m": <same finite range_bias_m recorded in the runtime snapshot>,
    "uncertainty_m": <non-negative measured uncertainty>,
    "qualified": true,
    "source": "<independent range-calibration evidence>"
  },
  "impact_time": {
    "time_s": <impact time in the IWR capture timeline>,
    "uncertainty_s": <non-negative measured uncertainty>,
    "source": "<independent impact-time evidence>",
    "qualified": true,
    "independent_of_iwr_range": true,
    "provenance": {
      "independence_basis": "<how independence was established>",
      "dependencies": ["<every input; must exclude iwr_range>"]
    }
  }
}
```

The boolean alone is insufficient: replay requires a non-empty independence
basis and an explicit dependency list that excludes `iwr_range`. Missing,
malformed, unqualified or circular timing is retained as a structured withheld
result. The candidate is always non-selectable and cannot count as independent
IWR support in the tee-range resolver.

When `--camera` is also requested, `stages.moving_camera_iwr_anchor` records the
anchor-free camera/IWR moving-ball diagnostic. Replay invokes it only when the
saved archive has aligned frames, host timestamps and a trigger timestamp; the
camera context restores an accuracy-qualified calibrated projection; the OPS
replay produces positive ball speed; the moving IWR track has qualified range
calibration; and `moving_range_evidence.camera_iwr_clock_mapping` is qualified
and hash-bound to provenance:

```jsonc
{
  "camera_iwr_clock_mapping": {
    "offset_s": <camera-trigger-relative to IWR-impact-relative offset>,
    "uncertainty_s": <non-negative mapping uncertainty>,
    "qualified": true,
    "source": "<independent timing bench>",
    "source_sha256": "<timing evidence SHA-256>",
    "provenance": {"<recorded provenance>": "<value>"}
  }
}
```

Otherwise the report lists every missing or unqualified prerequisite without
inventing a clock, calibration, speed or range. A run retains the selected path,
alternatives, scores and rejection reasons. The trigger must fall inside the
saved camera timestamps, a camera observation with IWR range support must land
within two delivered frame intervals of mapped impact, and back-projection is
capped at those two intervals. An impact pixel outside the saved image is
rejected. The anchor is conditioned on the same moving IWR range series, so it
is downstream diagnostic evidence and can never be recycled as independent
camera support for tee-range promotion.

The canonical OPS ball speed is an aggregate of several FFT windows and remains
radial. It must not be assigned the timestamp of one strong window. The measured
projection candidate in `openflight.speed_correction` accepts only one
instantaneous FFT-window radial reading, a measured OPS origin, a measured ball
position, and an independently measured 3D velocity direction associated with
the same shot and time window. It divides by their measured line-of-sight dot
product and records the association. The candidate is unvalidated and never
replaces canonical radial speed.

The optional projection manifest selects a replayed FFT reading by
`reading_index` and repeats its `expected_speed_mph` and
`expected_timestamp_ms`, session UUID, shot number, and canonical OPS capture
payload hash. It supplies an operator-declared common-clock capture origin; the
CLI derives the signed-64-bit FFT window bounds and center from that origin,
the selected reading, processor window size, and sample rate. The manifest also
supplies the direction timestamp, common clock and target-frame identities,
declared direction dependencies, measured origins/direction, operator-attested
sources, association tolerance, and a minimum projection conditioning
threshold. A binding or timing mismatch withholds the candidate. The declared
clock origin is retained as an assumption, not claimed as hardware timing proof.

Use this JSON template for `--projection-manifest`. Replace every angle-bracket
placeholder with evidence for the selected shot; the placeholders themselves
are intentionally not valid JSON values.

```jsonc
{
  "session_uuid": "<session_start.session_uuid>",
  "shot_number": <positive integer>,
  "ops_capture_payload_sha256": "<OPS stage canonical_capture_payload_sha256>",
  "reading_index": <zero-based overlapping_readings index>,
  "expected_speed_mph": <selected reading speed_mph>,
  "expected_timestamp_ms": <selected reading timestamp_ms>,
  "ops_capture_origin_ns": <operator-declared signed-64-bit common-clock origin>,
  "ops_origin_m": [<x>, <y>, <z>],
  "ball_position_m": [<x>, <y>, <z>],
  "trajectory_direction": [<x>, <y>, <z>],
  "direction_observed_at_ns": <signed-64-bit timestamp in the same clock domain>,
  "association_tolerance_ns": <non-negative integer>,
  "radial_clock_domain_id": "<declared clock-domain identity>",
  "direction_clock_domain_id": "<same clock-domain identity>",
  "radial_target_frame_id": "<declared target-frame identity>",
  "direction_target_frame_id": "<same target-frame identity>",
  "direction_dependencies": ["<each upstream quantity used by the direction fit>"],
  "minimum_projection": <finite value greater than 0 and at most 1>,
  "ops_geometry_source": "<operator-attested measured OPS-origin source>",
  "direction_source": "<operator-attested independent direction source>"
}
```

`direction_dependencies` must not contain an OPS radial-speed constraint. The
CLI supplies `shot_id`, `direction_shot_id`, radial quantity, and derived radial
window timestamps itself; including arbitrary alternatives cannot change those
bindings.
