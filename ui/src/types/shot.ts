import { firstMeasured, isMeasured } from '../utils/units';
export type SpinQuality = 'high' | 'medium' | 'low' | 'experimental' | 'withheld';

export interface CameraReplay {
  id: string;
  frame_count: number;
  trigger_frame: number;
  playback_fps: number;
  duration_seconds: number;
  display_mirror_horizontal: boolean;
}

/**
 * What a shot's carry is, as the server commits it. A fusion unit (IWR or camera)
 * reports its flight's label, `calculated` (simulator on measured or calculated
 * inputs) or `model_geometry` (a spin or spin axis built on an assumed swing
 * geometry, D39), or `not_measured`, and never the ball-speed table (D17);
 * `measured` appears only in older logs. An OPS-only unit reports
 * `estimated`. Missing or null: a shot that never reached finalization.
 */
export type CarryStatus = 'measured' | 'calculated' | 'model_geometry' | 'estimated' | 'not_measured';

/**
 * A metric's label (openflight.shot_metrics.LABELS): `measured` (a sensor saw it),
 * `calculated` (a named model on measured or calculated inputs, D38),
 * `model_geometry` (rests on assumed swing geometry, D39), `estimated` (an OPS-only
 * install's labelled stand-in), `experimental` (shown before it is validated; it feeds
 * no other metric), `not_measured` (no value; the reason says why, D17).
 */
export type MetricLabel = 'measured' | 'calculated' | 'model_geometry' | 'estimated' | 'experimental' | 'not_measured';

/** One metric of one shot, as the server reports it in `Shot.metrics`. */
export interface ShotMetric {
  value: number | null;
  unit: string;
  status: MetricLabel;
  source: string | null;
  range: [number, number] | null;
  reason: string | null;
  /** The zero of a horizontal metric: `target_line`, `unit_boresight` or `mixed`. */
  frame: string | null;
  note: string | null;
  /** A flight metric's normalized-air value (sea level, 25 C). */
  normalized: { value: number; range: [number, number] | null; status: string | null } | null;
}

export interface Shot {
  mode?: 'rolling-buffer' | 'mock' | 'swing-speed';
  shot_number?: number | null;
  ball_speed_mph: number | null;
  club_speed_mph: number | null;
  smash_factor: number | null;
  /**
   * What smash_factor divides. `radial_ratio` (fusion units): the OPS radial ball
   * speed over the OPS radial club speed, not TrackMan's full speeds (D21, D37).
   * Missing or null: unlabelled, as an OPS-only unit has always shown it.
   */
  smash_factor_status?: string | null;
  /** The ball-speed table; null on a fusion unit, which never shows it. */
  estimated_carry_yards: number | null;
  carry_range: [number, number] | null;
  club: string;
  profile_id?: string;
  profile_name?: string;
  timestamp: string;
  impact_timestamp?: number | null;
  peak_magnitude: number | null;
  // Launch angle data (from K-LD7 radar (deprecated), camera, or estimation)
  launch_angle_vertical: number | null;
  launch_angle_horizontal: number | null;
  launch_angle_confidence: number | null;
  launch_angle_vertical_confidence?: number | null;
  launch_angle_horizontal_confidence?: number | null;
  launch_angle_vertical_source?: string | null;
  launch_angle_horizontal_source?: string | null;
  launch_angle_horizontal_status?: string | null;
  angle_source: 'radar' | 'camera' | 'estimated' | null;
  club_angle_deg: number | null;
  club_path_deg: number | null;
  experimental_attack_angle_deg?: number | null;
  experimental_attack_angle_status?: string | null;
  experimental_club_path_deg?: number | null;
  experimental_club_path_status?: string | null;
  experimental_fused_attack_angle_deg?: number | null;
  experimental_fused_club_path_deg?: number | null;
  experimental_fused_status?: string | null;
  experimental_fused_attack_angle_confidence?: 'high' | 'medium' | 'low' | 'withheld' | null;
  experimental_fused_club_path_confidence?: 'high' | 'medium' | 'low' | 'withheld' | null;
  experimental_camera_trace_deg?: number | null;
  experimental_face_angle_deg?: number | null;
  experimental_face_angle_status?: string | null;
  experimental_face_angle_path_source?: string | null;
  experimental_face_angle_launch_source?: string | null;
  experimental_aoa_offset_source?: string | null;
  iwr6843_horizontal_deg?: number | null;
  iwr6843_horizontal_confidence?: number | null;
  experimental_camera_horizontal_deg?: number | null;
  experimental_camera_horizontal_confidence?: number | null;
  experimental_camera_horizontal_status?: string | null;
  experimental_camera_iwr_delta_deg?: number | null;
  spin_axis_deg: number | null;
  // Rolling buffer mode spin data
  spin_rpm: number | null;
  spin_confidence: number | null;
  spin_quality: SpinQuality | null;
  spin_source: 'measured' | 'calculated' | 'model_geometry' | 'estimated' | null;
  spin_method?: string | null;
  spin_multipath_fade_hz?: number | null;
  carry_spin_adjusted: number | null;
  carry_status?: CarryStatus | null;
  swing_speed_duration_ms?: number;
  swing_speed_reading_count?: number;
  swing_speed_trigger_mph?: number;
  training_implement?: string;
  training_implement_label?: string;
  camera_replay?: CameraReplay | null;
  /** Every golf metric with its label, by metric id (openflight.shot_metrics). */
  metrics?: Record<string, ShotMetric> | null;
}

