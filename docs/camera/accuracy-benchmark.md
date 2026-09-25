# Reviewed reference accuracy benchmark

The M2 benchmark consumes an existing session export or a normalized candidate
result document. Both live and replay producers use the same scoring core.
It keeps failed/excluded sensor attempts, reports compatibility failures and
scores only explicitly reviewed one-to-one reference matches. It does not
automatically align shots by row order or select product acceptance limits.

## Prepare and review matches

Use exported run directories containing `manifest.json`, `shots.csv`, the
preserved session log, ledger and runtime snapshot, plus the original Trackman
CSV. Repeat `--candidate` for every held-out session. The set hash is derived
from the session UUID and exact hash of every member, independent of argument
order. Duplicate paths, session UUIDs and attempt IDs are rejected.

```bash
uv run python scripts/analysis/benchmark_accuracy.py --candidate run-a --candidate run-b --reference reference.csv --write-match-template matches.json
```

The template records source hashes and lists candidate attempt IDs and parsed
reference IDs. It contains no approved matches. Inspect the physical sequence,
operator notes and original reference rows. Add only associations you can
establish; leave missed, ambiguous and reference-missing attempts visible.
Do not repair a poor comparison by deleting bad matches after looking at error.

Declare each metric's fields, unit, semantic contract and compatibility basis.
For example, a reviewed vertical launch comparison uses:

```json
{
  "vertical_launch": {
    "candidate_field": "launch_angle_vertical",
    "reference_field": "launch_angle_vertical",
    "unit": "deg",
    "contract_id": "ball.launch.vertical.deg.v1",
    "compatibility_basis": "Replace with the reviewed frame, sign and launch-time definition for these devices."
  }
}
```

Put that object under `metric_contracts` in the template and replace the basis
with the actual review. A match entry looks like:

```json
{
  "candidate_attempt_id": "SESSION_UUID:1",
  "reference_id": "trackman-row-1"
}
```

Use IDs from the template. The reference ID identifies the parsed reference
record, not necessarily a physical spreadsheet line. Keep the reference bytes
unchanged after review. A known reference can be associated with a no-read;
that records reference availability without inventing a candidate measurement.

Then score the reviewed inputs:

```bash
uv run python scripts/analysis/benchmark_accuracy.py --candidate run-a --candidate run-b --reference reference.csv --matches matches.json --output accuracy-report.json
```

That command remains a diagnostic run and exits successfully with
`acceptance.status: incomplete`. To run a gate, create and freeze a versioned
criteria file before collection. Because future session UUIDs do not yet exist,
freeze a `heldout_selection_rule` in the criteria. After collection, create a
separate held-out manifest naming the selected UUIDs, the exact criteria SHA-256,
the same rule, selection time and operator provenance. Supply both with
`--criteria acceptance.json --heldout-manifest heldout.json`. Legacy profiles
may still contain literal UUIDs when those identities really were known before
collection. The software checks declarations and hashes; it cannot prove that a
file was authored at the stated time. The held-out manifest has this exact v1
shape:

```json
{
  "schema_version": 1,
  "criteria_sha256": "REPLACE_WITH_EXACT_ACCEPTANCE_JSON_SHA256",
  "selection_rule": "REPLACE_WITH_THE_RULE_FROZEN_IN_ACCEPTANCE_JSON",
  "selected_at": "REPLACE_WITH_OFFSET_AWARE_TIMESTAMP_AFTER_COLLECTION",
  "provenance": "REPLACE_WITH_THE_OPERATOR_REVIEW_RECORD",
  "held_out_session_uuids": ["REPLACE_WITH_SESSION_UUIDS"]
}
```

The CLI validates that `selected_at` is timezone-aware, follows the criteria
freeze, and is on or after every selected session start. The CLI exits 0 only
when an explicitly supplied profile passes, 1 when limits fail,
3 when required evidence is incomplete, and 2 for malformed inputs.

The v1 profile names the candidate identity and exact group values that every
held-out row must match. It freezes the session-selection rule, an
operator-declared timestamp with an explicit timezone, and the independent
reference qualification evidence. Session UUIDs come from the later hash-bound
manifest unless a legacy profile could truthfully predeclare them. A skeleton
is shown below. Null values are placeholders; using the draft produces
`incomplete`, never a pass.

