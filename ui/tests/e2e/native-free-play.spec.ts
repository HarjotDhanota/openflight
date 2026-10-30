import { expect, test } from '@playwright/test';
import { gotoApp, KIOSK_VIEWPORTS, simulateShot, withControlSocket } from './helpers';
import { mockConfirmedPlacementBox, useTester } from './testerHelpers';

for (const viewport of KIOSK_VIEWPORTS) {
  test.describe(`native free play ${viewport.width}x${viewport.height}`, () => {
    test.use({ viewport, hasTouch: true });
    test('normal kiosk prepares native capture and keeps live tiles visible', async ({ page }) => {
      let phase = 'next';
      const requests: object[] = [];
      await page.route('**/api/free-play', (route) =>
        route.fulfill({
          json: {
            enabled: true,
            phase: 'recording',
            native_sync_enabled: true,
          },
        })
      );
      await page.route('**/api/native-sync', (route) =>
        route.fulfill({
          json: {
            enabled: true,
            phase,
            phase_token: 'owned-attempt',
            message:
              phase === 'next'
                ? 'Remove the ball, leave the hitting area empty, then prepare the next attempt.'
                : 'Armed: make one swing.',
          },
        })
      );
      await page.route('**/api/native-sync/next', async (route) => {
        requests.push(route.request().postDataJSON());
        phase = 'armed';
        await route.fulfill({ json: { accepted: true } });
      });
      await withControlSocket(async (socket) => {
        await simulateShot(socket);
      });
      await gotoApp(page, '/?from=tester');
      await page.evaluate(async () => {
        const path = '/src/stores/useSystemStore.ts';
        const { useSystemStore } = await import(path);
        useSystemStore
          .getState()
          .setReadyLight({ state: 'green', cause: 'Ready for a swing', word: 'SWING', swing: null, time_left_s: null });
      });
      const prepare = page.getByRole('button', { name: 'Area empty; prepare next attempt' });
      await expect(prepare).toBeVisible();
      expect((await page.locator('.session-status').boundingBox())!.height).toBeLessThanOrEqual(
        viewport.height > 520 ? 96.5 : 60.5
      );
      for (const button of [prepare, page.getByRole('button', { name: 'Stop & package', exact: true })]) {
        const box = (await button.boundingBox())!;
        expect(box.height).toBeGreaterThanOrEqual(44);
        expect(box.x).toBeGreaterThanOrEqual(0);
        expect(box.x + box.width).toBeLessThanOrEqual(viewport.width);
      }
      await page.screenshot({ path: test.info().outputPath('native-kiosk-next.png') });
      await prepare.tap();
      await expect(page.getByText('Armed: make one swing.', { exact: true })).toBeVisible();
      expect(requests).toEqual([{ phase_token: 'owned-attempt' }]);
      await expect(prepare).toHaveCount(0);
      await expect(page.locator('.live-panel__grid .metric-card')).toHaveCount(10);
      for (const tile of await page.locator('.live-panel__grid .metric-card').all()) {
        const box = (await tile.boundingBox())!;
        expect(box.y + box.height).toBeLessThanOrEqual(viewport.height);
      }
      await page.screenshot({ path: test.info().outputPath('native-kiosk-armed.png') });
    });

    test('normal tester page shows phase-bound RAM confirmation', async ({ page }) => {
      await useTester(page);
      await mockConfirmedPlacementBox(page);
      const requests: object[] = [];
      await page.route('**/api/tester/free-play/native-sync', (route) =>
        route.fulfill({
          json: {
            configured: true,
            enabled: true,
            phase: 'reset',
            phase_token: 'reset-identity',
            message: 'Press RESET once, wait at least one second, then confirm.',
          },
        })
      );
      await page.route('**/api/tester/free-play/native-sync/command', async (route) => {
        requests.push(route.request().postDataJSON());
        await route.fulfill({ json: { accepted: true } });
      });
      await page.goto('/tester.html');
      await expect(page.locator('#native-free-play-choice')).toBeVisible();
      await page.locator('#native-free-play').check();
      await page.locator('#native-free-play-confirm').tap();
      expect(requests).toEqual([{ command: 'reset_pressed', phase_token: 'reset-identity' }]);
      await page.screenshot({ path: test.info().outputPath('native-tester-reset.png') });
    });
  });
}
