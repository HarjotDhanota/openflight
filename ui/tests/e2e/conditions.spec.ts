import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test } from '@playwright/test';
import { KIOSK_VIEWPORTS } from './helpers';

for (const viewport of KIOSK_VIEWPORTS) {
  test(`Conditions distinguishes assumed air and sensor readings at ${viewport.width}x${viewport.height}`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    await page.goto('/');
    await page.getByRole('button', { name: 'Close Select club' }).click();
    await page.getByRole('button', { name: 'Conditions', exact: true }).click();
    await expect(page.locator('.conditions-panel')).toContainText('Standard air');
    await expect(page.locator('.conditions-panel .metric-card')).toHaveCount(0);
    await page.evaluate(async () => {
      const { useAirStore } = await import('/src/stores/useAirStore.ts');
      useAirStore.getState().setConditions({
        source: 'bme280',
        air_density_kg_m3: 1.1316,
        temperature_c: 31.5,
        pressure_hpa: 1002.4,
        humidity_pct: 38,
        measured_at: 1000,
        age_s: 4,
        reason: null,
        density_altitude_ft: 2685,
      });
    });
    await expect(page.locator('.conditions-panel .metric-card')).toHaveCount(4);
    await expect(page.locator('.conditions-panel')).toContainText('2,685 ft');
    await expect(page.locator('.conditions-panel')).toContainText('88.7');
    const body = page.locator('.conditions-panel__body');
    const overflows = await body.evaluate((element) => element.scrollHeight > element.clientHeight + 2);
    if (overflows) {
      const box = (await body.boundingBox())!;
      await page.mouse.move(box.x + box.width * 0.8, box.y + box.height * 0.8);
      await page.mouse.down();
      await page.mouse.move(box.x + box.width * 0.8, box.y + box.height * 0.2, { steps: 10 });
      await page.mouse.up();
      expect(await body.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
      await body.evaluate((element) => {
        element.scrollTop = 0;
      });
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 2)).toBe(false);
    if (process.env.OPENFLIGHT_SCREENSHOT_DIR) {
      mkdirSync(process.env.OPENFLIGHT_SCREENSHOT_DIR, { recursive: true });
      await page.screenshot({
        path: resolve(process.env.OPENFLIGHT_SCREENSHOT_DIR, `conditions-${viewport.width}x${viewport.height}.png`),
      });
    }
  });
}
