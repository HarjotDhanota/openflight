import { t } from '../i18n';
import { useSystemStore, type Handed } from '../stores/useSystemStore';

/** The club as the header shows it, with the golfer's hand once the kiosk has said it. */
export function clubWithHand(club: string, handed: Handed | null): string {
  if (!handed) return club;
  return `${club} · ${t(handed === 'LH' ? 'app.leftHanded' : 'app.rightHanded')}`;
}

/** The kiosk's `handedness` event: RH or LH, anything else ignored. */
export function ingestHandedness(data: unknown): void {
  const handed = (data as { handed?: unknown } | null)?.handed;
  if (handed === 'RH' || handed === 'LH') useSystemStore.getState().setHanded(handed);
}
