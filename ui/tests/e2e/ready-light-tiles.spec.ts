import { expect, test, type Page } from '@playwright/test';
import {
  gotoApp,
  KIOSK_VIEWPORTS,
  overflowingLiveMetricMetadata,
  overflowingLiveMetricValues,
  simulateShot,
  withControlSocket,
} from './helpers';

/**
 * With the ready light on (a tester's kiosk: swings or free play) the tiles get the
 * band's height less: a three-digit speed's "mph" and the club path's
 * "(D-plane)" note must still fit, and the light keeps its own size.
 */
async function showReadyLightAndAThreeDigitShot(page: Page) {
  await page.evaluate(async () => {
    const systemPath = '/src/stores/useSystemStore.ts';
    const shotPath = '/src/stores/useShotStore.ts';
    const { useSystemStore } = await import(systemPath);
    const { useShotStore } = await import(shotPath);
    useSystemStore
      .getState()
      .setReadyLight({ state: 'green', cause: 'Ready for a swing', word: 'SWING', swing: null, time_left_s: null });
    const latest = useShotStore.getState().latestShot;
    const shot = {
      ...latest,
      ball_speed_mph: 168.4,
      club_speed_mph: 118.6,
      smash_factor: 1.42,
      // the camera-fused path with its D-plane face angle: the longest club-path note
      club_path_deg: null,
      experimental_fused_status: 'ok',
      experimental_fused_club_path_deg: -13.2,
      experimental_fused_club_path_confidence: 'medium',
      experimental_face_angle_deg: -12.4,
      experimental_face_angle_status: 'd_plane_estimate',
    };
    delete (shot as { metrics?: unknown }).metrics;
    useShotStore.setState({ latestShot: shot, shots: [shot] });
  });
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => resolve())));
}

for (const viewport of KIOSK_VIEWPORTS) {
  test.describe(`tiles beside the ready light at ${viewport.width}x${viewport.height}`, () => {
    test.use({ viewport, hasTouch: true });

    test('three-digit speeds keep their unit and the D-plane note fits', async ({ page }) => {
      await withControlSocket(async (socket) => {
        await simulateShot(socket);
      });
      await gotoApp(page);
      await page.getByRole('button', { name: 'Close Select club' }).click();
      await expect(page.locator('.live-panel__grid .metric-card')).toHaveCount(10);
      await showReadyLightAndAThreeDigitShot(page);

      const word = page.locator('.ready-light__word');
      await expect(word).toHaveText('SWING');
      // the fix gives the tiles room, never by shrinking the ready light
      const wordSize = await word.evaluate((node) => parseFloat(getComputedStyle(node).fontSize));
      expect(wordSize).toBeGreaterThanOrEqual(viewport.height > 520 ? 64 : 44);
      await expect(page.locator('.live-panel__grid .metric-card')).toHaveCount(10);
      await expect(page.locator('.metric-card__value').first()).toHaveText('168.4');
      await expect(page.getByText(/D-plane/)).toBeVisible();

      expect(await overflowingLiveMetricValues(page)).toEqual([]);
      expect(await overflowingLiveMetricMetadata(page)).toEqual([]);
      for (const tile of await page.locator('.live-panel__grid .metric-card').all()) {
        const bounds = await tile.boundingBox();
        expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(viewport.height + 0.5);
      }
      await page.screenshot({
        path: test.info().outputPath(`ready-light-tiles-${viewport.width}x${viewport.height}.png`),
      });
    });
  });
}
