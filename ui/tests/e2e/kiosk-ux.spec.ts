import { test, type Page, type Route } from '@playwright/test';
import { expect, gotoApp, resetSession, simulateShot, withControlSocket } from './helpers';

// 13 Oct UX pass on the kiosk: the Live tab says what left/right is measured from,
// the tab bar shows there is more to scroll to, and the Club tab folds the metrics
// not built yet into one row with tester-facing reasons a tap away.
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

test('the Live tab shows a set target line, signed, with its ±', async ({ page }) => {
  await page.route('**/api/target-line', (route) =>
    json(route, { status: 'set', yaw_deg: 2.14, total_uncertainty_deg: 0.8, source: 'aim_point' })
  );
  await gotoApp(page);
  await dismissPicker(page);
  const line = page.locator('.live-panel__target');
  await expect(line).toHaveText('Target line set +2.1° ±0.8°');
  await expect(line).toHaveAttribute('data-status', 'set');
  // the strip is one line and leaves the tiles inside the screen
  const box = await line.boundingBox();
  expect(box?.height ?? 99).toBeLessThan(24);
  await expect(page.locator('.live-panel__grid .metric-card')).toHaveCount(10);
});

test("the Live tab says left/right is from the unit's aim when no target line is set", async ({ page }) => {
  await page.route('**/api/target-line', (route) =>
    json(route, { status: 'not_set', yaw_deg: null, total_uncertainty_deg: null, source: null })
  );
  await gotoApp(page);
  await dismissPicker(page);
  await expect(page.locator('.live-panel__target')).toHaveText(
    "Target line not set: left/right is from the unit's aim"
  );
});

test('a kiosk server without the route draws no target line', async ({ page }) => {
  await page.route('**/api/target-line', (route) => json(route, { error: 'not found' }, 404));
  await gotoApp(page);
  await dismissPicker(page);
  await expect(page.locator('.live-panel__grid .metric-card').first()).toBeVisible();
  await expect(page.locator('.live-panel__target')).toHaveCount(0);
});

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

test('the Club tab folds the metrics not built yet into one Coming later row; a tap lists why', async ({ page }) => {
  await gotoApp(page);
  await dismissPicker(page);
  await page.getByRole('button', { name: 'Club', exact: true }).click();
  const later = page.locator('[data-metric="coming-later"]');
  await expect(later).toHaveCount(1);
  await expect(later).toContainText('Coming later');
  await expect(later).toContainText('Impact offset');
  for (const id of ['impact_offset', 'impact_height', 'dynamic_lie']) {
    await expect(page.locator(`[data-metric="${id}"]`)).toHaveCount(0);
  }
  const list = later.locator('.metric-cell__later-list');
  await expect(list).toBeHidden();
  await later.tap();
  await expect(list).toBeVisible();
  await expect(list).toContainText('Impact offset:');
  const box = await later.boundingBox();
  expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
});
