import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { test, type Page } from '@playwright/test';
import { expect, gotoApp, resetSession, setClub, simulateShot, withControlSocket } from './helpers';

const OUT = process.env.UX_SHOTS_DIR;
const CLUBS = ['driver', '3-wood', '5-hybrid', '7-iron', 'sw'];
const VIEWS = ['face', 'top', 'side'];
async function open(page: Page, club = '7-iron', hand = 'right') {
  await withControlSocket(async (socket) => {
    await resetSession(socket);
    await setClub(socket, club);
    await simulateShot(socket);
  });
  await gotoApp(page);
  await page.getByRole('button', { name: 'Close Select club' }).click();
  await page.getByRole('button', { name: 'Club', exact: true }).click();
  await expect(page.locator('.club-views')).toBeVisible();
  await page.evaluate(
    async ({ hand, club }) => {
      const { useShotStore } = await import('/src/stores/useShotStore.ts');
      const { useSystemStore } = await import('/src/stores/useSystemStore.ts');
      useSystemStore.getState().setHanded(hand === 'left' ? 'LH' : 'RH');
      const latest = useShotStore.getState().shots.at(-1)!;
      const loft =
        club === 'driver' ? 13.2 : club === '3-wood' ? 16.5 : club === '5-hybrid' ? 20.1 : club === 'sw' ? 51.3 : 29.4;
      const values = {
        impact_height: 4,
        impact_offset: 9,
        dynamic_lie: null,
        face_angle: -3.17,
        club_path: 4.23,
        face_to_path: -7.4,
        dynamic_loft: loft,
        attack_angle: -4.2,
        spin_loft:
          (Math.acos(
            Math.cos((-4.2 * Math.PI) / 180) * Math.cos((loft * Math.PI) / 180) * Math.cos((-7.4 * Math.PI) / 180) +
              Math.sin((-4.2 * Math.PI) / 180) * Math.sin((loft * Math.PI) / 180)
          ) *
            180) /
          Math.PI,
      };
      const metrics = { ...latest.metrics };
      for (const [key, value] of Object.entries(values))
        metrics[key] = {
          value,
          unit: key.startsWith('impact') ? 'mm' : 'deg',
          status: key === 'dynamic_lie' ? 'not_measured' : key.startsWith('impact') ? 'experimental' : 'measured',
          source: 'browser fixture',
          range: key.startsWith('impact') ? [(value ?? 0) - 3, (value ?? 0) + 3] : null,
          reason: key === 'dynamic_lie' ? 'no camera shaft reading in this layout fixture' : null,
          frame: null,
          note: null,
          normalized: null,
        };
      useShotStore.getState().updateShot({ ...latest, metrics, impact_location: { status: 'ok', handedness: hand } });
    },
    { hand, club }
  );
}

async function fit(page: Page, view: string) {
  return page.locator(`[data-club-view="${view}"]`).evaluate((card) => {
    const issues: string[] = [];
    const box = card.getBoundingClientRect();
    if (box.left < -1 || box.right > innerWidth + 1 || box.bottom > innerHeight - 45) issues.push('card outside kiosk');
    for (const part of card.querySelectorAll('dt, dd, .label-badge, p, h3')) {
      const bounds = part.getBoundingClientRect();
      if (bounds.left < box.left - 1 || bounds.right > box.right + 1 || part.scrollWidth > part.clientWidth + 2)
        issues.push(part.className || part.tagName);
    }
    const projection = card.querySelector('.club-projection') as SVGSVGElement | null;
    if (projection) {
      const area = projection.getBoundingClientRect();
      const outline = projection.querySelector('.club-projection__outline')!.getBoundingClientRect();
      const ball = projection.querySelector('.club-projection__ball')!.getBoundingClientRect();
      const occupiedHeight = Math.max(outline.bottom, ball.bottom) - Math.min(outline.top, ball.top);
      if (occupiedHeight / area.height < 0.5) issues.push('head and ball occupy less than half the drawing height');
      for (const selector of [
        '.club-projection__head',
        '.club-projection__ball',
        '.club-projection__arrow--face',
        '.club-projection__arrow--path',
      ]) {
        const item = projection.querySelector(selector) as SVGGraphicsElement | null;
        if (!item) continue;
        const b = item.getBBox(),
          m = item.getScreenCTM()!;
        for (const [x, y] of [
          [b.x, b.y],
          [b.x + b.width, b.y],
          [b.x, b.y + b.height],
          [b.x + b.width, b.y + b.height],
        ]) {
          const point = new DOMPoint(x, y).matrixTransform(m);
          if (
            point.x < area.left - 1 ||
            point.x > area.right + 1 ||
            point.y < area.top - 1 ||
            point.y > area.bottom + 1
          )
            issues.push(selector);
        }
      }
    }
    if (document.documentElement.scrollWidth > innerWidth) issues.push('page scrolls sideways');
    return issues;
  });
}

