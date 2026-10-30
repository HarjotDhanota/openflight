import { expect, test } from '@playwright/test';
import { gotoApp, KIOSK_VIEWPORTS, simulateShot, withControlSocket } from './helpers';
import { fulfillJson, mockConfirmedPlacementBox, mockSetupDone, RECORDING, useTester } from './testerHelpers';

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
            trigger_policy: { label: 'Hybrid confirmation (OPS retention)' },
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
      expect(
        await page
          .locator('.native-sync > span')
          .first()
          .evaluate((node) => node.scrollHeight <= node.clientHeight + 1)
      ).toBe(true);
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
      await expect(
        page.getByText('Hybrid confirmation (OPS retention): Armed: make one swing.', { exact: true })
      ).toBeVisible();
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
      let stopped = false;
      await page.route('**/api/tester/free-play/native-sync', (route) =>
        route.fulfill({
          json: {
            configured: true,
            trigger_policies: [
              { id: 'sound', label: 'Sound baseline', available: true, reason: 'Existing sound capture route.' },
              {
                id: 'independent',
                label: 'Independent native detectors',
                available: true,
                reason: 'Each radar retains independently.',
              },
              {
                id: 'ops_led',
                label: 'OPS-led (IWR association only)',
                available: true,
                reason: 'OPS cannot initiate IWR capture.',
              },
              {
                id: 'hybrid',
                label: 'Hybrid confirmation (OPS retention)',
                available: true,
                reason: 'Retain promptly; confirm by clock/coverage.',
              },
              {
                id: 'iwr_led',
                label: 'IWR-led (recommended experiment)',
                available: false,
                reason: 'IWR notification exceeds OPS prehistory.',
              },
              {
                id: 'common_hardware',
                label: 'Common hardware trigger',
                available: false,
                reason: 'No verified common wiring.',
              },
            ],
            enabled: true,
            phase: stopped ? 'stopped' : 'reset',
            phase_token: stopped ? 'final-identity' : 'reset-identity',
            message: 'Press RESET once, wait at least one second, then confirm.',
            normal_boot_restore_needed: true,
            resources_released: stopped,
            restore_instructions: 'Restore normal IWR boot after stopping.',
          },
        })
      );
      await page.route('**/api/tester/free-play/native-sync/command', async (route) => {
        requests.push(route.request().postDataJSON());
        await route.fulfill({ json: { accepted: true } });
      });
      await page.goto('/tester.html');
      await expect(page.locator('#native-free-play-choice')).toBeVisible();
      await expect(page.locator('#native-free-play-message')).toContainText('Press RESET once');
      await expect(page.locator('#native-free-play-restored')).toBeHidden();
      await expect(page.locator('#native-free-play')).toHaveValue('sound');
      await expect(page.locator('#native-free-play option[value="iwr_led"]')).toBeDisabled();
      await expect(page.locator('#native-free-play option[value="common_hardware"]')).toBeDisabled();
      await page.locator('#native-free-play').selectOption('ops_led');
      await expect(page.locator('#trigger-policy-reason')).toContainText('OPS cannot initiate IWR capture.');
      await page.waitForTimeout(1100);
      await expect(page.locator('#native-free-play')).toHaveValue('ops_led');
      await page.locator('#native-free-play-confirm').tap();
      expect(requests).toEqual([{ command: 'reset_pressed', phase_token: 'reset-identity' }]);
      stopped = true;
      await expect(page.locator('#native-free-play-message')).toContainText('Restore normal IWR boot');
      await page.locator('#native-free-play-restored').tap();
      expect(requests[1]).toEqual({ command: 'restored', phase_token: 'final-identity' });
      await page.screenshot({ path: test.info().outputPath('native-tester-reset.png') });
    });

    test('selected policy reaches Start and stays locked during recording', async ({ page }) => {
      await useTester(page);
      const world: { freePlay: object } = { freePlay: { enabled: false } };
      await mockSetupDone(page, world);
      await page.route('**/api/tester/free-play/native-sync', (route) =>
        fulfillJson(route, {
          configured: true,
          enabled: false,
          trigger_policies: [
            { id: 'sound', label: 'Sound baseline', available: true, reason: 'Sound comparison.' },
            {
              id: 'hybrid',
              label: 'Hybrid confirmation (OPS retention)',
              available: true,
              reason: 'Retain before confirmation.',
            },
          ],
        })
      );
      const starts: Record<string, unknown>[] = [];
      await page.route('**/api/tester/run', async (route) => {
        starts.push(route.request().postDataJSON());
        world.freePlay = RECORDING;
        await fulfillJson(route, { free_play: RECORDING, job: { state: 'running', action: 'free_play' } }, 202);
      });
      await page.goto('/tester.html');
      await page.locator('#free-play-resolution button[data-resolution="arm5"]').tap();
      await page.locator('#first-club button[data-club="7-iron"]').tap();
      await page.locator('#native-free-play').selectOption('hybrid');
      await page.locator('#step-free-play').tap();
      await expect.poll(() => starts.length).toBe(1);
      expect(starts[0]).toMatchObject({ action: 'free_play', trigger_policy: 'hybrid' });
      await expect(page.locator('#native-free-play')).toBeDisabled();
    });
  });
}