```json
{
  "schema_version": 1,
  "profile_id": null,
  "version": null,
  "predeclared_at": null,
  "session_timezone": null,
  "candidate_identity": null,
  "required_group_values": null,
  "held_out_session_uuids": null,
  "heldout_selection_rule": null,
  "reference_qualification": null,
  "minimum_read_coverage": null,
  "minimum_reference_match_coverage": null,
  "session_bootstrap": {"iterations": 2000, "seed": 0},
  "metrics": null,
  "physical_coverage": null
}
```

`physical_coverage` is optional. When used, it contains
`minimum_read_coverage` and `metric_minimum_availability` keyed exactly like
`metrics`. It requires `--attempt-reconciliation`, a reviewed v1 document bound
to the candidate-set hash. Its `mappings` account once for every effective
ledger entry with `session_uuid`, `ledger_entry_id`, and a sensor attempt ID or
null. `unmatched_sensor_attempts` must account once for every remaining sensor
attempt and classify it as `warmup` or `false_trigger`. Incomplete, duplicate,
cross-session, or unreviewed mappings are rejected. The tool never infers these
physical identities from counts or timing.

`--write-criteria-template` writes the required metric keys with null values so
the draft remains incomplete until the study protocol supplies every limit. It
does not add `physical_coverage`; add that optional object only when a reviewed
physical-attempt reconciliation will be supplied.

Use this exact reconciliation schema. `reviewed` must be true and `provenance`
must identify the operator review record. Include every effective ledger entry
once. A null `sensor_attempt_id` explicitly records no matched sensor attempt.
Account for every remaining sensor attempt once under
`unmatched_sensor_attempts`.

```json
{
  "schema_version": 1,
  "candidate_sha256": "REPLACE_WITH_CANDIDATE_SHA256_FROM_MATCH_TEMPLATE",
  "reviewed": true,
  "provenance": "REPLACE_WITH_OPERATOR_REVIEW_RECORD",
  "mappings": [
    {
      "session_uuid": "REPLACE_WITH_SESSION_UUID",
      "ledger_entry_id": "REPLACE_WITH_LEDGER_ENTRY_ID",
      "sensor_attempt_id": "REPLACE_WITH_SESSION_UUID:SHOT_NUMBER_OR_NULL"
    }
  ],
  "unmatched_sensor_attempts": [
    {
      "sensor_attempt_id": "REPLACE_WITH_SESSION_UUID:SHOT_NUMBER",
      "classification": "warmup"
    }
  ]
}
```

`classification` is exactly `warmup` or `false_trigger`. The generated match
template lists `candidate_attempt_ids` and `candidate_ledger_entries`. If the
profile requires physical coverage, replace its null with this shape; metric
keys must exactly match the reviewed contracts:

```json
{
  "minimum_read_coverage": null,
  "metric_minimum_availability": {
    "ball_speed": null,
    "club_speed": null,
    "vertical_launch": null,
    "horizontal_launch": null,
    "club_path": null,
    "attack_angle": null
  }
}
```

Each metric object must contain `minimum_comparable_pairs`,
`minimum_sessions` (at least two), `minimum_compatible_coverage`,
`gross_error_threshold`, `maximum_gross_error_rate`,
`maximum_absolute_bias`, `maximum_mae`, `maximum_rmse`,
`maximum_p90_absolute_error`, and `maximum_error`. These values must come from
the study protocol. The tool supplies none.

The six first-release comparisons use these registered combinations. The
metric keys are reviewer-chosen; these names keep the criteria and physical
coverage examples consistent. Each declaration still requires a concrete
`compatibility_basis` for the actual frame, sign, timing, and physical feature.

