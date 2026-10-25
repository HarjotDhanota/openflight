import { expect, test, type Page } from '@playwright/test';
import {
  drawnAt,
  gotoApp,
  KIOSK_VIEWPORTS,
  mockSetupAndPreview,
  overflowingLiveMetricMetadata,
  overflowingLiveMetricValues,
  SET_UP,
  setHanded,
  setUpSnapshot,
  simulateShot,
  withControlSocket,
} from './helpers';

/** With the ready light on, a three-digit "mph" and the "(D-plane)" note still fit. */
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
      // the ready light never shrinks to make room
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
    });
  });
}

const AIM_PICTURE = '.live-panel__aim-view .setup-picture';

/**
 * The green light a kiosk shows with every sensor ready and its setup finished: the mock
 * backend reports no light at all, and every setup used here is finished.
 */
async function showGreenLight(page: Page) {
  await page.evaluate(async () => {
    const { useSystemStore } = await import('/src/stores/useSystemStore.ts');
    useSystemStore
      .getState()
      .setReadyLight({ state: 'green', cause: 'Ready for a swing', word: 'SWING', swing: null, time_left_s: null });
  });
}

async function openLive(page: Page, hand: 'RH' | 'LH') {
  await gotoApp(page);
  await page.getByRole('button', { name: 'Close Select club' }).click();
  await setHanded(page, hand);
  await showGreenLight(page);
  await expect(page.locator('.live-panel__grid .metric-card')).toHaveCount(10);
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => resolve())));
}

async function tileHeights(page: Page): Promise<number[]> {
  return page
    .locator('.live-panel__grid .metric-card')
    .evaluateAll((tiles) => tiles.map((tile) => tile.getBoundingClientRect().height));
}

for (const viewport of KIOSK_VIEWPORTS) {
  for (const hand of ['RH', 'LH'] as const) {
    test.describe(`the aim line on the live view at ${viewport.width}x${viewport.height} ${hand}`, () => {
      test.use({ viewport, hasTouch: true });

      test('the target strip opens it over the tiles, and the tiles keep their height', async ({ page }) => {
        await withControlSocket(async (socket) => {
          await simulateShot(socket);
        });
        // a finished setup with the aim skipped: the strip is words only
        const kiosk = await mockSetupAndPreview(
          page,
          setUpSnapshot({ aim: { state: 'skipped', source: 'skipped', reason: 'the tester skipped the aim point' } })
        );
        await openLive(page, hand);
        await expect(page.locator('.live-panel__target')).toHaveText(/Target line not set/);
        const words = await tileHeights(page);

        kiosk.use(setUpSnapshot());
        await openLive(page, hand);
        const strip = page.locator('.live-panel__target-button');
        await expect(strip).toHaveText(/Target line set \+4\.1° ±0\.8° ›/);
        await expect(page.getByRole('button', { name: 'Finish setup' })).toHaveCount(0);
        expect(await tileHeights(page)).toEqual(words);
        expect(await overflowingLiveMetricValues(page)).toEqual([]);
        expect(await overflowingLiveMetricMetadata(page)).toEqual([]);
        await expect(page.locator('.live-panel__aim-view')).toHaveCount(0);

        await strip.click();
        const line = page.locator(`${AIM_PICTURE} .setup-picture__aim-line`);
        await expect(line).toBeVisible();
        const svg = (await page.locator(`${AIM_PICTURE} .setup-picture__overlay`).boundingBox())!;
        const ends = await line.evaluate((node) =>
          ['x1', 'y1', 'x2', 'y2'].map((name) => Number(node.getAttribute(name)))
        );
        const from = await drawnAt(page, AIM_PICTURE, SET_UP.ball, hand);
        const to = await drawnAt(page, AIM_PICTURE, SET_UP.aim, hand);
        expect(Math.abs(svg.x + ends[0] - from[0])).toBeLessThan(1);
        expect(Math.abs(svg.y + ends[1] - from[1])).toBeLessThan(1);
        expect(Math.abs(svg.x + ends[2] - to[0])).toBeLessThan(1);
        expect(Math.abs(svg.y + ends[3] - to[1])).toBeLessThan(1);
        // a right-hander's aim point is right of the ball on the picture, a left-hander's left
        expect(Math.sign(ends[2] - ends[0])).toBe(hand === 'RH' ? 1 : -1);
        // big enough to see what the line points at
        expect(Math.hypot(ends[2] - ends[0], ends[3] - ends[1])).toBeGreaterThanOrEqual(60);
        const picture = (await page.locator(`${AIM_PICTURE} img`).boundingBox())!;
        expect(Math.min(picture.width, (picture.height * 8) / 5)).toBeGreaterThanOrEqual(290);

        await page.locator('.live-panel__aim-view').click();
        await expect(page.locator('.live-panel__aim-view')).toHaveCount(0);
      });
    });
  }
}
