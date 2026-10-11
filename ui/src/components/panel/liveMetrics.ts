import type { Shot, SpinQuality, SwingSpeedStats } from '../../types/shot';
import { getSwingSpeedMph, isCarryEstimated, isSwingSpeedShot, reportedCarryYards } from '../../types/shot';
import type { UnitSystem } from '../../utils/units';
import {
  NO_VALUE,
  firstMeasured,
  formatDistance,
  formatSpeed,
  getDistanceUnit,
  getSpeedUnit,
  isMeasured,
  unitIfMeasured,
} from '../../utils/units';
import { getHtmlLang, t } from '../../i18n';

export { NO_VALUE };

export interface LiveMetric {
  /** Stable key. Persisted as the promoted-hero choice, so do not rename. */
  id: string;
  label: string;
  value: string;
  unit?: string;
  subtext?: string;
  /** True when the value is modeled rather than measured. Rendered as an icon. */
  estimated?: boolean;
  confidence?: SpinQuality | null;
  /** Override confidence copy while preserving its dot level. */
  confidenceLabel?: string;
}

/**
 * Live table: ten metrics, always the same ten in the same canonical order, so
 * the grid never reflows between shots. Metrics the shot did not produce render
 * as {@link NO_VALUE} rather than disappearing. The selected metric is then
 * pinned to the top-left by {@link pinSelectedMetric}.
 */
export const LIVE_METRIC_COUNT = 10;

/** Metric count for a swing-speed session: a single 5-tile row. */
export const SWING_METRIC_COUNT = 5;

function formatOptionalAngle(value: number | null | undefined, signed = false): string {
  if (!isMeasured(value)) return NO_VALUE;
  const prefix = signed && value >= 0 ? '+' : '';
  return `${prefix}${value.toFixed(1)}`;
}

function angleUnit(value: number | null | undefined): string | undefined {
  return unitIfMeasured(value, '°');
}

function formatSpinRpm(rpm: number | null | undefined): string {
  if (!isMeasured(rpm)) return NO_VALUE;
  return rpm.toLocaleString(getHtmlLang(), { maximumFractionDigits: 0 });
}

function launchAngleQuality(confidence: number | null): SpinQuality | null {
  if (confidence === null) return null;
  if (confidence >= 0.7) return 'high';
  if (confidence >= 0.4) return 'medium';
  return 'low';
}

function shotShape(spinAxisDeg: number | null | undefined): string | undefined {
  if (!isMeasured(spinAxisDeg)) return undefined;
  if (spinAxisDeg > 2) return t('shape.fade');
  if (spinAxisDeg < -2) return t('shape.draw');
  return t('shape.straight');
}

function markEstimated(isEstimated: boolean): true | undefined {
  return isEstimated ? true : undefined;
}

/** The D-plane face angle rides on the club-path tile it is derived from. */
function withFaceAngle(
  subtext: string | undefined,
  faceDeg: number | null | undefined,
  faceStatus?: string | null
): string | undefined {
  if (!isMeasured(faceDeg)) return subtext;
  // an uncalibrated radar start direction is labelled, not hidden (P8-7)
  const basis = faceStatus === 'd_plane_estimate_azimuth_uncalibrated' ? 'D-plane, azimuth uncalibrated' : 'D-plane';
  const face = `face ${formatOptionalAngle(faceDeg, true)}° (${basis})`;
  return subtext ? `${subtext} · ${face}` : face;
}

/** Subtext naming a camera-derived horizontal launch source; radar and estimates get none. */
const HORIZONTAL_SOURCE_LABELS: Record<string, string> = {
  camera_assisted_experimental: 'camera assisted',
  camera_only_experimental: 'camera only',
  camera_legacy_fallback: 'camera legacy',
  camera_low_consensus: 'camera, low consensus',
  radar_low_coherence: 'radar, low coherence',
};

function experimentalStatus(status: string | null | undefined): string {
  if (!status || status === 'candidate_available') return 'candidate';
  return status.replace(/^rejected_/, 'rejected: ').replaceAll('_', ' ');
}

