import { useEffect, useState } from 'react';
import type { CameraCaptureSettings } from '../stores/useCameraStore';
import type { Shot } from '../types/shot';
import { computeSwingSpeedStats, getSwingSpeedMph, isSwingSpeedShot } from '../types/shot';
import { useUnitPreference } from '../state/useUnitPreference';
import {
  NO_VALUE,
  firstMeasured,
  formatDistance,
  formatSpeed,
  getDistanceUnit,
  getSpeedUnit,
  isMeasured,
  unitIfMeasured,
} from '../utils/units';
import { getServerOrigin } from '../utils/serverOrigin';
import { MetricCard } from './ui/MetricCard';
import { getHtmlLang, type MessageKey } from '../i18n';
import { useI18n } from '../i18n/useI18n';
import './DisplayMode.css';

type Translate = (key: MessageKey, vars?: Record<string, string | number>) => string;

interface DisplayModeProps {
  connected: boolean;
  captureSettings: CameraCaptureSettings;
  latestShot: Shot | null;
  shots: Shot[];
}

interface DisplayMetric {
  label: string;
  value: string;
  unit?: string;
  detail?: string;
}

const CAMERA_PREVIEW_URL = `${getServerOrigin()}/api/camera/preview.jpg`;
const CAMERA_PREVIEW_REFRESH_MS = 5_000;
const RECENT_SHOT_COUNT = 5;

function formatOptionalNumber(value: number | null | undefined, digits = 1, prefixPositive = false): string {
  if (!isMeasured(value)) {
    return NO_VALUE;
  }

  const prefix = prefixPositive && value > 0 ? '+' : '';
  return `${prefix}${value.toFixed(digits)}`;
}

function formatSpin(value: number | null | undefined): string {
  if (!isMeasured(value)) {
    return NO_VALUE;
  }

  return value.toLocaleString(getHtmlLang(), { maximumFractionDigits: 0 });
}

/** A recent-shot chip value with its unit, or the no-value mark alone. */
function chipValue(value: number | null, format: (value: number) => string, unit: string): string {
  return isMeasured(value) ? `${format(value)} ${unit}` : NO_VALUE;
}

function experimentalStatus(status: string | null | undefined): string {
  if (!status || status === 'candidate_available') return 'experimental candidate';
  return `experimental · ${status.replace(/^rejected_/, 'rejected: ').replaceAll('_', ' ')}`;
}