| Example key | Candidate field | Reference field | Unit | Contract ID |
|---|---|---|---|---|
| `ball_speed` | `experimental_ball_speed_total.value_mph` | `ball_speed_mph` | `mph` | `ball.speed.total.mph.v1` |
| `club_speed` | `club_speed_mph` | `club_speed_mph` | `mph` | `club.speed.ops_radial_vs_trackman.conditional.mph.v1` |
| `vertical_launch` | `launch_angle_vertical` | `launch_angle_vertical` | `deg` | `ball.launch.vertical.deg.v1` |
| `horizontal_launch` | `launch_angle_horizontal` | `launch_angle_horizontal` | `deg` | `ball.launch.horizontal.deg.v1` |
| `club_path` | `experimental_fused_club_path_deg` | `club_path_deg` | `deg` | `club.path.optical_feature_vs_face_center.conditional.deg.v1` |
| `attack_angle` | `experimental_fused_attack_angle_deg` | `attack_angle_deg` | `deg` | `club.attack.optical_feature_vs_face_center.conditional.deg.v1` |

Optional declaration labels include `candidate_source`, `reference_source`,
`reference_estimated`, and `validation`. They cannot override a recorded
withheld/rejected status or the adapter's source checks.

`session_timezone` is the explicit numeric UTC offset used only for legacy
session logs whose start timestamp has no offset, for example `-07:00`. Newer
logs provide a UTC-aware start field. The evaluator never assumes the host's
current timezone.

The report preserves the match-manifest hash and comparison declarations.
Changed sources require new review. An explicit `--overwrite` may replace a
previous report, but must not replace original inputs.

## Read the report

Read/error availability and matching are separate counts. The attempt
denominator comes from logged sensor attempts, including excluded/partial
ones. The operator ledger is reported separately: equal counts do not prove
identity reconciliation, and unobserved physical swings cannot be inferred
from logs. Physical availability remains unqualified without reconciliation.

Comparable pairs report signed bias (candidate minus reference), MAE, RMSE,
nearest-rank 90th-percentile absolute error and maximum absolute error.
Diagnostic error summaries remain descriptive. With a completed acceptance
profile, the gate also reports a deterministic percentile bootstrap for bias,
MAE and RMSE by resampling whole held-out sessions. It requires at least two
sessions and states the assumption that those sessions represent the target
population. P90 and maximum limits remain direct descriptive checks.

Coverage uses nominal Wilson intervals with the Bernoulli independence
assumption stated explicitly. Session clustering and incomplete physical
coverage limit their interpretation. See the
[NIST discussion of proportion intervals](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm).

Setup, session, mode, software and placement-warning groups remain identifiable.
`software_content_sha256` comes from the verified embedded runtime content
manifest and is the stable software grouping identity. The runtime ZIP hash is
also retained as artifact provenance, but ZIP timestamps can make that archive
hash differ across sessions with identical source content. Calibrated model,
mode-profile and stable-placement hashes separate calibrated and legacy fallback
rows.
Measured pitch/roll are observations, not individual grouping categories for
every tiny sensor fluctuation. Missing required identities prevent a group
from being treated as a qualified comparison; its attempts remain in the report.

The adapter enforces known field semantics and preserves withheld/rejected
values. In particular, OPS radial speed cannot be declared equal to Trackman
total speed just because both fields are in mph. Model-derived total-speed
candidates and estimated reference fields stay labeled. See the
[speed contract](speed-contract.md).

The first release can declare all six motion comparisons: ball speed, club
speed, vertical launch, horizontal launch, club path and attack angle. Club
speed is an explicitly reviewed OPS-radial-versus-Trackman conditional
contract. Club path and attack angle are conditional optical-feature versus
Trackman face-centre contracts. They remain unvalidated and require a concrete
compatibility basis in the match document. `camera_legacy_fallback` launch
values are retained with their source and withheld unless a separately reviewed
known contract supports them.

Normalized candidate JSON is a producer-declared contract for future replay
outputs. Hash binding identifies the supplied values and declarations; it
cannot prove that a producer's physical interpretation is correct. The
benchmark comparison rows remain diagnostic. A passing gate means the supplied
operator-attested reference qualification and frozen limits were met; it is not
software proof of independent calibration or a product accuracy certification.
The profile timestamp is checked against the recorded session starts, but the
software cannot prove when the criteria file was first authored.
