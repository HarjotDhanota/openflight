import { expect, test } from '@playwright/test';
import { gotoApp, KIOSK_VIEWPORTS, simulateShot, withControlSocket } from './helpers';

for (const viewport of KIOSK_VIEWPORTS) {
  test.describe(`manual exposure at ${viewport.width}x${viewport.height}`, () => {
    test.use({ viewport, hasTouch: true });
    test('tap advances early, preserves progress, and reports a refused change', async ({ page }) => {
      let exposure = 300;
      let changing = false;
      let refuse = false;
      const requests: object[] = [];
      const study = () => ({
        phase: exposure === 75 ? 'automatic' : 'ladder',
        text: `Exposure study: ${exposure} µs · swing ${exposure === 300 ? 3 : 1} of 5 · setting 1 of 2`,
        changing,
        rung: { rung_id: `full-${exposure}` },
      });
      await page.route('**/api/free-play', (route) =>
        route.fulfill({
          json: {
            enabled: true,
            phase: 'recording',
            tester_id: 'tester',
            run_id: 'run',
            geometry_unverified: true,
            exposure_study: study(),
          },
        })
      );
      await page.route('**/api/free-play/next-exposure', async (route) => {
        requests.push(route.request().postDataJSON());
        if (refuse) {
          await route.fulfill({ status: 409, json: { error: 'wait for the camera capture to finish' } });
          return;
        }
        exposure = exposure === 300 ? 150 : 75;
        changing = true;
        await route.fulfill({ json: { exposure_study: study() } });
      });
      await withControlSocket(async (socket) => {
        await simulateShot(socket);
      });
      await gotoApp(page);
      await page.getByRole('button', { name: 'Close Select club' }).tap();
      const next = page.getByRole('button', { name: 'Next exposure', exact: true });
      const photo = page.getByRole('button', { name: 'Photograph the face' });
      const club = page.getByRole('button', { name: 'Change club', exact: true });
      await expect(next).toBeEnabled();
      for (const button of [next, photo, club]) {
        const bounds = (await button.boundingBox())!;
        expect(bounds.height).toBeGreaterThanOrEqual(44);
        expect(bounds.x).toBeGreaterThanOrEqual(0);
        expect(bounds.x + bounds.width).toBeLessThanOrEqual(viewport.width);
      }
      const nextBounds = (await next.boundingBox())!;
      const photoBounds = (await photo.boundingBox())!;
      expect(nextBounds.x + nextBounds.width).toBeLessThanOrEqual(photoBounds.x);
      const identity = (await page.locator('.panel-header__identity').boundingBox())!;
      expect(identity.x + identity.width).toBeLessThanOrEqual(nextBounds.x);
      await expect(page.locator('.live-panel__grid .metric-card')).toHaveCount(10);
      for (const tile of await page.locator('.live-panel__grid .metric-card').all()) {
        const bounds = (await tile.boundingBox())!;
        expect(bounds.height).toBeGreaterThan(0);
        expect(bounds.y + bounds.height).toBeLessThanOrEqual(viewport.height);
      }
      await page.screenshot({
        path: test.info().outputPath(`manual-exposure-${viewport.width}x${viewport.height}.png`),
      });
      await expect(page.locator('.free-play__study')).toContainText('swing 3 of 5');
      await next.tap();
      expect(requests).toEqual([{ rung_id: 'full-300', tester_id: 'tester', run_id: 'run' }]);
      await expect(next).toBeDisabled();
      await expect(photo).toBeDisabled();
      await expect(page.locator('.free-play__study')).toContainText('150 µs');
      changing = false;
      await expect(next).toBeEnabled();
      refuse = true;
      await next.tap();
      await expect(page.getByRole('alert')).toContainText('wait for the camera capture to finish');
      await expect(page.locator('.free-play__study')).toContainText('150 µs');
      refuse = false;
      await next.tap();
      await expect(next).toHaveCount(0);
      expect(requests).toHaveLength(3);
    });
  });
}