async function auditFace(page: Page) {
  return page.locator('.face-impact__svg').evaluate((svg) => {
    const face = svg.querySelector('.face-impact__face') as SVGPathElement;
    const shaft = svg.querySelector('.face-impact__shaft') as SVGPathElement;
    const dot = svg.querySelector('.face-impact__dot') as SVGCircleElement;
    const axis = (x: number, y: number) => new DOMPoint(x, y).matrixTransform(face.getScreenCTM()!);
    const origin = axis(0, 0),
      right = axis(1, 0),
      down = axis(0, 1);
    const scale = Math.hypot(right.x - origin.x, right.y - origin.y);
    const hit = new DOMPoint(dot.cx.baseVal.value, dot.cy.baseVal.value).matrixTransform(dot.getScreenCTM()!);
    const along = (point: DOMPoint) =>
      ((hit.x - origin.x) * (point.x - origin.x) + (hit.y - origin.y) * (point.y - origin.y)) / scale ** 2;
    const points = shaft
      .getAttribute('d')!
      .match(/-?\d+(?:\.\d+)?/g)!
      .map(Number);
    const bottom = axis((points[0] + points[6]) / 2, (points[1] + points[7]) / 2);
    const tip = axis((points[2] + points[4]) / 2, (points[3] + points[5]) / 2);
    const area = svg.getBoundingClientRect();
    const outside: string[] = [];
    for (const selector of ['.face-impact__face', '.face-impact__head']) {
      const path = svg.querySelector(selector) as SVGPathElement;
      for (let i = 0; i <= 200; i++) {
        const local = path.getPointAtLength((path.getTotalLength() * i) / 200);
        const p = new DOMPoint(local.x, local.y).matrixTransform(path.getScreenCTM()!);
        if (p.x < area.left - 1 || p.x > area.right + 1 || p.y < area.top - 1 || p.y > area.bottom + 1) {
          outside.push(selector);
          break;
        }
      }
    }
    const toe = [...svg.querySelectorAll('.face-impact__side')].find(
      (node) => node.textContent?.toLowerCase() === 'toe'
    ) as SVGTextElement;
    const toeAt = new DOMPoint(toe.x.baseVal[0].value, toe.y.baseVal[0].value).matrixTransform(toe.getScreenCTM()!);
    return {
      heelSide: Math.sign(tip.x - origin.x),
      toeSide: Math.sign(toeAt.x - origin.x),
      offset: -along(right),
      height: -along(down),
      lie: (Math.atan2(-(tip.y - bottom.y), Math.abs(tip.x - bottom.x)) * 180) / Math.PI,
      outside,
    };
  });
}

async function sheet(page: Page, name: string) {
  if (!OUT) return;
  await page.evaluate(() => {
    const holder = document.createElement('div');
    holder.id = 'club-review-sheet';
    holder.style.cssText =
      'position:absolute;top:0;left:0;z-index:9999;display:flex;width:max-content;background:var(--color-surface)';
    for (const card of document.querySelectorAll('.club-view')) {
      const original = card.querySelector('.face-impact__figure, .club-view__figure')!;
      const { width, height } = original.getBoundingClientRect();
      const viewBox = original.querySelector('svg')!.viewBox.baseVal;
      const scale = Math.min(width / viewBox.width, height / viewBox.height) * 4;
      const zoomWidth = viewBox.width * scale,
        zoomHeight = viewBox.height * scale;
      const clone = card.cloneNode(true) as HTMLElement;
      clone.style.cssText = `flex:none;width:${zoomWidth}px;padding:0;border:0`;
      for (const item of clone.querySelectorAll(
        '.face-impact__readout, .club-view__title, .club-view__numbers, .club-view__legend'
      ))
        item.remove();
      const figure = clone.querySelector('.face-impact__figure, .club-view__figure') as HTMLElement;
      figure.style.cssText = `width:${zoomWidth}px;height:${zoomHeight}px`;
      const face = clone.querySelector('.face-impact') as HTMLElement | null;
      if (face) face.style.cssText = 'display:block;padding:0;outline:none';
      holder.append(clone);
    }
    document.body.append(holder);
  });
  try {
    await page.locator('#club-review-sheet').screenshot({ path: join(OUT, `${name}-4x.png`) });
  } finally {
    await page.locator('#club-review-sheet').evaluate((node) => node.remove());
  }
}

