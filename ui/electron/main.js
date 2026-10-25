// Kiosk shell for the OpenFlight React UI. Loads whatever URL the launcher
// script gives it (the startup splash, then the app itself once it
// navigates there) in a chromeless, fullscreen window — this replaces
// scripts/start-kiosk.sh's old system-browser detection (chromium-browser /
// chromium / google-chrome / firefox) with one pinned Chromium version.

import { app, BrowserWindow, Menu } from 'electron';
import { allowedOrigins, isNavigationAllowed } from './navigationPolicy.js';
import { resolveTargetUrl } from './resolveTargetUrl.js';
import { kioskWindowOptions } from './windowOptions.js';

const targetUrl = resolveTargetUrl(process.env, process.argv);
const origins = allowedOrigins(process.env, targetUrl);

Menu.setApplicationMenu(null);

function createWindow() {
  const win = new BrowserWindow(kioskWindowOptions(process.env));

  win.setMenuBarVisibility(false);
  // The kiosk shell only ever shows the OpenFlight UI itself; deny any
  // attempt (e.g. target="_blank" links) to pop a second window. window.open()
  // then returns null, and the tester page's Kiosk view navigates this window.
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  // ...and it goes only to the origins the launcher allowed (navigationPolicy.js).
  const refuseOtherOrigins = (event) => {
    if (!isNavigationAllowed(event.url, origins)) event.preventDefault();
  };
  win.webContents.on('will-frame-navigate', refuseOtherOrigins);
  win.webContents.on('will-redirect', refuseOtherOrigins);
  win.loadURL(targetUrl);

  win.on('closed', () => {
    app.quit();
  });
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  app.quit();
});