export interface SessionStats {
  shot_count: number;
  /** Null when no shot has a measured value (the server sends a non-finite one as null). */
  avg_ball_speed: number | null;
  max_ball_speed: number | null;
  min_ball_speed: number | null;
  /** Null with fewer than two measured ball speeds. */
  std_dev?: number | null;
  avg_club_speed: number | null;
  avg_smash_factor: number | null;
  /** Mean of the carries the kiosk reports; null when no shot has one. */
  avg_carry_est: number | null;
  // Rolling buffer mode spin stats
  avg_spin_rpm?: number | null;
  spin_detection_rate?: number;
  mode?: 'rolling-buffer';
}

export interface SessionState {
  stats: SessionStats;
  shots: Shot[];
  club?: string;
}

export interface TriggerDiagnostic {
  timestamp: string;
  trigger_type: string;
  accepted: boolean;
  reason: string;
  response_bytes: number;
  total_readings: number;
  outbound_readings: number;
  inbound_readings: number;
  peak_outbound_mph: number;
  peak_inbound_mph: number;
  all_outbound_speeds: number[];
  all_inbound_speeds: number[];
  peak_outbound_magnitude: number;
  peak_inbound_magnitude: number;
  latency_ms: number | null;
  // Present when accepted (shot created):
  ball_speed_mph?: number | null;
  club_speed_mph?: number | null;
  spin_rpm?: number | null;
  /** Diagnostic only: the ball-speed table at detection, never the shot's carry. */
  table_carry_estimate_yards?: number | null;
  iwr6843?: IWR6843Diagnostic;
}

export type IWR6843DiagnosticState = 'accepted' | 'rejected' | 'error';

export interface IWR6843Diagnostic {
  state: IWR6843DiagnosticState;
  reason: string;
  angle_deg?: number;
}

export interface TriggerDiagnosticUpdate {
  timestamp: string;
  iwr6843: IWR6843Diagnostic;
}

export interface TriggerStatus {
  mode: 'rolling-buffer' | 'mock' | 'swing-speed';
  trigger_type: string | null;
  radar_connected: boolean;
  radar_port: string | null;
  triggers_total: number;
  triggers_accepted: number;
  triggers_rejected: number;
}

export interface SwingSpeedStats {
  count: number;
  last_speed_mph: number;
  best_speed_mph: number;
  avg_speed_mph: number;
}

export interface SwingSpeedStatsFilter {
  profileId?: string | null;
  trainingImplement?: string | null;
  club?: string | null;
}

export function isSwingSpeedShot(shot: Shot | null): boolean {
  return shot?.mode === 'swing-speed' || shot?.club === 'Swing Speed';
}

export function getSwingSpeedMph(shot: Shot): number | null {
  return shot.club_speed_mph ?? shot.ball_speed_mph;
}

/** The measured values (the one rule, {@link isMeasured}); a not-measured metric is null. */
function measuredValues(values: Array<number | null | undefined>): number[] {
  return values.filter(isMeasured);
}

export function filterShotsByProfile(shots: Shot[], profileId: string): Shot[] {
  if (!profileId) return [];
  return shots.filter((shot) => shot.profile_id === profileId);
}

export function excludeShotsByProfile(shots: Shot[], profileId: string): Shot[] {
  if (!profileId) return shots;
  return shots.filter((shot) => shot.profile_id !== profileId);
}

function normalizeToken(value: string | null | undefined): string {
  return (value?.trim() || '').toLowerCase();
}

export function filterSwingSpeedShots(shots: Shot[], filter: SwingSpeedStatsFilter = {}): Shot[] {
  const scoped = filter.profileId ? filterShotsByProfile(shots, filter.profileId) : shots;
  const trainingImplement = normalizeToken(filter.trainingImplement);
  const club = normalizeToken(filter.club);

  return scoped.filter((shot) => {
    if (!isSwingSpeedShot(shot)) {
      return false;
    }

    if (trainingImplement) {
      const shotImplement = normalizeToken(shot.training_implement);
      const shotImplementLabel = normalizeToken(shot.training_implement_label);
      const shotClub = normalizeToken(shot.club);

      return (
        shotImplement === trainingImplement ||
        shotImplementLabel === trainingImplement ||
        shotClub === trainingImplement
      );
    }

    if (club && normalizeToken(shot.club) !== club) {
      return false;
    }

    return true;
  });
}