function buildBallStrikeMetrics(shot: Shot, unitSystem: UnitSystem): LiveMetric[] {
  const speedUnit = getSpeedUnit(unitSystem);
  // a fusion unit's unmeasured carry shows as no value, never the table (D17)
  const carry = reportedCarryYards(shot);
  const carryIsCommitted = carry !== null && isMeasured(shot.carry_spin_adjusted);
  const angleConfidence = launchAngleQuality(shot.launch_angle_confidence);
  const angleEstimated = shot.angle_source === 'estimated';
  // The horizontal tile reads its own axis (audit F5). Shots recorded before
  // the per-axis fields existed fall back to the shared ones.
  const horizontalConfidence = launchAngleQuality(
    shot.launch_angle_horizontal_confidence !== undefined
      ? shot.launch_angle_horizontal_confidence
      : shot.launch_angle_confidence
  );
  const horizontalEstimated =
    shot.launch_angle_horizontal_source !== undefined
      ? shot.launch_angle_horizontal_source === 'estimated'
      : angleEstimated;
  const horizontalSourceLabel = shot.launch_angle_horizontal_source
    ? HORIZONTAL_SOURCE_LABELS[shot.launch_angle_horizontal_source]
    : undefined;
  // A radar horizontal without the board's phase reference has its own zero (audit F8).
  const horizontalSubtext =
    shot.launch_angle_horizontal_status === 'azimuth_uncalibrated' ? 'azimuth uncalibrated' : horizontalSourceLabel;
  const fusedDeliveryAttempted = shot.experimental_fused_status != null;
  const attackAngle = firstMeasured(
    shot.club_angle_deg,
    shot.experimental_fused_attack_angle_deg,
    !fusedDeliveryAttempted ? shot.experimental_attack_angle_deg : null
  );
  const attackIsExperimental =
    !isMeasured(shot.club_angle_deg) &&
    (shot.experimental_fused_attack_angle_deg != null ||
      shot.experimental_fused_status != null ||
      shot.experimental_attack_angle_deg != null ||
      shot.experimental_attack_angle_status != null);
  const clubPath = firstMeasured(
    shot.club_path_deg,
    shot.experimental_fused_club_path_deg,
    !fusedDeliveryAttempted ? shot.experimental_club_path_deg : null
  );
  const clubPathIsExperimental =
    !isMeasured(shot.club_path_deg) &&
    (shot.experimental_fused_club_path_deg != null ||
      shot.experimental_fused_status != null ||
      shot.experimental_club_path_deg != null ||
      shot.experimental_club_path_status != null);

  return [
    {
      id: 'ball_speed',
      label: t('metric.ballSpeed'),
      value: formatSpeed(shot.ball_speed_mph, unitSystem, 1),
      unit: unitIfMeasured(shot.ball_speed_mph, speedUnit),
    },
    {
      id: 'carry',
      label: t('metric.carry'),
      value: formatDistance(carry, unitSystem, 0),
      unit: unitIfMeasured(carry, getDistanceUnit(unitSystem)),
      subtext: carryIsCommitted ? t('metric.spinAdjusted') : undefined,
      estimated: markEstimated(isCarryEstimated(shot)),
    },
    {
      id: 'club_speed',
      label: t('metric.clubSpeed'),
      value: formatSpeed(shot.club_speed_mph, unitSystem, 1),
      unit: unitIfMeasured(shot.club_speed_mph, speedUnit),
    },
    {
      id: 'smash',
      label: t('metric.smash'),
      value: isMeasured(shot.smash_factor) ? shot.smash_factor.toFixed(2) : NO_VALUE,
    },
    {
      id: 'launch_v',
      label: t('metric.vLaunch'),
      value: formatOptionalAngle(shot.launch_angle_vertical),
      unit: angleUnit(shot.launch_angle_vertical),
      estimated: markEstimated(isMeasured(shot.launch_angle_vertical) && angleEstimated),
      confidence: isMeasured(shot.launch_angle_vertical) ? angleConfidence : null,
    },
    {
      id: 'launch_h',
      label: t('metric.hLaunch'),
      value: formatOptionalAngle(shot.launch_angle_horizontal, true),
      unit: angleUnit(shot.launch_angle_horizontal),
      subtext: horizontalSubtext,
      estimated: markEstimated(isMeasured(shot.launch_angle_horizontal) && horizontalEstimated),
      confidence: isMeasured(shot.launch_angle_horizontal) ? horizontalConfidence : null,
      confidenceLabel: horizontalSourceLabel ? 'experimental' : undefined,
    },
    {
      id: 'spin',
      label: t('metric.spin'),
      value: formatSpinRpm(shot.spin_rpm),
      unit: unitIfMeasured(shot.spin_rpm, 'rpm'),
      estimated: markEstimated(isMeasured(shot.spin_rpm) && shot.spin_source === 'calculated'),
      confidence: isMeasured(shot.spin_rpm) ? shot.spin_quality : null,
    },
    {
      id: 'spin_axis',
      label: t('metric.spinAxis'),
      value: formatOptionalAngle(shot.spin_axis_deg, true),
      unit: angleUnit(shot.spin_axis_deg),
      subtext: shotShape(shot.spin_axis_deg),
    },
    {
      id: 'club_path',
      label: t('metric.clubPath'),
      value: formatOptionalAngle(clubPath, true),
      unit: angleUnit(clubPath),
      subtext: withFaceAngle(
        isMeasured(shot.club_path_deg)
          ? undefined
          : fusedDeliveryAttempted
            ? shot.experimental_fused_club_path_deg != null
              ? 'camera fused'
              : experimentalStatus(shot.experimental_fused_status)
            : clubPathIsExperimental
              ? experimentalStatus(shot.experimental_club_path_status)
              : undefined,
        shot.experimental_face_angle_deg,
        shot.experimental_face_angle_status
      ),
      confidence: clubPathIsExperimental ? (shot.experimental_fused_club_path_confidence ?? 'experimental') : null,
      confidenceLabel: shot.experimental_fused_club_path_confidence ? 'experimental' : undefined,
    },
    {
      id: 'club_aoa',
      label: t('metric.clubAoa'),
      value: formatOptionalAngle(attackAngle, true),
      unit: angleUnit(attackAngle),
      subtext: isMeasured(shot.club_angle_deg)
        ? undefined
        : fusedDeliveryAttempted
          ? shot.experimental_fused_attack_angle_deg != null
            ? 'camera fused'
            : experimentalStatus(shot.experimental_fused_status)
          : attackIsExperimental
            ? experimentalStatus(shot.experimental_attack_angle_status)
            : undefined,
      confidence: attackIsExperimental ? (shot.experimental_fused_attack_angle_confidence ?? 'experimental') : null,
      confidenceLabel: shot.experimental_fused_attack_angle_confidence ? 'experimental' : undefined,
    },
  ];
}

