// The kiosk shell's window. Fullscreen and chromeless on the unit's own screen,
// at whatever size that screen is; with OPENFLIGHT_KIOSK_WINDOWED=1 a normal
// window instead, so a launcher can show the kiosk during a tester run without
// it covering the tester page (13 Oct decision 2a). The window is 1024×600
// unless OPENFLIGHT_KIOSK_WINDOW_SIZE says otherwise (e.g. 800x480), to try a
// smaller screen's layout on a desktop. Kept apart from main.js so it can be
// unit-tested without the `electron` module.
export const DEFAULT_WINDOW_SIZE = { width: 1024, height: 600 };

function windowSize(env) {
  const match = /^(\d{3,5})[x×](\d{3,5})$/i.exec(String(env.OPENFLIGHT_KIOSK_WINDOW_SIZE || '').trim());
  return match ? { width: Number(match[1]), height: Number(match[2]) } : DEFAULT_WINDOW_SIZE;
}

export function kioskWindowOptions(env) {
  const windowed = Boolean(env.OPENFLIGHT_KIOSK_WINDOWED) && env.OPENFLIGHT_KIOSK_WINDOWED !== '0';
  const common = {
    autoHideMenuBar: true,
    backgroundColor: '#000000',
    webPreferences: {
      contextIsolation: true,
      sandbox: true,
    },
  };
  return windowed
    ? { ...common, kiosk: false, fullscreen: false, useContentSize: true, ...windowSize(env) }
    : { ...common, kiosk: true, fullscreen: true };
}
