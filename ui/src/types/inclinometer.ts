/**
 * The server's `inclinometer_status` event: how the unit is standing.
 *
 * `status` is always a name, never a blank -- the placement gate's verdict
 * (`openflight/placement.py`), plus `disabled` when no tilt sensor is running.
 */
export type PlacementStatus =
  | 'ok'
  | 'tilted_pitch'
  | 'tilted_roll'
  | 'tilted_both'
  | 'no_reading'
  | 'no_expected_orientation'
  | 'disabled';

export interface InclinometerStatus {
  enabled: boolean;
  status: PlacementStatus;
  /** Snapshot selection: stable, moving, stale, sensor_error, ... */
  reading_status: string;
  /** Calibrated angles, degrees: pitch positive nose up, roll positive right side down. */
  pitch_deg: number | null;
  roll_deg: number | null;
  raw_pitch_deg: number | null;
  raw_roll_deg: number | null;
  /** What the enclosure geometry says a correctly placed unit should read. */
  expected_pitch_deg: number | null;
  expected_roll_deg: number | null;
  pitch_error_deg: number | null;
  roll_error_deg: number | null;
  tolerance_deg: number;
  reason: string;
}
