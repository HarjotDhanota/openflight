// The kiosk shell's window. Fullscreen and chromeless on the unit's own screen;
// with OPENFLIGHT_KIOSK_WINDOWED=1 a normal 1024×600 window instead, so a
// launcher can show the kiosk during a tester run without it covering the
// tester page (13 Oct decision 2a). Kept apart from main.js so it can be
// unit-tested without the `electron` module.
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
    ? { ...common, kiosk: false, fullscreen: false, width: 1024, height: 600 }
    : { ...common, kiosk: true, fullscreen: true };
}
