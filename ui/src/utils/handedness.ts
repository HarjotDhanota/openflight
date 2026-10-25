import { t } from '../i18n';
import { HANDED_SOURCES, useSystemStore, type Handed, type HandedSource } from '../stores/useSystemStore';

/** The club as the header shows it, with the golfer's hand once the kiosk has said it. */
export function clubWithHand(club: string, handed: Handed | null): string {
  if (!handed) return club;
  return `${club} · ${t(handed === 'LH' ? 'app.leftHanded' : 'app.rightHanded')}`;
}

/** The kiosk's `handedness` event: RH or LH, anything else ignored, and who said it. */
export function ingestHandedness(data: unknown): void {
  const { handed, source } = (data as { handed?: unknown; source?: unknown } | null) ?? {};
  if (handed !== 'RH' && handed !== 'LH') return;
  const known = HANDED_SOURCES.includes(source as HandedSource) ? (source as HandedSource) : null;
  useSystemStore.getState().setHanded(handed, known);
}
