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

  it("sizes the window from OPENFLIGHT_KIOSK_WINDOW_SIZE, to try another screen's size", () => {
    const options = kioskWindowOptions({ OPENFLIGHT_KIOSK_WINDOWED: '1', OPENFLIGHT_KIOSK_WINDOW_SIZE: '800x480' });
    // the page gets the screen's size, not the window with its frame
    expect(options).toMatchObject({ width: 800, height: 480, useContentSize: true });
  });

  it('reads the multiplication sign too', () => {
    const options = kioskWindowOptions({ OPENFLIGHT_KIOSK_WINDOWED: '1', OPENFLIGHT_KIOSK_WINDOW_SIZE: '800×480' });
    expect(options).toMatchObject({ width: 800, height: 480 });
  });

  it('keeps 1024×600 for a size it cannot read', () => {
    for (const size of ['', 'big', '800', '0x0', '80x48', '-800x480']) {
      expect(
        kioskWindowOptions({ OPENFLIGHT_KIOSK_WINDOWED: '1', OPENFLIGHT_KIOSK_WINDOW_SIZE: size })
      ).toMatchObject({ width: 1024, height: 600 });
    }
  });

  it('leaves a fullscreen window at the size of the screen it is on', () => {
    const options = kioskWindowOptions({ OPENFLIGHT_KIOSK_WINDOW_SIZE: '800x480' });
    expect(options).not.toHaveProperty('width');
    expect(options).not.toHaveProperty('height');
  });

  it('zooms every page by OPENFLIGHT_KIOSK_ZOOM, for a screen with more pixels than 1024×600', () => {
    expect(kioskWindowOptions({ OPENFLIGHT_KIOSK_ZOOM: '1.25' }).webPreferences).toMatchObject({ zoomFactor: 1.25 });
    expect(
      kioskWindowOptions({ OPENFLIGHT_KIOSK_WINDOWED: '1', OPENFLIGHT_KIOSK_ZOOM: '0.8' }).webPreferences
    ).toMatchObject({ zoomFactor: 0.8 });
  });

  it('keeps 100% for a zoom it cannot read or that would make the page unusable', () => {
    for (const zoom of ['', 'big', '0', '-1', '0.2', '5']) {
      expect(kioskWindowOptions({ OPENFLIGHT_KIOSK_ZOOM: zoom }).webPreferences).toMatchObject({ zoomFactor: 1 });
    }
  });
});
