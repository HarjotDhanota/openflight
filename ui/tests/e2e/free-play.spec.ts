import { expect, test } from '@playwright/test';
import {
  gotoApp,
  KIOSK_VIEWPORTS,
  overflowingLiveMetricMetadata,
  overflowingLiveMetricValues,
  simulateShot,
  useTester,
  withControlSocket,
} from './helpers';

for (const viewport of KIOSK_VIEWPORTS) {
  test.describe(`free play at ${viewport.width}x${viewport.height}`, () => {
    test.use({ viewport, hasTouch: true });
    test('touch stop keeps metrics visible, packages, then closes with Done', async ({ page }) => {
      let phase = 'recording';
      const actions: string[] = [];
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
            bundle: phase === 'ready' ? { name: 'tester.zip', path: '/sessions/.bundles/tester.zip' } : null,
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
      const stop = page.getByRole('button', { name: 'Stop & package', exact: true });
      await expect(stop).toBeVisible();
      await expect(page.getByText('Geometry unverified', { exact: true })).toBeVisible();
      const box = await stop.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.y + box!.height).toBeLessThanOrEqual(viewport.height);
      expect(box!.height).toBeGreaterThanOrEqual(44);
      await expect(page.locator('.live-panel__grid .metric-card')).toHaveCount(10);
      await expect(page.getByText('SWING', { exact: true })).toBeVisible();
      await expect.poll(() => overflowingLiveMetricMetadata(page)).toEqual([]);
      await expect.poll(() => overflowingLiveMetricValues(page)).toEqual([]);
      for (const tile of await page.locator('.live-panel__grid .metric-card').all()) {
        const bounds = await tile.boundingBox();
        expect(bounds!.height).toBeGreaterThan(0);
        expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(viewport.height);
      }
      await page.screenshot({ path: test.info().outputPath('free-play-recording.png') });
      await stop.tap();
      await expect(stop).toBeDisabled();
      phase = 'ready';
      await expect(page.getByText('Package ready', { exact: true })).toBeVisible();
      await expect(page.getByText('/sessions/.bundles/tester.zip')).toBeVisible();
      await page.getByRole('button', { name: 'Done', exact: true }).tap();
      expect(actions).toEqual(['stop', 'close']);
    });

    test('the phone page restores the active run and stops that run without grading', async ({ page }) => {
      await useTester(page);
      let phase = 'recording';
      const identity = { tester_id: '20260922-name', run_id: 'free-run', enabled: true };
      await page.route('**/api/tester/status**', (route) =>
        route.fulfill({ json: { free_play: { ...identity, phase } } })
      );
      await page.route('**/api/tester/free-play/stop', async (route) => {
        expect(route.request().postDataJSON()).toEqual({ tester_id: identity.tester_id, run_id: identity.run_id });
        phase = 'stopping';
        await route.fulfill({ json: { ...identity, phase } });
      });
      await page.goto('/tester.html');
      await expect(page.locator('#free-play-status')).toHaveText('Free play is recording.');
      await expect(page.locator('#step-free-play')).toBeDisabled();
      await expect(page.locator('#verdict-card')).toBeHidden();
      await expect(page.locator('#ladder-controls')).toBeHidden();
      await page.locator('#free-play-stop').tap();
      await expect(page.locator('#free-play-status')).toHaveText('stopping');
      await expect(page.locator('#free-play-stop')).toBeDisabled();
    });
  });
}
