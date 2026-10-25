import type { MetricLabel, Shot, SpinQuality, SwingSpeedStats } from '../../types/shot';
import { carryLabel, getSwingSpeedMph, isSwingSpeedShot, reportedCarryYards } from '../../types/shot';
import { METRIC_CATALOG, isValueLabel, shotMetric } from '../../data/metricCatalog';
import { formatMetricValue } from './metricFormat';
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
import { getHtmlLang, t, type MessageKey } from '../../i18n';

export { NO_VALUE };

export interface LiveMetric {
  /** Stable key. Persisted as the promoted-hero choice, so do not rename. */
  id: string;
  label: string;
  value: string;
  unit?: string;
  subtext?: string;
  /** The shown value's label (server's `Shot.metrics` first); rendered as a badge. */
  status?: MetricLabel;
  /** Why the metric is not measured, when the tile shows no value. */
  reason?: string;
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

/** The smash is radial ball over radial club speed on every install, so it is labelled. */
export function smashSubtext(shot: Shot): string | undefined {
  return isMeasured(shot.smash_factor) && shot.smash_factor_status === 'radial_ratio' ? 'radial ratio' : undefined;
}

/** The server's metric id behind each live tile (openflight.shot_metrics). */
const TILE_METRIC: Record<string, string> = {
  ball_speed: 'ball_speed',
  carry: 'carry',
  club_speed: 'club_speed',
  smash: 'smash_factor',
  launch_v: 'launch_vertical',
  launch_h: 'launch_horizontal',
  spin: 'spin_rate',
  spin_axis: 'spin_axis',
  club_path: 'club_path',
  club_aoa: 'attack_angle',
};

const METRIC_UNIT: Record<string, string> = Object.fromEntries(METRIC_CATALOG.map((spec) => [spec.id, spec.unit]));

function labelIf(value: unknown, status: MetricLabel | undefined): MetricLabel | undefined {
  return isMeasured(value) ? status : undefined;
}

/** A model's spin keeps its label; an OPS spin of experimental quality is not a measurement. */
function spinLabel(shot: Shot): MetricLabel {
  if (shot.spin_source === 'ops_below_floor') return 'experimental';
  if (isValueLabel(shot.spin_source) && shot.spin_source !== 'measured') return shot.spin_source;
  return shot.spin_quality === 'experimental' ? 'experimental' : 'measured';
}

function withServerLabel(shot: Shot, metric: LiveMetric, unitSystem: UnitSystem): LiveMetric {
  const id = TILE_METRIC[metric.id];
  const record = shotMetric(shot, id);
  if (!record) return metric;
  const hasValue = isValueLabel(record.status) && isMeasured(record.value);
  const [value, unit] = formatMetricValue(
    { ...record, value: hasValue ? record.value : null, unit: METRIC_UNIT[id] },
    unitSystem,
    id
  );
  return {
    ...metric,
    value,
    unit: unit || undefined,
    status: hasValue ? record.status : undefined,
    reason: hasValue ? undefined : (record.reason ?? undefined),
    subtext: hasValue && metric.id === 'smash' ? smashSubtext(shot) : undefined,
    confidence: 'confidence' in metric || metric.id === 'spin_axis' ? null : undefined,
    confidenceLabel: undefined,
  };
}

/** The D-plane face angle rides on the club-path tile it is derived from. */
function withFaceAngle(
  subtext: string | undefined,
  faceDeg: number | null | undefined,
  faceStatus?: string | null,
  faceLabel?: MetricLabel
): string | undefined {
  if (!isMeasured(faceDeg)) return subtext;
  // the face's own label may follow ("_estimated", "_mixed_frames")
  const uncalibrated = faceStatus?.startsWith('d_plane_estimate_azimuth_uncalibrated') ?? false;
  const basis = uncalibrated ? 'D-plane, azimuth uncalibrated' : 'D-plane';
  const label = faceLabel ? `${t(`label.${faceLabel}` as MessageKey).toLowerCase()}, ` : '';
  const face = `face ${formatOptionalAngle(faceDeg, true)}° (${label}${basis})`;
  return subtext ? `${subtext} · ${face}` : face;
}

/** Horizontal sources too weak to stand as measured (source_labels.WEAK_HORIZONTAL_SOURCES). */
const WEAK_HORIZONTAL_SOURCES = new Set(['radar_low_coherence', 'camera_low_consensus']);

/** An IWR horizontal without a phase reference (source_labels.AZIMUTH_UNCALIBRATED): estimated. */
const AZIMUTH_UNCALIBRATED = 'azimuth_uncalibrated';
// The club chain's last fallback: the camera's hosel track (server camera/head_track.py).
const HEAD_TRACK_STATUS = 'camera_head_track';

/** Subtext naming a camera-derived vertical launch source (the IWR measured none). */
const VERTICAL_SOURCE_LABELS: Record<string, string> = {
  camera_fallback: 'camera fallback',
};

/** Subtext naming a camera-derived horizontal launch source; radar and estimates get none. */
const HORIZONTAL_SOURCE_LABELS: Record<string, string> = {
  camera_assisted_experimental: 'camera assisted',
  camera_only_experimental: 'camera only',
  camera_legacy_fallback: 'camera legacy',
  camera_fallback: 'camera fallback',
  camera_low_consensus: 'camera, low consensus',
  radar_low_coherence: 'radar, low coherence',
};

function experimentalStatus(status: string | null | undefined): string {
  if (!status || status === 'candidate_available') return 'candidate';
  return status.replace(/^rejected_/, 'rejected: ').replaceAll('_', ' ');
}

// The IWR attack that passed its fit gates but no acceptance gate (server IWR_CANDIDATE_STATUS).
const IWR_CANDIDATE_STATUS = 'candidate_available';

type AttackSource = 'canonical' | 'fused' | 'iwr' | 'head_track' | 'iwr_candidate' | null;

/** The tile's own label per route; the server's record label replaces it when there is one. */
const ATTACK_STATUS: Record<string, MetricLabel | undefined> = {
  canonical: 'measured',
  head_track: 'experimental',
  iwr_candidate: 'experimental',
};

function attackRoute(
  shot: Shot,
  { headTrack, fusedRejected }: { headTrack: boolean; fusedRejected: boolean }
): { value: number | null; source: AttackSource } {
  const fused = shot.experimental_fused_attack_angle_deg;
  const fusedShown =
    isMeasured(fused) && !fusedRejected && shot.experimental_fused_attack_angle_confidence !== 'withheld';
  const iwr = shot.experimental_attack_angle_deg;
  const iwrStatus = shot.experimental_attack_angle_status ?? '';
  if (isMeasured(shot.club_angle_deg)) return { value: shot.club_angle_deg, source: 'canonical' };
  if (!headTrack && fusedShown) return { value: fused, source: 'fused' };
  if (iwrStatus.startsWith('accepted') && isMeasured(iwr)) return { value: iwr, source: 'iwr' };
  if (headTrack && fusedShown) return { value: fused, source: 'head_track' };
  if (iwrStatus === IWR_CANDIDATE_STATUS && isMeasured(iwr)) return { value: iwr, source: 'iwr_candidate' };
  return { value: null, source: null };
}

function attackSubtext(
  shot: Shot,
  source: AttackSource,
  fusedSubtext: string,
  fusedDeliveryAttempted: boolean
): string | undefined {
  if (source === 'canonical') return undefined;
  if (source === 'fused' || source === 'head_track') return fusedSubtext;
  if (source === 'iwr') return experimentalStatus(shot.experimental_attack_angle_status);
  if (source === 'iwr_candidate') return 'IWR candidate';
  if (fusedDeliveryAttempted) return experimentalStatus(shot.experimental_fused_status);
  return shot.experimental_attack_angle_status ? experimentalStatus(shot.experimental_attack_angle_status) : undefined;
}

function buildBallStrikeMetrics(shot: Shot, unitSystem: UnitSystem): LiveMetric[] {
  const speedUnit = getSpeedUnit(unitSystem);
  const carry = reportedCarryYards(shot);
  const carryIsCommitted = carry !== null && isMeasured(shot.carry_spin_adjusted);
  const angleConfidence = launchAngleQuality(shot.launch_angle_confidence);
  const angleEstimated = shot.angle_source === 'estimated';
  // Shots recorded before the per-axis fields existed fall back to the shared ones.
  const horizontalConfidence = launchAngleQuality(
    shot.launch_angle_horizontal_confidence !== undefined
      ? shot.launch_angle_horizontal_confidence
      : shot.launch_angle_confidence
  );
  const horizontalEstimated =
    shot.launch_angle_horizontal_source !== undefined
      ? shot.launch_angle_horizontal_source === 'estimated' ||
        WEAK_HORIZONTAL_SOURCES.has(shot.launch_angle_horizontal_source ?? '') ||
        shot.launch_angle_horizontal_status === AZIMUTH_UNCALIBRATED
      : angleEstimated;
  const horizontalSourceLabel = shot.launch_angle_horizontal_source
    ? HORIZONTAL_SOURCE_LABELS[shot.launch_angle_horizontal_source]
    : undefined;
  const horizontalSubtext =
    shot.launch_angle_horizontal_status === AZIMUTH_UNCALIBRATED ? 'azimuth uncalibrated' : horizontalSourceLabel;
  const fusedDeliveryAttempted = shot.experimental_fused_status != null;
  const fusedStatus = shot.experimental_fused_status ?? '';
  const headTrack = fusedStatus === HEAD_TRACK_STATUS;
  const fusedRejected = fusedStatus.startsWith('rejected') || fusedStatus === 'error';
  const cameraFused = fusedDeliveryAttempted && !fusedRejected && !headTrack;
  const fusedSubtext = headTrack ? 'camera head track' : 'camera fused';
  // Server shot_models.attack_angle_resolution(): canonical, camera fused, an accepted
  // IWR attack, the camera head track, the IWR candidate (experimental); the arc's value
  // arrives through the server's record. A rejected or withheld fused value is never shown.
  const attack = attackRoute(shot, { headTrack, fusedRejected });
  const attackAngle = attack.value;
  const attackIsExperimental =
    !isMeasured(shot.club_angle_deg) &&
    attack.source !== 'iwr_candidate' &&
    (shot.experimental_fused_attack_angle_deg != null ||
      shot.experimental_fused_status != null ||
      shot.experimental_attack_angle_deg != null ||
      shot.experimental_attack_angle_status != null);
  // Server displayed_club_path(): camera fused, an accepted IWR path, the head track; an
  // IWR candidate only when the camera never ran.
  const iwrPathAccepted = (shot.experimental_club_path_status ?? '').startsWith('accepted');
  const fusedPath = shot.experimental_fused_club_path_deg;
  const iwrPath = shot.experimental_club_path_deg;
  const fusedPathShown = isMeasured(fusedPath) && shot.experimental_fused_club_path_confidence !== 'withheld';
  const clubPathSource = isMeasured(shot.club_path_deg)
    ? null
    : cameraFused && fusedPathShown
      ? 'fused'
      : iwrPathAccepted && isMeasured(iwrPath)
        ? 'iwr'
        : headTrack && fusedPathShown
          ? 'fused'
          : null;
  const clubPath = firstMeasured(
    shot.club_path_deg,
    clubPathSource === 'fused' ? fusedPath : null,
    clubPathSource === 'iwr' ? iwrPath : null
  );
  const clubPathIsExperimental =
    !isMeasured(shot.club_path_deg) &&
    (shot.experimental_fused_club_path_deg != null ||
      shot.experimental_fused_status != null ||
      shot.experimental_club_path_deg != null ||
      shot.experimental_club_path_status != null);

  const tiles: LiveMetric[] = [
    {
      id: 'ball_speed',
      label: t('metric.ballSpeed'),
      value: formatSpeed(shot.ball_speed_mph, unitSystem, 1),
      unit: unitIfMeasured(shot.ball_speed_mph, speedUnit),
      status: labelIf(shot.ball_speed_mph, 'measured'),
    },
    {
      id: 'carry',
      label: t('metric.carry'),
      value: formatDistance(carry, unitSystem, 0),
      unit: unitIfMeasured(carry, getDistanceUnit(unitSystem)),
      subtext: carryIsCommitted ? t('metric.spinAdjusted') : undefined,
      status: carryLabel(shot) ?? undefined,
    },
    {
      id: 'club_speed',
      label: t('metric.clubSpeed'),
      value: formatSpeed(shot.club_speed_mph, unitSystem, 1),
      unit: unitIfMeasured(shot.club_speed_mph, speedUnit),
      status: labelIf(shot.club_speed_mph, 'measured'),
    },
    {
      id: 'smash',
      label: t('metric.smash'),
      value: isMeasured(shot.smash_factor) ? shot.smash_factor.toFixed(2) : NO_VALUE,
      subtext: smashSubtext(shot),
      status: labelIf(shot.smash_factor, 'calculated'),
    },
    {
      id: 'launch_v',
      label: t('metric.vLaunch'),
      value: formatOptionalAngle(shot.launch_angle_vertical),
      unit: angleUnit(shot.launch_angle_vertical),
      subtext: shot.launch_angle_vertical_source
        ? VERTICAL_SOURCE_LABELS[shot.launch_angle_vertical_source]
        : undefined,
      status: labelIf(shot.launch_angle_vertical, angleEstimated ? 'estimated' : 'measured'),
      confidence: isMeasured(shot.launch_angle_vertical) ? angleConfidence : null,
    },
    {
      id: 'launch_h',
      label: t('metric.hLaunch'),
      value: formatOptionalAngle(shot.launch_angle_horizontal, true),
      unit: angleUnit(shot.launch_angle_horizontal),
      subtext: horizontalSubtext,
      status: labelIf(shot.launch_angle_horizontal, horizontalEstimated ? 'estimated' : 'measured'),
      confidence: isMeasured(shot.launch_angle_horizontal) ? horizontalConfidence : null,
      confidenceLabel: horizontalSourceLabel ? 'experimental' : undefined,
    },
    {
      id: 'spin',
      label: t('metric.spin'),
      value: formatSpinRpm(shot.spin_rpm),
      unit: unitIfMeasured(shot.spin_rpm, 'rpm'),
      status: labelIf(shot.spin_rpm, spinLabel(shot)),
      confidence: !isMeasured(shot.spin_rpm)
        ? null
        : spinLabel(shot) === 'measured'
          ? shot.spin_quality
          : spinLabel(shot) === 'experimental'
            ? 'experimental'
            : null,
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
      subtext: isMeasured(shot.club_path_deg)
        ? undefined
        : clubPathSource === 'fused'
          ? fusedSubtext
          : clubPathSource === 'iwr'
            ? experimentalStatus(shot.experimental_club_path_status)
            : fusedDeliveryAttempted
              ? experimentalStatus(shot.experimental_fused_status)
              : clubPathIsExperimental
                ? experimentalStatus(shot.experimental_club_path_status)
                : undefined,
      confidence: !clubPathIsExperimental
        ? null
        : clubPathSource === 'iwr'
          ? 'experimental'
          : (shot.experimental_fused_club_path_confidence ?? 'experimental'),
      status: isMeasured(shot.club_path_deg) ? 'measured' : undefined,
      confidenceLabel:
        clubPathSource !== 'iwr' && shot.experimental_fused_club_path_confidence ? 'experimental' : undefined,
    },
    {
      id: 'club_aoa',
      label: t('metric.clubAoa'),
      value: formatOptionalAngle(attackAngle, true),
      unit: angleUnit(attackAngle),
      subtext: attackSubtext(shot, attack.source, fusedSubtext, fusedDeliveryAttempted),
      confidence: attackIsExperimental
        ? attack.source === 'iwr'
          ? 'experimental'
          : (shot.experimental_fused_attack_angle_confidence ?? 'experimental')
        : null,
      confidenceLabel:
        (attack.source === 'fused' || attack.source === 'head_track') && shot.experimental_fused_attack_angle_confidence
          ? 'experimental'
          : undefined,
      status: ATTACK_STATUS[attack.source ?? ''],
    },
  ];
  return tiles.map((tile) => {
    const shown = withServerLabel(shot, tile, unitSystem);
    if (tile.id !== 'club_path' || shown.value === NO_VALUE) return shown;
    const face = shotMetric(shot, 'face_angle');
    const faceValue = face ? (isValueLabel(face.status) ? face.value : null) : shot.experimental_face_angle_deg;
    return {
      ...shown,
      subtext: withFaceAngle(
        shown.subtext,
        faceValue,
        face ? undefined : shot.experimental_face_angle_status,
        face?.status
      ),
    };
  });
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