for (const viewport of [
  { width: 1024, height: 600 },
  { width: 800, height: 480 },
]) {
  const size = `${viewport.width}x${viewport.height}`;
  test.describe(`club delivery ${size}`, () => {
    test.use({ viewport, hasTouch: true });
    for (const club of CLUBS)
      for (const hand of ['right', 'left']) {
        test(`${club} ${hand}: metrics, geometry and kiosk fit`, async ({ page }) => {
          await open(page, club, hand);
          await expect(page.locator('.panel-header__club')).toContainText(
            hand === 'left' ? 'Left-handed' : 'Right-handed'
          );
          await page.evaluate(() => document.fonts.ready);
          if (OUT) mkdirSync(OUT, { recursive: true });
          for (const [index, view] of VIEWS.entries()) {
            if (viewport.width === 800) await page.locator('.club-views__nav button').nth(index).tap();
            const card = page.locator(`[data-club-view="${view}"]`);
            await expect(card.locator('[data-view-metric]')).toHaveCount(3);
            expect(await fit(page, view)).toEqual([]);
            if (view === 'face') {
              const face = await auditFace(page);
              expect(face.heelSide).toBe(hand === 'right' ? 1 : -1);
              expect(face.toeSide).toBe(hand === 'right' ? -1 : 1);
              expect(face.offset).toBeCloseTo(9, 5);
              expect(face.height).toBeCloseTo(4, 5);
              const staticLie = await page.evaluate(async (club) => {
                const { FACE_SHAPES, faceCategory } = await import('/src/components/panel/faceImpact.ts');
                return FACE_SHAPES[faceCategory(club)].hosel.lieDeg;
              }, club);
              expect(face.lie).toBeCloseTo(staticLie, 1);
              await expect(card.locator('[data-view-metric="dynamic_lie"] dd')).toHaveText('—');
              expect(face.outside).toEqual([]);
            } else {
              const result = await card.locator('.club-projection').evaluate((svg) => {
                const group = svg.querySelector('.club-projection__hand') as SVGGElement;
                const face = svg.querySelector('.club-projection__arrow--face') as SVGLineElement;
                const path = svg.querySelector('.club-projection__arrow--path') as SVGLineElement;
                const matrix = group.getScreenCTM()!;
                const head = svg.querySelector('.club-projection__outline') as SVGPathElement;
                const headMatrix = head.getScreenCTM()!;
                const ball = svg.querySelector('.club-projection__ball')!.getBoundingClientRect();
                const origin = new DOMPoint(0, 0).matrixTransform(matrix);
                const target = new DOMPoint(100, 0).matrixTransform(matrix);
                const sign = Math.sign(target.x - origin.x);
                const angle = (line: SVGLineElement) => {
                  const tip = new DOMPoint(line.x2.baseVal.value, line.y2.baseVal.value).matrixTransform(matrix);
                  const x = tip.x - origin.x,
                    y = tip.y - origin.y;
                  return (
                    (Math.atan2(svg.getAttribute('data-view') === 'top' ? sign * y : -y, sign * x) * 180) / Math.PI
                  );
                };
                return {
                  ballWidthMm: ball.width / Math.hypot(headMatrix.a, headMatrix.b),
                  ballHeightMm: ball.height / Math.hypot(headMatrix.c, headMatrix.d),
                  sign,
                  face: angle(face),
                  path: angle(path),
                  displayedFace: Number(face.getAttribute('data-angle')),
                  displayedPath: Number(path.getAttribute('data-angle')),
                };
              });
              expect(result.sign).toBe(hand === 'right' ? -1 : 1);
              expect(result.ballWidthMm).toBeCloseTo(42.67, 4);
              expect(result.ballHeightMm).toBeCloseTo(42.67, 4);
              expect(result.path).toBeCloseTo(view === 'top' ? 4.23 : -4.2, 5);
              expect(result.face).toBeCloseTo(result.displayedFace, 5);
              expect(result.path).toBeCloseTo(result.displayedPath, 5);
              if (view === 'top') expect(result.face).toBeCloseTo(-3.17, 5);
            }
            if (OUT) await page.screenshot({ path: join(OUT, `${club}-${hand}-${view}-${size}.png`) });
          }
          await sheet(page, `${club}-${hand}-${size}`);
        });
      }
    for (const hand of ['right', 'left']) {
      test(`${hand}: face normals, impact signs and lie extremes agree with shot identity`, async ({ page }) => {
        await open(page, '7-iron', hand);
        for (const [lie, offset, height] of [
          [45, -9, -4],
          [75, 9, 4],
        ]) {
          await page.evaluate(
            async ({ lie, offset, height, hand }) => {
              const { useShotStore } = await import('/src/stores/useShotStore.ts');
              const { useSystemStore } = await import('/src/stores/useSystemStore.ts');
              useSystemStore.getState().setHanded(hand === 'left' ? 'RH' : 'LH');
              const shot = useShotStore.getState().shots.at(-1)!;
              const metrics = { ...shot.metrics };
              for (const [key, value] of Object.entries({
                dynamic_lie: lie,
                impact_offset: offset,
                impact_height: height,
              }))
                metrics[key] = {
                  ...metrics[key],
                  value,
                  status: key === 'dynamic_lie' ? 'model_geometry' : 'experimental',
                  reason: null,
                  note: key === 'dynamic_lie' ? 'Synthetic model-geometry fixture; not a live measurement' : null,
                };
              useShotStore.getState().updateShot({ ...shot, metrics });
            },
            { lie, offset, height, hand }
          );
          await expect(page.locator('.panel-header__club')).toContainText(
            hand === 'left' ? 'Left-handed' : 'Right-handed'
          );
          await expect(page.locator('[data-view-metric="dynamic_lie"] .label-badge')).toHaveAttribute(
            'data-label',
            'model_geometry'
          );
          const face = await auditFace(page);
          expect(face.heelSide).toBe(hand === 'right' ? 1 : -1);
          expect(face.toeSide).toBe(hand === 'right' ? -1 : 1);
          expect(face.offset).toBeCloseTo(offset, 5);
          expect(face.height).toBeCloseTo(height, 5);
          expect(face.lie).toBeCloseTo(
            (Math.atan(Math.tan((lie * Math.PI) / 180) * Math.cos((29.4 * Math.PI) / 180)) * 180) / Math.PI,
            1
          );
          expect(face.outside).toEqual([]);
          if (OUT) {
            const name = `audit-iron-${hand}-lie-${lie}-${size}`;
            await page.screenshot({ path: join(OUT, `${name}.png`) });
            await sheet(page, name);
          }
        }
      });
    }
  });
}

