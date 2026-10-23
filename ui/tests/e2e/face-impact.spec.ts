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

async function keepFigure(page: Page, name: string) {
  if (!OUT) return;
  const figure = page.locator('.face-impact__figure');
  await figure.screenshot({ path: join(OUT, `${name}.png`) });
  const original = await figure.getAttribute('style');
  await figure.evaluate((element) => {
    const { width, height } = element.getBoundingClientRect();
    element.setAttribute(
      'style',
      `position: fixed; top: 0; left: 0; z-index: 9999; width: ${width * 4}px; height: ${height * 4}px; background: var(--color-surface)`
    );
  });
  try {
    await figure.screenshot({ path: join(OUT, `${name}-4x.png`) });
  } finally {
    await figure.evaluate(
      (element, style) => (style === null ? element.removeAttribute('style') : element.setAttribute('style', style)),
      original
    );
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
      await expect(view.locator('.face-impact__title .label-badge--experimental')).toBeVisible();
      for (const metric of ['impact_height', 'impact_offset']) {
        await expect(view.locator(`[data-view-metric="${metric}"] .label-badge--experimental`)).toBeVisible();
      }
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

async function inFace(page: Page): Promise<{ origin: boolean; dot: boolean }> {
  return page.locator('.face-impact').evaluate((view) => {
    const face = view.querySelector('.face-impact__face') as SVGGeometryElement;
    const dot = view.querySelector('.face-impact__dot') as SVGCircleElement;
    const mirror = view.getAttribute('data-handed') === 'left' ? -1 : 1;
    const inside = (x: number, y: number) => face.isPointInFill(new DOMPoint(mirror * x, y));
    return { origin: inside(0, 0), dot: inside(dot.cx.baseVal.value, dot.cy.baseVal.value) };
  });
}

/** The dot's distance from the centre-line crossing on screen, back in mm (+ toe, + up). */
async function dotMm(page: Page): Promise<{ toe: number; up: number; rx: number }> {
  return page.locator('.face-impact').evaluate((view) => {
    const svg = view.querySelector('.face-impact__svg') as SVGSVGElement;
    const dot = view.querySelector('.face-impact__dot') as SVGCircleElement;
    const ellipse = view.querySelector('.face-impact__ellipse') as SVGEllipseElement;
    const lines = [...view.querySelectorAll('.face-impact__centre-line')] as SVGLineElement[];
    const across = lines.find((line) => line.y1.baseVal.value === line.y2.baseVal.value)!;
    const upright = lines.find((line) => line.x1.baseVal.value === line.x2.baseVal.value)!;
    const matrix = svg.getScreenCTM()!;
    const screen = (x: number, y: number) => new DOMPoint(x, y).matrixTransform(matrix);
    const crossing = screen(upright.x1.baseVal.value, across.y1.baseVal.value);
    const centre = screen(dot.cx.baseVal.value, dot.cy.baseVal.value);
    const mirror = view.getAttribute('data-handed') === 'left' ? 1 : -1;
    const rim = screen(ellipse.cx.baseVal.value + ellipse.rx.baseVal.value, ellipse.cy.baseVal.value);
    const ellipseCentre = screen(ellipse.cx.baseVal.value, ellipse.cy.baseVal.value);
    return {
      toe: (mirror * (centre.x - crossing.x)) / matrix.a,
      up: -(centre.y - crossing.y) / matrix.d,
      rx: (rim.x - ellipseCentre.x) / matrix.a,
    };
  });
}

/** The topline highlight, sleeve band and score lines stay on their parts (no stroke poking out). */
async function artefacts(page: Page): Promise<string[]> {
  return page.locator('.face-impact').evaluate((view) => {
    const box = (selector: string) => (view.querySelector(selector) as SVGGraphicsElement | null)?.getBBox();
    const problems: string[] = [];
    const face = box('.face-impact__face')!;
    const within = (inner: DOMRect, outer: DOMRect, slop = 0.05) =>
      inner.x >= outer.x - slop &&
      inner.y >= outer.y - slop &&
      inner.x + inner.width <= outer.x + outer.width + slop &&
      inner.y + inner.height <= outer.y + outer.height + slop;
    for (const edge of view.querySelectorAll('.face-impact__edge'))
      if (!within((edge as SVGGraphicsElement).getBBox(), face)) problems.push('topline highlight off the face');
    const scoring = box('.face-impact__scoring');
    if (scoring && !within(scoring, face)) problems.push('milled area off the face');
    for (const groove of view.querySelectorAll('.face-impact__groove'))
      if (!within((groove as SVGGraphicsElement).getBBox(), face)) problems.push('score line off the face');
    const collar = box('.face-impact__collar');
    const hosel = box('.face-impact__hosel')!;
    if (collar && !within(collar, hosel)) problems.push('sleeve band off the sleeve');
    return problems;
  });
}

const DRAWINGS = [
  ['driver', 'driver'],
  ['3-wood', 'fairway'],
  ['5-hybrid', 'hybrid'],
  ['7-iron', 'iron'],
  ['sw', 'wedge'],
] as const;

for (const viewport of [
  { width: 1024, height: 600 },
  { width: 800, height: 480 },
]) {
  const size = `${viewport.width}x${viewport.height}`;
  test.describe(`face impact drawings at ${size}`, () => {
    test.use({ hasTouch: true, viewport });

    for (const [club, category] of DRAWINGS) {
      test(`draws the ${category} face with the centre strike and an off-centre strike on it`, async ({ page }) => {
        await openClubPanel(page, 2, club);
        await setReadings(page, [
          { offset: 0, height: 0 },
          { offset: 9, height: 4, sigma: [4, 5] },
        ]);
        const view = page.locator('.face-impact');
        await expect(view).toHaveAttribute('data-face', category);
        await expect(view.locator('.face-impact__head')).toHaveCount(1);
        await expect(view.locator('.face-impact__dot')).toHaveCount(1);
        await expect(view.locator('.face-impact__earlier')).toHaveCount(1);
        expect(await inFace(page)).toEqual({ origin: true, dot: true });
        const mm = await dotMm(page);
        expect(Math.abs(mm.toe - 9)).toBeLessThanOrEqual(0.1);
        expect(Math.abs(mm.up - 4)).toBeLessThanOrEqual(0.1);
        expect(Math.abs(mm.rx - 4)).toBeLessThanOrEqual(0.1);
        await expect(view.locator('.face-impact__centre-line')).toHaveCount(2);
        await expect(view).not.toContainText('grid');
        expect(await overflowing(page)).toEqual([]);
        expect(await artefacts(page)).toEqual([]);
        await keep(page, `face-impact-${category}-${size}`);
        await keepFigure(page, `face-impact-${category}-figure-${size}`);
      });
    }

    for (const [club, category] of DRAWINGS) {
      test(`mirrors the ${category} face for a left-handed golfer`, async ({ page }) => {
        await openClubPanel(page, 1, club);
        await setReadings(page, [{ offset: 12, height: 6, handedness: 'left' }]);
        const view = page.locator('.face-impact');
        await expect(view).toHaveAttribute('data-face', category);
        await expect(view).toHaveAttribute('data-handed', 'left');
        await expect(view.locator('.face-impact__outline-group')).toHaveAttribute('transform', 'scale(-1 1)');
        expect(await inFace(page)).toEqual({ origin: true, dot: true });
        const mm = await dotMm(page);
        expect(Math.abs(mm.toe - 12)).toBeLessThanOrEqual(0.1);
        expect(Math.abs(mm.up - 6)).toBeLessThanOrEqual(0.1);
        expect(await overflowing(page)).toEqual([]);
        expect(await artefacts(page)).toEqual([]);
        await keep(page, `face-impact-${category}-left-${size}`);
        await keepFigure(page, `face-impact-${category}-left-figure-${size}`);
      });
    }
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
});
