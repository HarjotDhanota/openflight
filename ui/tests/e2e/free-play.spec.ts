import { expect, test } from '@playwright/test';
import { gotoApp, KIOSK_VIEWPORTS, simulateShot, withControlSocket } from './helpers';
import { useTester } from './testerHelpers';

for (const viewport of KIOSK_VIEWPORTS) {
  test.describe(`free play at ${viewport.width}x${viewport.height}`, () => {
    test.use({ viewport, hasTouch: true });
    test('touch stop keeps metrics visible, packages, then closes with Done', async ({ page }) => {
      let phase = 'recording';
      let studyPhase = 'ladder';
      let studyText = 'Exposure study: 150 µs · swing 3 of 5 · setting 3 of 6';
      const actions: string[] = [];
      const bundle = {
        name: '20260922-name-session-bundle-20261019T143012Z.zip',
        path: '/home/openflight/openflight_sessions/.bundles/20260922-name-session-bundle-20261019T143012Z.zip',
      };
      await page.route('**/api/free-play', async (route) => {
        if (route.request().method() === 'POST') {
          const { action } = route.request().postDataJSON();
          actions.push(action);
          if (action === 'stop') phase = 'packaging';
        }
        await route.fulfill({
          json: {
            enabled: true,
            phase,
            geometry_unverified: true,
            bundle: phase === 'ready' ? bundle : null,
            exposure_study: { phase: studyPhase, text: studyText, changing: false },
          },
        });
      });
      await withControlSocket(async (socket) => {
        await simulateShot(socket);
      });
      await gotoApp(page);
      await page.evaluate(async () => {
        const modulePath = '/src/stores/useSystemStore.ts';
        const { useSystemStore } = await import(modulePath);
        useSystemStore
          .getState()
          .setReadyLight({ state: 'green', cause: 'Ready for a swing', word: 'SWING', swing: null, time_left_s: null });
      });
      await page.getByRole('button', { name: 'Close Select club' }).tap();
      const shot = (name: string) =>
        page.screenshot({ path: test.info().outputPath(`free-play-${name}-${viewport.width}x${viewport.height}.png`) });

      // the ready light keeps its own size beside the controls: it is read from address
      const word = page.locator('.ready-light__word');
      await expect(word).toHaveText('SWING');
      const wordSize = await word.evaluate((node) => parseFloat(getComputedStyle(node).fontSize));
      expect(wordSize).toBeGreaterThanOrEqual(viewport.height > 520 ? 64 : 44);
      const wordBox = await word.boundingBox();
      const lightBox = await page.locator('.ready-light').boundingBox();
      expect(wordBox!.x + wordBox!.width).toBeLessThanOrEqual(lightBox!.x + lightBox!.width);

      const stop = page.getByRole('button', { name: 'Stop & package', exact: true });
      await expect(stop).toBeVisible();
      await expect(page.getByText('Geometry unverified', { exact: true })).toBeVisible();
      const box = await stop.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.y + box!.height).toBeLessThanOrEqual(viewport.height);
      expect(box!.height).toBeGreaterThanOrEqual(44);
      // an active action while recording, never the grey of a disabled one (19 Oct)
      await expect(stop).toBeEnabled();
      const fill = (selector: string) =>
        page
          .locator(selector)
          .first()
          .evaluate((node) => getComputedStyle(node).backgroundColor);
      const stopFill = await stop.evaluate((node) => getComputedStyle(node).backgroundColor);
      expect(stopFill).not.toBe(await fill('.free-play'));
      expect(stopFill).toBe(
        await page.evaluate(() => {
          const probe = document.createElement('div');
          probe.style.background = 'var(--color-accent-block)';
          document.body.append(probe);
          const value = getComputedStyle(probe).backgroundColor;
          probe.remove();
          return value;
        })
      );
      await expect(page.locator('.live-panel__grid .metric-card')).toHaveCount(10);
      // the controls sit inside the ready light's band: the tiles get exactly the
      // room they have in a swings kiosk, so free play needs no tile overrides
      const bandHeight = viewport.height > 520 ? 96 : 60;
      expect((await page.locator('.session-status').boundingBox())!.height).toBeLessThanOrEqual(bandHeight + 0.5);
      for (const tile of await page.locator('.live-panel__grid .metric-card').all()) {
        const bounds = await tile.boundingBox();
        expect(bounds!.height).toBeGreaterThan(0);
        expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(viewport.height);
      }
      // the exposure study runs itself: one progress line in the band, no prompt
      const study = page.locator('.free-play__study');
      await expect(study).toHaveText(studyText);
      const studyBox = (await study.boundingBox())!;
      const panelBox = (await page.locator('.free-play').boundingBox())!;
      expect(studyBox.y).toBeGreaterThanOrEqual(panelBox.y - 0.5);
      expect(studyBox.y + studyBox.height).toBeLessThanOrEqual(panelBox.y + panelBox.height + 0.5);
      expect(studyBox.x + studyBox.width).toBeLessThanOrEqual(box!.x + 0.5);
      // the whole line reads: wrapped onto at most two lines, never cut
      expect(await study.evaluate((node) => node.scrollHeight <= node.clientHeight + 1)).toBe(true);
      expect((await page.locator('.session-status').boundingBox())!.height).toBeLessThanOrEqual(bandHeight + 0.5);
      await shot('recording');

      studyPhase = 'automatic';
      studyText = 'Automatic exposure';
      await expect(study).toHaveText('Automatic exposure');

      await stop.tap();
      await expect(stop).toBeDisabled();
      await expect(page.getByText('Stopping and packaging…', { exact: true })).toBeVisible();
      // acquisition is stopping: the controls take the light's band, at its height
      await expect(page.locator('.ready-light')).toBeHidden();
      expect((await page.locator('.session-status').boundingBox())!.height).toBeLessThanOrEqual(bandHeight + 0.5);
      await shot('packaging');

      phase = 'ready';
      const ready = page.getByText(`Package ready: ${bundle.name} · send it from the tester page (step 8)`, {
        exact: true,
      });
      await expect(ready).toBeVisible();
      // the unit's own folders stay off the scoreboard (decision 3, 19 Oct)
      await expect(page.getByText(bundle.path)).toHaveCount(0);
      const done = page.getByRole('button', { name: 'Done', exact: true });
      await expect(done).toBeEnabled();
      const panel = await page.locator('.free-play').boundingBox();
      const readyBox = await ready.boundingBox();
      const doneBox = await done.boundingBox();
      // the whole file name shows: inside the panel, clear of the Done button
      expect(readyBox!.x + readyBox!.width).toBeLessThanOrEqual(doneBox!.x);
      expect(readyBox!.y).toBeGreaterThanOrEqual(panel!.y);
      expect(readyBox!.y + readyBox!.height).toBeLessThanOrEqual(panel!.y + panel!.height + 1);
      expect(doneBox!.y + doneBox!.height).toBeLessThanOrEqual(viewport.height);
      expect(doneBox!.height).toBeGreaterThanOrEqual(44);
      // a typical tester id fits the band; a longer one may wrap and grow it, never clip
      expect((await page.locator('.session-status').boundingBox())!.height).toBeLessThanOrEqual(bandHeight + 0.5);
      await shot('ready');
      await done.tap();
      expect(actions).toEqual(['stop', 'close']);
    });

    test('the phone page restores the active run and stops that run without grading', async ({ page }) => {
      await useTester(page);
      let phase = 'recording';
      const identity = { tester_id: '20260922-name', run_id: 'free-run', enabled: true };
      await page.route('**/api/tester/status**', (route) =>
        route.fulfill({
          json: { free_play: { ...identity, phase }, ball_at_address: { state: 'in_box', range_m: 2.21 } },
        })
      );
      await page.route('**/api/tester/free-play/stop', async (route) => {
        expect(route.request().postDataJSON()).toEqual({ tester_id: identity.tester_id, run_id: identity.run_id });
        phase = 'stopping';
        await route.fulfill({ json: { ...identity, phase } });
      });
      await page.goto('/tester.html');
      await expect(page.locator('#free-play-status')).toHaveText('Free play is recording.');
      // the kiosk's ball at address shows during free play, as in the swings step
      await expect(page.locator('#address-status')).toContainText('the ball is in the box');
      await expect(page.locator('#step-free-play')).toBeDisabled();
      await expect(page.locator('#verdict-card')).toBeHidden();
      await expect(page.locator('#ladder-controls')).toBeHidden();
      await page.locator('#free-play-stop').tap();
      await expect(page.locator('#free-play-status')).toHaveText('stopping');
      await expect(page.locator('#free-play-stop')).toBeDisabled();
    });

    test('the Tester button and Face photo bring the tester page back', async ({ page }) => {
      const tester = 'http://127.0.0.1:8765/';
      let paused: object | null = null;
      const pauses: object[] = [];
      await page.route('**/api/free-play', (route) =>
        route.fulfill({ json: { enabled: true, phase: 'recording', tester_url: tester, paused } })
      );
      await page.route('**/api/tester-page', (route) =>
        route.fulfill({ json: { available: true, url: tester, reason: null, final: false } })
      );
      await page.route('**/api/free-play/pause', async (route) => {
        pauses.push(route.request().postDataJSON());
        paused = { cause: 'photo' };
        await route.fulfill({ json: { pause: paused, paused } });
      });
      await page.route(`${tester}**`, (route) => route.fulfill({ contentType: 'text/html', body: 'tester page' }));
      await gotoApp(page);
      await page.getByRole('button', { name: 'Close Select club' }).tap();

      const header = page.locator('.panel-header');
      const back = page.locator('.session-status').getByRole('button', { name: 'Back to the tester page' });
      const photo = header.getByRole('button', { name: 'Photograph the face' });
      await expect(back).toHaveText('Tester');
      await expect(photo).toHaveText('Face photo');
      // one way back, never a second Setup in the header
      await expect(header.getByRole('button', { name: 'Setup', exact: true })).toHaveCount(0);
      // the Tester button sits in the band, finger-sized, inside the screen
      const backBox = (await back.boundingBox())!;
      expect(backBox.height).toBeGreaterThanOrEqual(44);
      expect(backBox.x + backBox.width).toBeLessThanOrEqual(viewport.width);
      // the face photo fits the header beside Change club, clear of the club and hand
      const club = (await page.locator('.panel-header__club').boundingBox())!;
      for (const button of [photo, header.getByRole('button', { name: 'Change club' })]) {
        const box = (await button.boundingBox())!;
        expect(box.x).toBeGreaterThanOrEqual(club.x + club.width);
        expect(box.x + box.width).toBeLessThanOrEqual(viewport.width);
      }
      await page.screenshot({
        path: test.info().outputPath(`free-play-header-${viewport.width}x${viewport.height}.png`),
      });

      await back.tap();
      await expect(page).toHaveURL(tester);
      await gotoApp(page);
      await page.getByRole('button', { name: 'Close Select club' }).tap();
      await page.locator('.panel-header').getByRole('button', { name: 'Photograph the face' }).tap();
      await expect(page).toHaveURL(`${tester}#free-play-photo`);
      expect(pauses).toEqual([{ cause: 'photo' }]);
    });
  });
}

