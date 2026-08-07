import { test } from '@playwright/test';
import { expect, gotoApp, simulateShot, withControlSocket } from './helpers';

/**
 * The live view must fit, at any screen height.
 *
 * It is read at a glance between shots, so it deliberately does not scroll —
 * a number you have to scroll to find is a number nobody looks at on a range.
 * That makes "does it fit" a correctness property rather than a cosmetic one,
 * and the only way to check it is to measure at a real viewport size.
 *
 * A single `max-height: 500px` breakpoint used to leave every screen between
 * 501px and ~850px rendering the full-size layout in too little room and
 * clipping it, with `overflow: hidden` and no way to reach what was cut off.
 * 1024x600 lost 245px that way. These are the sizes that band covers: kiosk
 * panels at 600 and 720, laptops at 768, plus the 480 the layout was
 * originally tuned for and a 1080 desktop, so a future change cannot fix the
 * middle by breaking either end.
 */
const VIEWPORT_HEIGHTS = [480, 600, 720, 768, 1080];

test('live view fits without clipping at every screen height', async ({ page }) => {
  await withControlSocket(async (socket) => {
    await simulateShot(socket);
  });

  await gotoApp(page);
  await page.getByRole('button', { name: 'Close club selection' }).click();
  await expect(page.locator('.shot-display')).toBeVisible();

  for (const height of VIEWPORT_HEIGHTS) {
    await page.setViewportSize({ width: 1024, height });

    const overflow = await page
      .locator('.shot-display')
      .evaluate((element) => element.scrollHeight - element.clientHeight);

    // A pixel of tolerance for sub-pixel rounding; the bug this guards was 245.
    expect(overflow, `.shot-display clipped by ${overflow}px at ${height}px tall`).toBeLessThanOrEqual(1);
  }
});

test('metric values scale with the space rather than stepping at a breakpoint', async ({ page }) => {
  await withControlSocket(async (socket) => {
    await simulateShot(socket);
  });

  await gotoApp(page);
  await page.getByRole('button', { name: 'Close club selection' }).click();

  const fontSizeAt = async (height: number) => {
    await page.setViewportSize({ width: 1024, height });
    return page
      .locator('.metric-card__value')
      .first()
      .evaluate((element) => parseFloat(getComputedStyle(element).fontSize));
  };

  const short = await fontSizeAt(480);
  const medium = await fontSizeAt(720);
  const tall = await fontSizeAt(1080);

  // Strictly increasing: a breakpoint would give two heights the same size,
  // which is what left the middle band rendering the full layout too big.
  expect(short).toBeLessThan(medium);
  expect(medium).toBeLessThan(tall);

  // The ends are pinned so scaling cannot creep: 1.75rem floor, 5rem ceiling.
  expect(short).toBeCloseTo(28, 0);
  expect(tall).toBeCloseTo(80, 0);
});
