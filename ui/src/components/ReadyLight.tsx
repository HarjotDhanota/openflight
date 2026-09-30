import { useSystemStore } from '../stores/useSystemStore';
import type { ReadyLight } from '../types/readyLight';
import { swingIsFresh, timeLeftSeconds } from '../utils/readyLight';
import { useI18n, type MessageKey } from '../i18n/useI18n';
import './ReadyLight.css';

const WORD_KEYS: Record<'red' | 'amber' | 'green', MessageKey> = {
  red: 'ready.notReady',
  amber: 'ready.wait',
  green: 'ready.swing',
};

/**
 * The ready light as a full-width band (P7-14): the word as well as the colour,
 * the cause, a rough time left, and the last swing. It sits in the page flow
 * above the panels, so it never covers the camera feed.
 */
export function ReadyLightBand({ light }: { light: ReadyLight | null }) {
  const { t } = useI18n();
  if (light === null || light.state === 'off') return null;
  const seconds = timeLeftSeconds(light.time_left_s);
  const swing = light.swing;
  const swingText = swing
    ? swing.result
      ? t('ready.lastSwing', { result: swing.result.text })
      : swingIsFresh(light)
        ? t('ready.swingPickedUp')
        : null
    : null;

  return (
    <div
      className={`ready-light ready-light--${light.state}`}
      data-state={light.state}
      role="status"
      aria-live="polite"
      aria-label={t('ready.label')}
    >
      {swing && swingIsFresh(light) ? <span key={swing.id} className="ready-light__flash" aria-hidden="true" /> : null}
      <strong className="ready-light__word">{t(WORD_KEYS[light.state])}</strong>
      <span className="ready-light__cause">
        {light.cause}
        {seconds !== null ? (
          <span className="ready-light__time">{t('ready.timeLeft', { seconds: String(seconds) })}</span>
        ) : null}
      </span>
      {swingText ? <span className="ready-light__swing">{swingText}</span> : null}
    </div>
  );
}

/** The kiosk screen's ready light, fed by the `ready_light` socket event. */
export function ReadyLightBar() {
  const light = useSystemStore((state) => state.readyLight);
  return <ReadyLightBand light={light} />;
}
