import { test, expect } from '@playwright/test';
import { gotoApp, KIOSK_VIEWPORTS } from './helpers';

for (const size of KIOSK_VIEWPORTS) {
  test.describe(`radar transfer at ${size.width}x${size.height}`, () => {
    test.use({ viewport: size, hasTouch: true });

    test('fits, scrolls by dragging without selecting, and accepts a tap', async ({ page }) => {
      await gotoApp(page);
      await page.getByRole('button', { name: 'Close Select club' }).click();
      await expect(page.getByLabel('Server connected')).toBeVisible();
      await page.evaluate(async () => {
        const storePath = '/src/stores/useSystemStore.ts';
        const servicePath = '/src/services/socketService.ts';
        const { useSystemStore } = await import(storePath);
        const { socketService } = await import(servicePath);
        useSystemStore.getState().setIwr6843Transfer({
          enabled: true,
          requested: 'full',
          effective: 'full',
          available: true,
          reason: null,
          last_fallback: null,
        });
        socketService.setIwr6843Transfer = (mode: 'full' | 'two_round') => {
          const store = useSystemStore.getState();
          store.setIwr6843Transfer({ ...store.iwr6843Transfer, requested: mode, effective: mode });
        };
      });
      await page.getByRole('button', { name: 'Open menu' }).tap();
      const menu = page.locator('.menu-sheet');
      const full = menu.getByRole('button', { name: 'Full (complete data)', exact: true });
      const fast = menu.getByRole('button', { name: 'Fast', exact: true });
      const dimensions = await menu.evaluate((node) => ({
        width: node.clientWidth,
        contentWidth: node.scrollWidth,
      }));
      expect(dimensions.contentWidth).toBeLessThanOrEqual(dimensions.width + 1);
      await fast.scrollIntoViewIfNeeded();
      await fast.tap();
      await expect(fast).toHaveAttribute('aria-pressed', 'true');
      await full.tap();
      await expect(full).toHaveAttribute('aria-pressed', 'true');
      await menu.evaluate((node) => {
        node.scrollTop = 0;
      });
      const overflow = await menu.evaluate((node) => node.scrollHeight > node.clientHeight);
      if (overflow) {
        const button = menu.getByRole('button', { name: 'Dark', exact: true });
        const box = (await button.boundingBox())!;
        await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
        await page.mouse.down();
        await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2 - 75, { steps: 10 });
        await page.mouse.up();
        expect(await menu.evaluate((node) => node.scrollTop)).toBeGreaterThan(30);
        await expect(full).toHaveAttribute('aria-pressed', 'true');
      }
    });
  });
}
