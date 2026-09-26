# IQ8 offline qualification

`scripts/iwr6843/qualify_iq8.py` is an opt-in evidence tool. It compares
declared IQ16/IQ8 captures through the production estimators, but it does not
change the production IQ16 profile or qualify IQ8 for use on hardware.

## Run it

```text
uv run python scripts/iwr6843/qualify_iq8.py \
  --manifest evidence/iq-pairs.json \
  --out evidence/iq8-report.json \
  --cal config/iwr6843_calibration_reference.json \
  --iq16-config config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg \
  --iq8-config config/iwr6843_l3dump_dense_36f2ms_53bin_iq8.cfg \
  --tee-m 1.575 --net-m 4.064
```

The output path must not already exist. A complete offline comparison returns
zero. An invalid contract, unreadable input, or any failed pair returns nonzero
and writes deterministic incomplete evidence when it can do so safely.

## Manifest v2

Every capture object needs `path`, a finite positive `ball_speed_mph`, and any
shared estimator inputs (`club_speed_mph` and `club`) used for that capture.
Every pair has a unique `pair_id` and exactly one of these match bases:

- `same_raw_cube`: both dumps were encoded directly from one immutable complex
  cube. `comparison_provenance.source.kind` is `raw_cube`; both transformation
  operations are `encode_from_raw_cube`.
- `same_event_derived_encoding`: one dump is the captured event and the other
  is derived from it. The operations are `captured_event` and
  `derived_encoding`; the latter names its `source_representation`.
- `reference_matched_distinct_swings`: the captures are different swings. Each
  has its own event and reference ID, reference values, and hash-verified
  provenance. A separate reviewed match artifact explains why they are paired.

The first two bases use this shared shape:

```json
{
  "schema": "openflight.iwr6843_iq_pair_manifest.v2",
  "experiment_id": "iq8-screen-001",
  "pairs": [{
    "pair_id": "pair-001",
    "match_basis": "same_raw_cube",
    "comparison_provenance": {
      "source": {"kind": "raw_cube", "id": "cube-001", "path": "cube.bin", "sha256": "<64 hex>"},
      "profile_identity": {"id": "profile-001", "sha256": "<64 hex>"},
      "cadence_identity": {"id": "cadence-001", "sha256": "<64 hex>"},
      "transformations": {
        "iq16": {"operation": "encode_from_raw_cube", "tool": "encoder", "version": "1", "provenance_path": "encoder.json", "provenance_sha256": "<64 hex>"},
        "iq8": {"operation": "encode_from_raw_cube", "tool": "encoder", "version": "1", "provenance_path": "encoder.json", "provenance_sha256": "<64 hex>"}
      }
    },
    "iq16": {"path": "shot-iq16.l3dump", "ball_speed_mph": 100.0},
    "iq8": {"path": "shot-iq8.l3dump", "ball_speed_mph": 100.0}
  }]
}
```

For `same_event_derived_encoding`, change the source kind to `recorded_event`.
Give one transformation `captured_event`; give the other `derived_encoding`
and `"source_representation": "iq16"` (or `iq8`). Profile identity is the
canonical hash of configuration commands excluding only representation and
IQ8 scale. Cadence identity hashes exact chirp count, ordered phase windows and
stride-derived frame timing with the remaining capture geometry. Both hashes must
equal the declarations, so the shipped wide-IQ16 and dense-IQ8 profiles cannot
be presented as same-cadence encodings.

Distinct swings replace `comparison_provenance` with:

```json
{
  "reference_match": {"id": "match-001", "method": "reviewed interleaved speed bin", "provenance_path": "match.json", "provenance_sha256": "<64 hex>"},
  "iq16": {
    "path": "event-a.l3dump", "event_id": "event-a", "ball_speed_mph": 100.0,
    "reference": {"id": "ref-a", "source": "independent-launch-monitor", "provenance_path": "ref-a.json", "provenance_sha256": "<64 hex>", "metrics": {"launch_angle_deg": 17.2, "horizontal_deg": 0.8}}
  },
  "iq8": {
    "path": "event-b.l3dump", "event_id": "event-b", "ball_speed_mph": 101.0,
    "reference": {"id": "ref-b", "source": "independent-launch-monitor", "provenance_path": "ref-b.json", "provenance_sha256": "<64 hex>", "metrics": {"launch_angle_deg": 17.4, "horizontal_deg": 0.7}}
  }
}
```

Reference metric sets and sources must match. Supported metrics are launch and
horizontal angle, club path, and attack angle, all in degrees. Distinct swings
are never subtracted directly: each estimate is scored against its own event's
reference, then error distributions and status agreement are summarized.

## Interpret the report

`evidence_sha256` binds deterministic estimator output and exact input,
configuration, calibration, source, and provenance hashes. It excludes local
paths, timestamps, Git state, and uncontrolled processing duration.
`qualification.evidence_status=complete` means only that the declared offline
contract was comparable and every pair processed. The report always keeps
`hardware_qualified=false`, `production_eligible=false`, and production-default
changes disabled. Metric availability uses only reference-eligible records;
status agreement uses its explicit status-comparable denominator.

Transport evidence reports sample payload and total dump bytes, reduction
fractions, and modeled UART time. The UART model is bytes times 10 bits (8N1)
divided by the declared baud. It excludes CLI setup, USB/driver buffering,
firmware packing, scheduling, recovery, and estimator work.

Before IQ8 can be considered for production, run randomized or interleaved
captures on the Pi under the complete live workload; retain failures/no-reads;
measure actual dump and final-output latency plus drops; score independently
reviewed reference matches against frozen accuracy and availability limits;
cover representative clubs and speeds; and retain IQ16 rollback. This tool and
synthetic/same-event evidence do not satisfy those hardware gates.