export function computeSwingSpeedStats(shots: Shot[], filter: SwingSpeedStatsFilter = {}): SwingSpeedStats {
  const swingSpeeds = measuredValues(filterSwingSpeedShots(shots, filter).map(getSwingSpeedMph));

  if (swingSpeeds.length === 0) {
    return {
      count: 0,
      last_speed_mph: 0,
      best_speed_mph: 0,
      avg_speed_mph: 0,
    };
  }

  const total = swingSpeeds.reduce((sum, speed) => sum + speed, 0);

  return {
    count: swingSpeeds.length,
    last_speed_mph: swingSpeeds[swingSpeeds.length - 1],
    best_speed_mph: Math.max(...swingSpeeds),
    avg_speed_mph: total / swingSpeeds.length,
  };
}

/**
 * The carry the kiosk shows for a shot, or null when it is not measured.
 *
 * The committed carry when there is one. The ball-speed table stands in only on
 * an OPS-only unit (`estimated`) or for a shot that never reached finalization;
 * a fusion unit's missing carry stays missing (D17). Mirrors the server's
 * `Shot.reported_carry_yards`.
 */
export function reportedCarryYards(shot: Shot): number | null {
  if (shot.carry_status === 'not_measured') return null;
  // the one missing-value rule ({@link isMeasured}): a non-finite carry is no carry
  if (isMeasured(shot.carry_spin_adjusted)) return shot.carry_spin_adjusted;
  const tableAllowed = shot.carry_status == null || shot.carry_status === 'estimated';
  return tableAllowed ? firstMeasured(shot.estimated_carry_yards) : null;
}

/**
 * The label on the reported carry, or null when there is none to label.
 *
 * The server's own label when the shot carries `metrics`. Otherwise its carry
 * status: a simulated carry is `calculated` (D38) whether its spin was measured
 * or calculated (an older log's `measured` is that same simulated carry), and
 * `model_geometry` when flown on model geometry (D39). An OPS-only or unfinalized
 * shot's carry is `estimated`.
 */
export function carryLabel(shot: Shot): MetricLabel | null {
  if (reportedCarryYards(shot) === null) return null;
  const server = shot.metrics?.carry?.status;
  if (server && server !== 'not_measured') return server;
  if (shot.carry_status === 'calculated' || shot.carry_status === 'measured') return 'calculated';
  if (shot.carry_status === 'model_geometry') return 'model_geometry';
  return 'estimated';
}

/**
 * Compute session stats from an array of shots.
 *
 * Only measured values count ({@link isMeasured}); a stat with nothing measured is
 * null, never 0: an empty session too, and std_dev with fewer than two ball speeds.
 */
export function computeStats(shots: Shot[]): SessionStats {
  const ballSpeeds = measuredValues(shots.map((s) => s.ball_speed_mph));
  const clubSpeeds = measuredValues(shots.map((s) => s.club_speed_mph));
  const smashFactors = measuredValues(shots.map((s) => s.smash_factor));
  // only reported carries: a fusion shot's missing carry is not averaged in
  const carries = measuredValues(shots.map(reportedCarryYards));

  const mean = (arr: number[]) => arr.reduce((a, b) => a + b, 0) / arr.length;
  const stdDev = (arr: number[]): number | null => {
    if (arr.length < 2) return null;
    const m = mean(arr);
    return Math.sqrt(arr.reduce((sum, x) => sum + (x - m) ** 2, 0) / (arr.length - 1));
  };

  return {
    shot_count: shots.length,
    avg_ball_speed: ballSpeeds.length > 0 ? mean(ballSpeeds) : null,
    max_ball_speed: ballSpeeds.length > 0 ? Math.max(...ballSpeeds) : null,
    min_ball_speed: ballSpeeds.length > 0 ? Math.min(...ballSpeeds) : null,
    std_dev: stdDev(ballSpeeds),
    avg_club_speed: clubSpeeds.length > 0 ? mean(clubSpeeds) : null,
    avg_smash_factor: smashFactors.length > 0 ? mean(smashFactors) : null,
    avg_carry_est: carries.length > 0 ? mean(carries) : null,
  };
}

/**
 * Get unique clubs from shots array.
 */
export function getUniqueClubs(shots: Shot[]): string[] {
  const clubs = new Set(shots.map((s) => s.club));
  return Array.from(clubs);
}