test('the Tester button focuses the tester page that opened the scoreboard', async ({ page }) => {
  await page.route('**/api/free-play', (route) =>
    route.fulfill({ json: { enabled: true, phase: 'recording', tester_url: 'http://127.0.0.1:8765/' } })
  );
  await gotoApp(page, '/tester-opener-stub');
  await page.evaluate(() => {
    (window as unknown as { focused: number }).focused = 0;
    window.focus = () => {
      (window as unknown as { focused: number }).focused += 1;
    };
  });
  const [scoreboard] = await Promise.all([
    page.waitForEvent('popup'),
    page.evaluate(() => window.open('/', 'openflight-kiosk')),
  ]);
  await scoreboard.route('**/api/free-play', (route) =>
    route.fulfill({ json: { enabled: true, phase: 'recording', tester_url: 'http://127.0.0.1:8765/' } })
  );
  await scoreboard.route('**/api/tester-page', (route) =>
    route.fulfill({ json: { available: true, url: 'http://127.0.0.1:8765/', reason: null, final: false } })
  );
  await scoreboard.reload();
  await scoreboard.getByRole('button', { name: 'Close Select club' }).click();
  await scoreboard.getByRole('button', { name: 'Back to the tester page' }).click();
  await expect.poll(() => page.evaluate(() => (window as unknown as { focused: number }).focused)).toBe(1);
  expect(new URL(scoreboard.url()).pathname).toBe('/');
});

