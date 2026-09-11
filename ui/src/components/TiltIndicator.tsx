import { useSystemStore } from '../stores/useSystemStore';
import { useI18n } from '../i18n/useI18n';
import type { MessageKey } from '../i18n';
import type { InclinometerStatus, PlacementStatus } from '../types/inclinometer';
import './TiltIndicator.css';

type Tone = 'ok' | 'warn' | 'crit';

/**
 * Green only when the unit is standing as its geometry says it should; red
 * when it is tilted past the tolerance; amber for every "cannot tell" case,
 * which is a different thing from "fine" and must not look like it.
 */
const TONES: Record<PlacementStatus, Tone> = {
  ok: 'ok',
  tilted_pitch: 'crit',
  tilted_roll: 'crit',
  tilted_both: 'crit',
  no_reading: 'warn',
  no_expected_orientation: 'warn',
  disabled: 'warn',
};

const LABELS: Record<PlacementStatus, MessageKey> = {
  ok: 'tilt.ok',
  tilted_pitch: 'tilt.tiltedPitch',
  tilted_roll: 'tilt.tiltedRoll',
  tilted_both: 'tilt.tiltedBoth',
  no_reading: 'tilt.noReading',
  no_expected_orientation: 'tilt.notChecked',
  disabled: 'tilt.disabled',
};

function degrees(value: number | null): string {
  return value === null ? '--' : `${value.toFixed(1)}°`;
}

/**
 * Compact enclosure-tilt readout for the kiosk status panel: the measured
 * pitch and roll, and one pill naming the placement verdict. Non-interactive
 * by design — the numbers and the pill text carry the whole message, so
 * nothing here is hidden behind a hover or a tap.
 */
export function TiltIndicator({ status: statusProp }: { status?: InclinometerStatus | null }) {
  const { t } = useI18n();
  const storeStatus = useSystemStore((state) => state.inclinometerStatus);
  const status = statusProp !== undefined ? statusProp : storeStatus;
  if (!status) return null;

  const verdict = t(LABELS[status.status] ?? 'tilt.noReading');
  const pitch = degrees(status.pitch_deg);
  const roll = degrees(status.roll_deg);
  const label = status.enabled
    ? t('tilt.summary', { verdict, pitch, roll })
    : `${t('tilt.label')}: ${verdict}`;

  return (
    <div
      className={`tilt-indicator tilt-indicator--${TONES[status.status] ?? 'warn'}`}
      role="status"
      aria-label={label}
      title={status.reason}
    >
      <span className="tilt-indicator__readout">
        <span className="tilt-indicator__axis">
          <span className="tilt-indicator__axis-label">{t('tilt.pitch')}</span>
          <span className="tilt-indicator__axis-value">{pitch}</span>
        </span>
        <span className="tilt-indicator__axis">
          <span className="tilt-indicator__axis-label">{t('tilt.roll')}</span>
          <span className="tilt-indicator__axis-value">{roll}</span>
        </span>
      </span>
      <span className="tilt-indicator__pill">{verdict}</span>
    </div>
  );
}
