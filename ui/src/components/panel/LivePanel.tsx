import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import type { SetupSnapshot } from '../../hooks/useSetup';
import { useCameraStill } from '../../hooks/useCameraStill';
import type { Handed } from '../../stores/useSystemStore';
import { getServerOrigin } from '../../utils/serverOrigin';
import { SetupPicture } from '../setup/SetupPicture';
import { aimLine, type AimOverlay } from '../setup/setupOverlay';
import type { Shot } from '../../types/shot';
import { computeSwingSpeedStats, filterShotsByProfile } from '../../types/shot';
import { useUnitPreference } from '../../state/useUnitPreference';
import { useI18n } from '../../i18n/useI18n';
import { useSharedFitFontSize } from '../../hooks/useFitFontSize';
import { uiExtensions } from '../../extensions';
import { MetricCard } from '../ui/MetricCard';
import { PanelHeader } from './PanelHeader';
import { buildLiveMetrics, pinSelectedMetric } from './liveMetrics';
import { targetLineYawText, type TargetLine } from '../../utils/targetLine';

interface LivePanelProps {
  shot: Shot | null;
  shots: Shot[];
  profileId: string;
  profileName: string;
  clubLabel: string;
  /** Undefined outside swing-speed mode. Scopes the swing stats to one implement. */
  activeTrainingImplement?: string;
  /** Metric pinned top-left while the full table remains visible. */
  selectedMetricId?: string | null;
  onSelectMetric?: (id: string) => void;
  /** True for a freshly captured shot (not a restored session). */
  isNewShot?: boolean;
  /** Pinned header control, e.g. Change club. */
  headerAction?: ReactNode;
  /** Nothing covers this result (no picker, replay, shutdown, menu or dialog). */
  resultVisible?: boolean;
  /** The kiosk's target line (GET /api/target-line); null draws nothing. */
  targetLine?: TargetLine | null;
  /** The kiosk's setup record: the applied aim can be opened on a camera view. */
  setup?: SetupSnapshot | null;
  handed?: Handed | null;
}

// The preview is in the setup's pixels only because the kiosk never rolls it
// (test_the_preview_the_overlays_draw_on_is_not_rolled).
const AIM_VIEW_URL = `${getServerOrigin()}/api/camera/preview.jpg?width=640`;
const AIM_VIEW_REFRESH_MS = 2000;
const AIM_VIEW_OPEN_MS = 10_000;

/** The aim line on the camera view, over the tiles; fetched only while it is open. */
function AimView({
  aim,
  handed,
  label,
  closeLabel,
  onClose,
}: {
  aim: AimOverlay;
  handed: Handed | null;
  label: string;
  closeLabel: string;
  onClose: () => void;
}) {
  const still = useCameraStill(AIM_VIEW_URL, AIM_VIEW_REFRESH_MS);
  useEffect(() => {
    const timer = setTimeout(onClose, AIM_VIEW_OPEN_MS);
    return () => clearTimeout(timer);
  }, [onClose]);
  return (
    <button type="button" className="live-panel__aim-view" aria-label={closeLabel} onClick={onClose}>
      {still.src ? <SetupPicture src={still.src} alt={label} handed={handed} aim={aim} /> : null}
    </button>
  );
}

/**
 * Ten-metric table. Tapping a tile selects it: the title turns accent yellow
 * and the tile moves to the top-left without hiding the remaining metrics.
 */