test.describe('a kiosk the tester page showed in its own window', () => {
  const tester = 'http://127.0.0.1:8765/';
  async function mockRun(page: import('@playwright/test').Page, start = 'recording') {
    let phase = start;
    await page.route('**/api/free-play', async (route) => {
      const action = route.request().method() === 'POST' ? route.request().postDataJSON().action : null;
      if (action === 'stop') phase = 'packaging';
      if (action === 'close') return route.fulfill({ json: { closing: true } });
      await route.fulfill({ json: { enabled: true, phase, tester_url: tester, bundle: { name: 'session.zip' } } });
    });
    await page.route(`${tester}**`, (route) => route.fulfill({ contentType: 'text/html', body: 'tester page' }));
  }

  test('opens without the club picker: the tester page chose the club', async ({ page }) => {
    await mockRun(page);
    await gotoApp(page, '/?from=tester');
    await expect(page.locator('.panel-header')).toBeVisible();
    await expect(page.getByRole('dialog', { name: 'Select club' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Close Select club' })).toHaveCount(0);
  });

  test('goes back to the tester page when free play stops', async ({ page }) => {
    await mockRun(page);
    await gotoApp(page, '/?from=tester');
    await page.getByRole('button', { name: 'Stop & package', exact: true }).click();
    await expect(page).toHaveURL(tester);
  });

  test('Done there goes back to the tester page too: the kiosk it closes is gone', async ({ page }) => {
    await mockRun(page, 'ready');
    await gotoApp(page, '/?from=tester');
    await page.getByRole('button', { name: 'Done', exact: true }).click();
    await expect(page).toHaveURL(tester);
  });

  test('a kiosk in its own window (the page on a phone) stays on the scoreboard', async ({ page }) => {
    await mockRun(page);
    await gotoApp(page);
    await page.getByRole('button', { name: 'Close Select club' }).click();
    await page.getByRole('button', { name: 'Stop & package', exact: true }).click();
    await expect(page.getByText('Stopping and packaging…')).toBeVisible();
    expect(new URL(page.url()).pathname).toBe('/');
  });
});
