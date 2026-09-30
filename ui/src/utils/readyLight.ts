import type { ReadyLight } from '../types/readyLight';

/** A swing this recent (on the kiosk's clock) flashes the band. */
export const FLASH_RECENT_S = 3;

export function timeLeftSeconds(seconds: number | null): number | null {
  return seconds === null ? null : Math.ceil(seconds);
}

export function swingIsFresh(light: ReadyLight): boolean {
  return light.swing !== null && light.checked_at - light.swing.at <= FLASH_RECENT_S;
}

/** The light to show once the kiosk's socket drops: it is restarting or gone. */
export function kioskRestartingLight(previous: ReadyLight | null): ReadyLight | null {
  if (previous === null || previous.state === 'off') return null;
  return {
    ...previous,
    state: 'red',
    word: 'NOT READY',
    cause: 'kiosk restarting',
    time_left_s: null,
    causes: [{ cause: 'kiosk restarting', time_left_s: null }],
    hold: null,
  };
}