export function LivePanel({
  shot,
  shots,
  profileId,
  profileName,
  clubLabel,
  activeTrainingImplement,
  selectedMetricId = null,
  onSelectMetric,
  isNewShot = false,
  headerAction,
  resultVisible = false,
  targetLine = null,
  setup = null,
  handed = null,
}: LivePanelProps) {
  const { locale, t } = useI18n();
  const { unitSystem } = useUnitPreference();
  const [aimOpen, setAimOpen] = useState(false);
  const closeAim = useCallback(() => setAimOpen(false), []);
  const profileShots = useMemo(() => filterShotsByProfile(shots, profileId), [shots, profileId]);
  const displayedShot = profileShots[profileShots.length - 1] ?? null;
  // the same position in both trees, so an extension's state survives an empty panel
  const extensionResults = uiExtensions.map((extension, index) =>
    extension.LiveResult ? <extension.LiveResult key={index} shot={displayedShot} visible={resultVisible} /> : null
  );
  const isProfileNewShot = Boolean(isNewShot && shot && displayedShot && shot.timestamp === displayedShot.timestamp);

  const swingStats = useMemo(
    () => computeSwingSpeedStats(profileShots, { profileId, trainingImplement: activeTrainingImplement }),
    [profileShots, profileId, activeTrainingImplement]
  );
  const metrics = useMemo(
    () =>
      displayedShot ? pinSelectedMetric(buildLiveMetrics(displayedShot, unitSystem, swingStats), selectedMetricId) : [],
    // locale is not read in the factory; t() is a stable import. Without it,
    // changing language would keep stale metric labels.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- see above
    [displayedShot, unitSystem, swingStats, selectedMetricId, locale]
  );
  const selected = metrics[0] ?? null;
  const gridRef = useSharedFitFontSize(
    metrics.length > 0,
    metrics.map((metric) => `${metric.value}:${metric.unit ?? ''}`).join('|')
  );
  const header = <PanelHeader title={t('nav.live')} subtitle={profileName} club={clubLabel} actions={headerAction} />;
  let targetText: string | null = null;
  if (targetLine?.status === 'not_set') {
    targetText = t('live.targetLineNotSet');
  } else if (targetLine?.status === 'set') {
    const yaw = targetLine.yawDeg === null ? '—' : targetLineYawText(targetLine.yawDeg);
    targetText =
      targetLine.uncertaintyDeg === null
        ? t('live.targetLineSetNoSpread', { yaw })
        : t('live.targetLineSet', { yaw, spread: `${targetLine.uncertaintyDeg.toFixed(1)}°` });
  }
  // the picture and the words describe one aim: the one the kiosk applied
  const aim = targetLine?.status === 'set' && !setup?.unapplied.aim ? aimLine(setup) : null;
  const target = targetText ? (
    <p className="live-panel__target" data-status={targetLine?.status} role="status">
      {aim ? (
        <button
          type="button"
          className="live-panel__target-button"
          aria-expanded={aimOpen}
          onClick={() => setAimOpen((open) => !open)}
        >
          {targetText} ›
        </button>
      ) : (
        targetText
      )}
    </p>
  ) : null;
  const aimView =
    aim && aimOpen ? (
      <AimView
        aim={aim}
        handed={handed}
        label={t('live.aimView')}
        closeLabel={t('live.aimViewClose')}
        onClose={closeAim}
      />
    ) : null;

  if (!selected) {
    return (
      <div className="panel">
        {extensionResults}
        {header}
        {target}
        <div className="panel__body panel__body--empty live-panel__empty">
          <span className="panel__empty-title live-panel__empty-title">{t('live.ready')}</span>
          <span className="panel__empty-detail live-panel__empty-detail">{t('live.readyDetail')}</span>
          {aimView}
        </div>
      </div>
    );
  }

  return (
    <div className="panel">
      {extensionResults}
      {header}
      {target}
      <div className="panel__body live-panel__body">
        {isProfileNewShot ? <div className="shot-flash" /> : null}
        {aimView}
        <div ref={gridRef} className={`live-panel__grid live-panel__grid--of-${metrics.length}`}>
          {metrics.map((metric) => (
            <MetricCard
              key={metric.id}
              label={metric.label}
              value={metric.value}
              unit={metric.unit}
              subtext={metric.subtext}
              status={metric.status}
              reason={metric.reason}
              confidence={metric.confidence}
              confidenceLabel={metric.confidenceLabel}
              labelPosition="above"
              selected={metric.id === selected.id}
              onClick={onSelectMetric ? () => onSelectMetric(metric.id) : undefined}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
