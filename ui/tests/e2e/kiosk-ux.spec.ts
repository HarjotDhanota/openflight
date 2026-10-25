import { test, type Page, type Route } from '@playwright/test';
import { expect, gotoApp, resetSession, simulateShot, withControlSocket } from './helpers';

// Kiosk UX: the horizontal frame strip, the scrollable tab bar, and the folded pending metrics.
test.use({ hasTouch: true, viewport: { width: 800, height: 480 } });

async function json(route: Route, payload: object, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(payload) });
}

async function dismissPicker(page: Page) {
  await page.getByRole('button', { name: 'Close Select club' }).click();
}

test.beforeEach(async () => {
  await withControlSocket(async (socket) => {
    await resetSession(socket);
    await simulateShot(socket);
  });
});

const TARGET_LINES = [
  ['set', 200, { status: 'set', yaw_deg: 2.14, total_uncertainty_deg: 0.8, source: 'aim_point' }],
  ['not set', 200, { status: 'not_set', yaw_deg: null, total_uncertainty_deg: null, source: null }],
  ['missing', 404, { error: 'not found' }],
] as const;

for (const [name, status, payload] of TARGET_LINES) {
  test(`the Live tab's target line when it is ${name}`, async ({ page }) => {
    await page.route('**/api/target-line', (route) => json(route, payload, status));
    await gotoApp(page);
    await dismissPicker(page);
    await expect(page.locator('.live-panel__grid .metric-card')).toHaveCount(10);
    const line = page.locator('.live-panel__target');
    if (name === 'missing') {
      // a kiosk server without the route draws no target line
      await expect(line).toHaveCount(0);
    } else if (name === 'set') {
      await expect(line).toHaveText('Target line set +2.1° ±0.8°');
      await expect(line).toHaveAttribute('data-status', 'set');
      expect((await line.boundingBox())?.height ?? 99).toBeLessThan(24);
    } else {
      await expect(line).toHaveText("Target line not set: left/right is from the unit's aim");
    }
  });
}

test('the tab bar shows there are more tabs and a tap on the hint scrolls to them', async ({ page }) => {
  await gotoApp(page);
  await dismissPicker(page);
  const wrap = page.locator('.panel-footer__tabs-wrap');
  await expect(wrap).toHaveAttribute('data-more-right', 'true');
  const more = page.getByRole('button', { name: 'More tabs' });
  await expect(more).toBeVisible();
  const box = await more.boundingBox();
  expect(box?.width ?? 0).toBeGreaterThanOrEqual(44);
  expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
  for (let tap = 0; tap < 4 && (await more.isVisible()); tap += 1) {
    await more.tap();
    await page.waitForTimeout(400);
  }
  await expect(wrap).toHaveAttribute('data-more-right', 'false');
  await expect(wrap).toHaveAttribute('data-more-left', 'true');
  const tabs = page.locator('.panel-footer__tabs .nav__button');
  const last = tabs.last();
  await expect(last).toBeInViewport({ ratio: 0.95 });
});
