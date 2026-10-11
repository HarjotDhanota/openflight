import { useI18n } from '../i18n/useI18n';
import type { SimShotNotSent } from '../types/socket';
import './SimNotSentNotice.css';

interface SimNotSentNoticeProps {
  notSent: SimShotNotSent | null;
  /** The timestamp of the shot on screen; the notice belongs to that shot only. */
  latestShotTimestamp: string | null;
}

/**
 * Says why the shot on screen was not sent to the simulator (D31): a fusion unit
 * sends only what it measured, e.g. "Not sent to simulator: spin not measured".
 */
export function SimNotSentNotice({ notSent, latestShotTimestamp }: SimNotSentNoticeProps) {
  const { t } = useI18n();
  if (!notSent || !latestShotTimestamp || notSent.shot_timestamp !== latestShotTimestamp) {
    return null;
  }
  return (
    <div className="sim-not-sent" role="status">
      {t('sim.notSent', { reason: notSent.reason })}
    </div>
  );
}