function buildSwingSpeedMetrics(shot: Shot, stats: SwingSpeedStats, unitSystem: UnitSystem): LiveMetric[] {
  const speedUnit = getSpeedUnit(unitSystem);

  return [
    {
      id: 'swing_last',
      label: t('metric.lastSwing'),
      value: formatSpeed(getSwingSpeedMph(shot), unitSystem, 1),
      unit: unitIfMeasured(getSwingSpeedMph(shot), speedUnit),
    },
    {
      id: 'swing_best',
      label: t('metric.best'),
      value: formatSpeed(stats.best_speed_mph, unitSystem, 1),
      unit: speedUnit,
      subtext: t('metric.profileImplement'),
    },
    {
      id: 'swing_avg',
      label: t('metric.average'),
      value: formatSpeed(stats.avg_speed_mph, unitSystem, 1),
      unit: speedUnit,
      subtext: t('metric.swingsCount', { count: stats.count }),
    },
    {
      id: 'swing_count',
      label: t('metric.swings'),
      value: String(stats.count),
      subtext:
        shot.swing_speed_reading_count === undefined
          ? undefined
          : t('metric.readingsCount', { count: shot.swing_speed_reading_count }),
    },
    {
      id: 'swing_implement',
      label: t('metric.implement'),
      value: shot.training_implement_label ?? shot.club,
      subtext:
        shot.swing_speed_trigger_mph === undefined
          ? undefined
          : t('metric.trigger', {
              speed: formatSpeed(shot.swing_speed_trigger_mph, unitSystem, 1),
              unit: speedUnit,
            }),
    },
  ];
}

/**
 * Build the fixed metric list for the Live panel. Returns {@link LIVE_METRIC_COUNT}
 * entries for a ball-strike shot and {@link SWING_METRIC_COUNT} for a swing-speed
 * one; the two sets share no ids, so a selected-metric choice never leaks across
 * modes.
 */
export function buildLiveMetrics(shot: Shot, unitSystem: UnitSystem, swingStats: SwingSpeedStats): LiveMetric[] {
  return isSwingSpeedShot(shot)
    ? buildSwingSpeedMetrics(shot, swingStats, unitSystem)
    : buildBallStrikeMetrics(shot, unitSystem);
}

/**
 * Put the selected metric first (top-left of the table) and keep the rest in
 * canonical order. Falls back to the first metric when `selectedId` is absent
 * from this list — which is the normal case right after switching between
 * ball-strike and swing-speed modes.
 */
export function pinSelectedMetric(metrics: LiveMetric[], selectedId: string | null): LiveMetric[] {
  if (metrics.length === 0) {
    return [];
  }

  const selectedIndex = metrics.findIndex((metric) => metric.id === selectedId);
  const index = selectedIndex === -1 ? 0 : selectedIndex;

  return [metrics[index], ...metrics.filter((_, i) => i !== index)];
}
