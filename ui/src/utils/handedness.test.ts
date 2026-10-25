import { afterEach, describe, expect, it } from 'vitest';
import { setActiveLocale } from '../i18n';
import { useSystemStore } from '../stores/useSystemStore';
import { clubWithHand, ingestHandedness } from './handedness';

describe('the golfer hand beside the club', () => {
  afterEach(() => {
    setActiveLocale('en');
    useSystemStore.getState().setHanded(null, null);
  });

  it('names the hand after the club', () => {
    expect(clubWithHand('7 Iron', 'RH')).toBe('7 Iron · Right-handed');
    expect(clubWithHand('Driver', 'LH')).toBe('Driver · Left-handed');
  });

  it('shows the club alone until the kiosk says which hand', () => {
    expect(clubWithHand('Driver', null)).toBe('Driver');
  });

  it('follows the kiosk language', () => {
    setActiveLocale('es');
    expect(clubWithHand('Driver', 'LH')).not.toContain('Left-handed');
  });

  it('keeps only RH or LH from the kiosk', () => {
    ingestHandedness({ handed: 'LH', source: 'setup' });
    expect(useSystemStore.getState().handed).toBe('LH');
    ingestHandedness({ handed: 'left' });
    expect(useSystemStore.getState().handed).toBe('LH');
    ingestHandedness({ handed: 'RH', source: 'simulator' });
    expect(useSystemStore.getState().handed).toBe('RH');
    ingestHandedness(null);
    expect(useSystemStore.getState().handed).toBe('RH');
  });

  it('keeps who said the hand, so the picker can show a simulator overriding it', () => {
    ingestHandedness({ handed: 'LH', source: 'simulator' });
    expect(useSystemStore.getState().handedSource).toBe('simulator');
    ingestHandedness({ handed: 'RH', source: 'profile' });
    expect(useSystemStore.getState().handedSource).toBe('profile');
    ingestHandedness({ handed: 'RH', source: 'somewhere else' });
    expect(useSystemStore.getState().handedSource).toBe(null);
    ingestHandedness({ handed: 'RH' });
    expect(useSystemStore.getState().handedSource).toBe(null);
  });
});
