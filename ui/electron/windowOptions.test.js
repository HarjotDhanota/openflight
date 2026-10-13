import { describe, it, expect } from 'vitest';
import { kioskWindowOptions } from './windowOptions.js';

describe('kioskWindowOptions', () => {
  it('is a chromeless fullscreen kiosk by default', () => {
    expect(kioskWindowOptions({})).toMatchObject({ kiosk: true, fullscreen: true });
  });

  it('opens a normal window for tester runs, so it never covers the tester page (2a)', () => {
    const options = kioskWindowOptions({ OPENFLIGHT_KIOSK_WINDOWED: '1' });
    expect(options).toMatchObject({ kiosk: false, fullscreen: false, width: 1024, height: 600 });
  });

  it('ignores an empty or zero flag', () => {
    expect(kioskWindowOptions({ OPENFLIGHT_KIOSK_WINDOWED: '' })).toMatchObject({ kiosk: true });
    expect(kioskWindowOptions({ OPENFLIGHT_KIOSK_WINDOWED: '0' })).toMatchObject({ kiosk: true });
  });
});