function buildMetrics(shot: Shot | null, unitSystem: 'imperial' | 'metric', t: Translate): DisplayMetric[] {
  if (!shot) {
    return [
      { label: t('display.ballSpeed'), value: NO_VALUE },
      { label: t('metric.carry'), value: NO_VALUE },
      { label: t('display.clubSpeed'), value: NO_VALUE },
      { label: t('metric.smash'), value: NO_VALUE },
      { label: t('display.launch'), value: NO_VALUE },
      { label: t('metric.spin'), value: NO_VALUE },
      { label: t('display.clubPath'), value: NO_VALUE },
      { label: t('metric.clubAoa'), value: NO_VALUE },
      { label: t('metric.hLaunch'), value: NO_VALUE },
    ];
  }

  const fusedDeliveryAttempted = shot.experimental_fused_status != null;
  const carryYards = firstMeasured(shot.carry_spin_adjusted, shot.estimated_carry_yards);
  const clubPath = firstMeasured(
    shot.club_path_deg,
    shot.experimental_fused_club_path_deg,
    !fusedDeliveryAttempted ? shot.experimental_club_path_deg : null
  );
  const attackAngle = firstMeasured(
    shot.club_angle_deg,
    shot.experimental_fused_attack_angle_deg,
    !fusedDeliveryAttempted ? shot.experimental_attack_angle_deg : null
  );

  return [
    {
      label: t('display.ballSpeed'),
      value: formatSpeed(shot.ball_speed_mph, unitSystem, 1),
      unit: unitIfMeasured(shot.ball_speed_mph, getSpeedUnit(unitSystem)),
    },
    {
      label: t('metric.carry'),
      value: formatDistance(carryYards, unitSystem, 0),
      unit: unitIfMeasured(carryYards, getDistanceUnit(unitSystem)),
      detail: isMeasured(shot.carry_spin_adjusted) ? t('metric.spinAdjusted') : undefined,
    },
    {
      label: t('display.clubSpeed'),
      value: formatSpeed(shot.club_speed_mph, unitSystem, 1),
      unit: unitIfMeasured(shot.club_speed_mph, getSpeedUnit(unitSystem)),
    },
    {
      label: t('metric.smash'),
      value: isMeasured(shot.smash_factor) ? shot.smash_factor.toFixed(2) : NO_VALUE,
    },
    {
      label: t('display.launch'),
      value: formatOptionalNumber(shot.launch_angle_vertical),
      unit: unitIfMeasured(shot.launch_angle_vertical, 'deg'),
      detail: shot.angle_source ?? undefined,
    },
    {
      label: t('metric.spin'),
      value: formatSpin(shot.spin_rpm),
      unit: unitIfMeasured(shot.spin_rpm, 'rpm'),
      detail: shot.spin_quality ?? undefined,
    },
    {
      label: t('display.clubPath'),
      value: formatOptionalNumber(clubPath, 1, true),
      unit: unitIfMeasured(clubPath, 'deg'),
      detail: isMeasured(shot.club_path_deg)
        ? undefined
        : fusedDeliveryAttempted
          ? shot.experimental_fused_club_path_deg != null
            ? 'camera fused (exp.)'
            : experimentalStatus(shot.experimental_fused_status)
          : shot.experimental_club_path_deg != null || shot.experimental_club_path_status != null
            ? experimentalStatus(shot.experimental_club_path_status)
            : undefined,
    },
    {
      label: t('metric.clubAoa'),
      value: formatOptionalNumber(attackAngle),
      unit: unitIfMeasured(attackAngle, 'deg'),
      detail: isMeasured(shot.club_angle_deg)
        ? undefined
        : fusedDeliveryAttempted
          ? shot.experimental_fused_attack_angle_deg != null
            ? 'camera fused (exp.)'
            : experimentalStatus(shot.experimental_fused_status)
          : shot.experimental_attack_angle_deg != null || shot.experimental_attack_angle_status != null
            ? experimentalStatus(shot.experimental_attack_angle_status)
            : undefined,
    },
    {
      label: t('metric.hLaunch'),
      value: formatOptionalNumber(shot.launch_angle_horizontal, 1, true),
      unit: unitIfMeasured(shot.launch_angle_horizontal, 'deg'),
      detail:
        shot.launch_angle_horizontal_source === 'camera_assisted_experimental' ? 'camera assisted (exp.)' : undefined,
    },
  ];
}

function toMetricCard(metric: DisplayMetric, featured = false) {
  return (
    <MetricCard
      key={metric.label}
      value={metric.value}
      unit={metric.unit}
      label={metric.label}
      subtext={metric.detail}
      variant={featured ? 'emphasis' : 'default'}
    />
  );
}

