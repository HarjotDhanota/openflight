import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { test, type Page } from '@playwright/test';
import { expect, gotoApp, KIOSK_VIEWPORTS, resetSession, setClub, simulateShot, withControlSocket } from './helpers';

// The Club panel's face-impact view at the kiosk sizes, on mock shots given impact records
// as the live reading gives them; UX_SHOTS_DIR also keeps the screenshots.
const OUT = process.env.UX_SHOTS_DIR;

interface Reading {
  offset: number | null;
  height: number | null;
  sigma?: [number, number];
  reason?: string;
  handedness?: 'right' | 'left';
}

function record(value: number | null, sigma: number, reason: string) {
  return value === null
    ? {
        value: null,
        unit: 'mm',
        status: 'not_measured',
        source: null,
        range: null,
        reason,
        frame: null,
        note: null,
        normalized: null,
      }
    : {
        value,
        unit: 'mm',
        status: 'experimental',
        source: 'camera head outline and resting ball, read after the shot (experimental)',
        range: [value - sigma, value + sigma],
        reason: null,
        frame: null,
        note: 'not validated yet: compare it with the face spray marks',
        normalized: null,
      };
}

async function setReadings(page: Page, readings: Reading[]) {
  await page.evaluate(
    async (all) => {
      const { useShotStore } = await import('/src/stores/useShotStore.ts');
      const shots = useShotStore.getState().shots;
      all.forEach((reading, index) => {
        const shot = shots[shots.length - all.length + index];
        if (!shot) return;
        useShotStore.getState().updateShot({
          ...shot,
          impact_location: {
            status: reading.offset === null ? 'rejected' : 'ok',
            handedness: reading.handedness ?? 'right',
          },
          metrics: {
            ...shot.metrics,
            impact_offset: reading.offsetRecord,
            impact_height: reading.heightRecord,
          },
        });
      });
    },
    readings.map((reading) => ({
      ...reading,
      offsetRecord: record(reading.offset, reading.sigma?.[0] ?? 4, reading.reason ?? 'not read'),
      heightRecord: record(reading.height, reading.sigma?.[1] ?? 5, reading.reason ?? 'no sole line at contact'),
    }))
  );
}

async function openClubPanel(page: Page, shots: number, club = '7-iron') {
  await withControlSocket(async (socket) => {
    await resetSession(socket);
    await setClub(socket, club);
    for (let index = 0; index < shots; index += 1) await simulateShot(socket);
  });
  await gotoApp(page);
  await page.getByRole('button', { name: 'Close Select club' }).click();
  await expect
    .poll(() =>
      page.evaluate(async () => (await import('/src/stores/useShotStore.ts')).useShotStore.getState().shots.length)
    )
    .toBe(shots);
  await page.getByRole('button', { name: 'Club', exact: true }).click();
}

async function overflowing(page: Page): Promise<string[]> {
  await page.evaluate(() => document.fonts.ready);
  return page.locator('.face-impact').evaluate((view) => {
    const slop = 2;
    const box = view.getBoundingClientRect();
    const problems: string[] = [];
    for (const part of view.querySelectorAll('.face-impact__svg, .face-impact__title, dt, dd, p, .label-badge')) {
      const rect = part.getBoundingClientRect();
      if (rect.right > box.right + slop || rect.left < box.left - slop) problems.push(part.className.toString());
    }
    if (box.right > window.innerWidth + slop) problems.push('view wider than the screen');
    if (document.documentElement.scrollWidth > window.innerWidth + slop) problems.push('page scrolls sideways');
    return problems;
  });
}

async function keep(page: Page, name: string) {
  const shot = await page.screenshot();
  await test.info().attach(name, { body: shot, contentType: 'image/png' });
  if (OUT) {
    mkdirSync(OUT, { recursive: true });
    await page.screenshot({ path: join(OUT, `${name}.png`) });
  }
}

for (const viewport of [...KIOSK_VIEWPORTS, { width: 390, height: 844 }]) {
  test.describe(`face impact at ${viewport.width}×${viewport.height}`, () => {
    test.use({ hasTouch: true, viewport });

    test('shows the shot on the club face with its EXP badge, numbers and earlier shots', async ({ page }) => {
      await openClubPanel(page, 3);
      await setReadings(page, [
        { offset: -6, height: 4 },
        { offset: 3, height: -5 },
        { offset: 9, height: 2, sigma: [4, 6] },
      ]);
      const view = page.locator('.face-impact');
      await expect(view).toBeVisible();
      await expect(view).toHaveAttribute('data-face', 'iron');
      await expect(view.locator('.label-badge--experimental')).toBeVisible();
      await expect(view.locator('.face-impact__dot')).toHaveCount(1);
      await expect(view.locator('.face-impact__ellipse')).toHaveCount(1);
      await expect(view.locator('.face-impact__earlier')).toHaveCount(2);
      await expect(view).toContainText('Toe 9 mm');
      await expect(view).toContainText('High 2 mm');
      const figure = await view.locator('.face-impact__svg').boundingBox();
      expect(figure!.height).toBeGreaterThan(60);
      expect(figure!.y + figure!.height).toBeLessThanOrEqual(viewport.height);
      expect(await overflowing(page)).toEqual([]);
      await keep(page, `face-impact-${viewport.width}x${viewport.height}`);
    });
  });
}

test.describe('face impact states', () => {
  test.use({ hasTouch: true, viewport: { width: 1024, height: 600 } });

  test('names why a shot was not read and draws no dot', async ({ page }) => {
    await openClubPanel(page, 1);
    const reason = 'building the head outline: 3 of 5 swings with this club';
    await setReadings(page, [{ offset: null, height: null, reason }]);
    const view = page.locator('.face-impact');
    await expect(view).toContainText(`Not measured: ${reason}`);
    await expect(view.locator('.face-impact__dot')).toHaveCount(0);
    await keep(page, 'face-impact-not-measured-1024x600');
  });

  test('mirrors the driver face for a left-handed golfer', async ({ page }) => {
    await openClubPanel(page, 1, 'driver');
    await setReadings(page, [{ offset: 12, height: 6, handedness: 'left' }]);
    const view = page.locator('.face-impact');
    await expect(view).toHaveAttribute('data-face', 'driver');
    await expect(view).toHaveAttribute('data-handed', 'left');
    await expect(view.locator('.face-impact__outline-group')).toHaveAttribute('transform', 'scale(-1 1)');
    expect(await overflowing(page)).toEqual([]);
    await keep(page, 'face-impact-driver-left-1024x600');
  });
});
