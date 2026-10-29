"""Declared synthetic inputs for existing known-answer impact tests only."""

import hashlib

import numpy as np

from openflight.camera.impact_angle_evidence import REFERENCE_POINTS, SCHEMA


def bind_test_angles(shot, raw_path):
    digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    with np.load(raw_path, allow_pickle=False) as archive:
        stamps = archive["sensor_timestamp_ns"]
    for name, entry in shot["metrics"].items():
        entry.update(unit="deg", frame="unit_boresight", source="synthetic_independent_angle")
        entry["evidence"] = {
            "schema": SCHEMA,
            "shot_number": shot["shot_number"],
            "capture_npz_sha256": digest,
            "observation_ids": [f"synthetic-angle-{name}"],
            "depends_on": [],
            "conditioned_on": [],
            "event": "impact",
            "clock_domain": "camera_sensor",
            "sensor_interval_ns": [int(stamps[10]), int(stamps[11])],
            "reference_point": REFERENCE_POINTS[name],
            "sigma_deg": 2.0,
        }
    return shot