export function DisplayMode({ connected, captureSettings, latestShot, shots }: DisplayModeProps) {
  const { t } = useI18n();
  const [failedCameraKey, setFailedCameraKey] = useState<string | null>(null);
  const [cameraRefresh, setCameraRefresh] = useState(0);
  const cameraAvailable = Boolean(captureSettings.available && captureSettings.running);
  useEffect(() => {
    if (!cameraAvailable) return undefined;
    const timer = window.setInterval(() => {
      setFailedCameraKey(null);
      setCameraRefresh((value) => value + 1);
    }, CAMERA_PREVIEW_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [cameraAvailable]);
  const { unitSystem } = useUnitPreference();
  const isSwingSpeedSession = latestShot ? isSwingSpeedShot(latestShot) : false;
  const swingStats = computeSwingSpeedStats(shots);
  const metrics = isSwingSpeedSession
    ? [
        {
          label: t('display.lastSwing'),
          value: formatSpeed(swingStats.last_speed_mph, unitSystem, 1),
          unit: getSpeedUnit(unitSystem),
        },
        {
          label: t('metric.best'),
          value: formatSpeed(swingStats.best_speed_mph, unitSystem, 1),
          unit: getSpeedUnit(unitSystem),
          detail: t('display.thisSession'),
        },
        {
          label: t('metric.average'),
          value: formatSpeed(swingStats.avg_speed_mph, unitSystem, 1),
          unit: getSpeedUnit(unitSystem),
        },
        { label: t('metric.swings'), value: String(swingStats.count) },
      ]
    : buildMetrics(latestShot, unitSystem, t);
  const recentShots = shots.slice(-RECENT_SHOT_COUNT).reverse();
  const cameraKey = `${captureSettings.available}-${captureSettings.running}`;
  const cameraError = failedCameraKey === cameraKey;

  return (
    <main className="display-mode">
      <section className="display-mode__hero" aria-label={t('display.tvAria')}>
        <div className="display-mode__camera">
          {!cameraAvailable || cameraError ? (
            <div className="display-mode__camera-placeholder">
              <span>{t('display.streamUnavailable')}</span>
            </div>
          ) : (
            <img
              src={`${CAMERA_PREVIEW_URL}?refresh=${cameraRefresh}`}
              alt={t('display.streamAlt')}
              className="display-mode__camera-image"
              onError={() => setFailedCameraKey(cameraKey)}
              onLoad={() => setFailedCameraKey(null)}
            />
          )}
          <div className="display-mode__status-row">
            <span
              className={`display-mode__status ${connected ? 'display-mode__status--online' : 'display-mode__status--offline'}`}
            >
              {connected ? t('display.socketOn') : t('display.socketOff')}
            </span>
            <span
              className={`display-mode__status ${cameraAvailable && !cameraError ? 'display-mode__status--online' : 'display-mode__status--offline'}`}
            >
              {cameraAvailable && !cameraError ? t('display.streamActive') : t('display.cameraUnavailable')}
            </span>
          </div>
        </div>

        <div className="display-mode__shot-panel">
          <div className="display-mode__eyebrow">{t('display.eyebrow')}</div>
          <h1 className="display-mode__title">
            {isSwingSpeedSession ? t('display.swingSpeed') : latestShot ? latestShot.club : t('display.ready')}
          </h1>
          <div className="display-mode__primary-grid">
            {toMetricCard(metrics[0], true)}
            {toMetricCard(metrics[1], true)}
          </div>
          <div className="display-mode__metrics-grid">{metrics.slice(2).map((metric) => toMetricCard(metric))}</div>
        </div>
      </section>

      <section className="display-mode__recent" aria-label={t('display.recentAria')}>
        {recentShots.length === 0 ? (
          <div className="display-mode__empty-strip">{t('display.recentEmpty')}</div>
        ) : (
          recentShots.map((shot, index) => (
            <div className="display-shot-chip" key={shot.timestamp}>
              <span className="display-shot-chip__number">#{shots.length - index}</span>
              <span className="display-shot-chip__club">
                {isSwingSpeedShot(shot) ? (shot.training_implement_label ?? shot.club) : shot.club}
              </span>
              <span className="display-shot-chip__stat">
                {chipValue(
                  isSwingSpeedShot(shot) ? getSwingSpeedMph(shot) : shot.ball_speed_mph,
                  (value) => formatSpeed(value, unitSystem, 0),
                  getSpeedUnit(unitSystem)
                )}
              </span>
              {!isSwingSpeedShot(shot) && (
                <span className="display-shot-chip__stat">
                  {chipValue(
                    firstMeasured(shot.carry_spin_adjusted, shot.estimated_carry_yards),
                    (value) => formatDistance(value, unitSystem, 0),
                    getDistanceUnit(unitSystem)
                  )}
                </span>
              )}
            </div>
          ))
        )}
      </section>
    </main>
  );
}
