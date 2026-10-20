import { expect, test } from '@playwright/test';
import {
  gotoApp,
  KIOSK_VIEWPORTS,
  simulateShot,
  useTester,
  withControlSocket,
} from './helpers';

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
        page.locator(selector).first().evaluate((node) => getComputedStyle(node).backgroundColor);
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
  });
}