test.describe('club view navigation', () => {
  test.use({ viewport: { width: 800, height: 480 }, hasTouch: true });
  test('mouse swipes select views and touch taps still work', async ({ page }) => {
    await open(page);
    const scroller = page.locator('.club-views__scroller');
    const box = (await scroller.boundingBox())!;
    await page.mouse.move(box.x + box.width - 30, box.y + 60);
    await page.mouse.down();
    await page.mouse.move(box.x + 30, box.y + 60, { steps: 14 });
    await page.mouse.up();
    await expect(page.locator('.club-views__nav button').nth(1)).toHaveAttribute('aria-pressed', 'true');
    expect(await fit(page, 'top')).toEqual([]);
    await page.locator('.club-views__nav button').nth(2).tap();
    expect(await fit(page, 'side')).toEqual([]);
    await page.locator('.club-views__nav button').nth(0).tap();
    expect(await fit(page, 'face')).toEqual([]);
    const touch = await page.context().newCDPSession(page);
    await touch.send('Input.dispatchTouchEvent', {
      type: 'touchStart',
      touchPoints: [{ x: box.x + box.width - 30, y: box.y + 60 }],
    });
    for (let step = 1; step <= 12; step++) {
      await touch.send('Input.dispatchTouchEvent', {
        type: 'touchMove',
        touchPoints: [{ x: box.x + box.width - 30 - ((box.width - 60) * step) / 12, y: box.y + 60 }],
      });
    }
    await touch.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await expect(page.locator('.club-views__nav button').nth(1)).toHaveAttribute('aria-pressed', 'true');
    expect(await fit(page, 'top')).toEqual([]);
    await touch.detach();
  });
  test('missing values leave square clubs, dashes and no arrows', async ({ page }) => {
    await open(page);
    await page.evaluate(async () => {
      const { useShotStore } = await import('/src/stores/useShotStore.ts');
      const latest = useShotStore.getState().shots.at(-1)!;
      const metrics = Object.fromEntries(
        Object.entries(latest.metrics!).map(([key, record]) => [
          key,
          { ...record, value: null, status: 'not_measured' },
        ])
      );
      useShotStore.getState().updateShot({ ...latest, metrics });
    });
    await expect(page.locator('.club-projection__arrow')).toHaveCount(0);
    await expect(page.locator('.club-projection__head').first()).toHaveAttribute('transform', 'rotate(0)');
    await expect(page.locator('.club-projection__head').last()).toHaveAttribute('transform', 'rotate(0)');
    for (let i = 0; i < 3; i++) {
      await page.locator('.club-views__nav button').nth(i).tap();
      await expect(page.locator(`[data-club-view="${VIEWS[i]}"] .club-view__numbers dd`)).toHaveText(['—', '—', '—']);
      expect(await fit(page, VIEWS[i])).toEqual([]);
    }
  });
});
