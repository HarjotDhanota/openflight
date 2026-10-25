import { isMeasured } from '../utils/units';
export type SpinQuality = 'high' | 'medium' | 'low' | 'experimental' | 'withheld';

export interface CameraReplay {
  id: string;
  frame_count: number;
  trigger_frame: number;
  playback_fps: number;
  duration_seconds: number;
  display_mirror_horizontal: boolean;
}

/** The carry's label as the server commits it; `measured` appears only in older logs. */
export type CarryStatus = Exclude<MetricLabel, 'experimental'>;

/** A metric's label; mirrors openflight.shot_metrics.LABELS. */
export const METRIC_LABELS = [
  'measured',
  'calculated',
  'model_geometry',
  'estimated',
  'experimental',
  'not_measured',
] as const;
export type MetricLabel = (typeof METRIC_LABELS)[number];

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
  /** `radial_ratio`: OPS radial ball over club speed, not full speeds. Null: unlabelled. */
  smash_factor_status?: string | null;
  /** The upstream schema's ball-speed table: null on every install, never shown (decision 1). */
  estimated_carry_yards: number | null;
  carry_range: [number, number] | null;
  club: string;
  profile_id?: string;
  profile_name?: string;
  /** The golfer's hand when the shot was detected; a later change never reaches it. */
  handed?: 'RH' | 'LH' | null;
  handed_source?: string | null;
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
  /** Why a sensor's horizontal is not on the shot (beyond a shank's reach, say). */
  launch_angle_horizontal_reason?: string | null;
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
  spin_source: Exclude<MetricLabel, 'experimental' | 'not_measured'> | null;
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
  /** Values are in `metrics.impact_*`. */
  impact_location?: ImpactLocationReading | null;
}

export interface ImpactLocationReading {
  status?: 'ok' | 'rejected' | 'dropped' | string;
  handedness?: 'right' | 'left' | string;
  club_category?: string | null;
}

export interface SessionStats {
  shot_count: number;
  /** Null when no shot has a measured value. */
  avg_ball_speed: number | null;
  max_ball_speed: number | null;
  min_ball_speed: number | null;
  std_dev?: number | null;
  avg_club_speed: number | null;
  avg_smash_factor: number | null;
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

function measuredValues(values: Array<number | null | undefined>): number[] {
  return values.filter(isMeasured);
}

export function filterShotsByProfile(shots: Shot[], profileId: string): Shot[] {
  if (!profileId) return [];
  return shots.filter((shot) => shot.profile_id === profileId);
}

/** The latest of these shots hit with ``club``, or null when none was. */
export function latestShotWithClub(shots: readonly Shot[], club: string): Shot | null {
  for (let index = shots.length - 1; index >= 0; index -= 1) {
    if (shots[index].club === club) return shots[index];
  }
  return null;
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
 * The carry the kiosk shows, or null; mirrors the server's `Shot.reported_carry_yards`.
 * The flight model's carry only: the ball-speed table never stands in (owner decision 1).
 */
export function reportedCarryYards(shot: Shot): number | null {
  if (shot.carry_status === 'not_measured') return null;
  return isMeasured(shot.carry_spin_adjusted) ? shot.carry_spin_adjusted : null;
}

/** The reported carry's label, or null; an older log's `measured` carry reads `calculated`. */
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
 * A stat with nothing measured is null, never 0.
 */
export function computeStats(shots: Shot[]): SessionStats {
  const ballSpeeds = measuredValues(shots.map((s) => s.ball_speed_mph));
  const clubSpeeds = measuredValues(shots.map((s) => s.club_speed_mph));
  const smashFactors = measuredValues(shots.map((s) => s.smash_factor));
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
