import { readFileSync } from 'node:fs';
import { test, type Page } from '@playwright/test';
import { expect, gotoApp, KIOSK_VIEWPORTS, resetSession, simulateShot, withControlSocket } from './helpers';

const catalog = JSON.parse(readFileSync(new URL('../../src/data/metricCatalog.json', import.meta.url), 'utf8')) as {
  metrics: Array<{ id: string; group: string }>;
};

/** The kiosk sizes plus a phone: every metric must fit. */
const VIEWPORTS = [...KIOSK_VIEWPORTS, { width: 390, height: 844 }] as const;
const LABELS = ['measured', 'calculated', 'model_geometry', 'estimated', 'experimental', 'not_measured'];
const PANEL_GROUPS: Record<'Club' | 'Flight', string[]> = { Club: ['club'], Flight: ['ball', 'flight'] };

async function dismissPicker(page: Page) {
  await page.getByRole('button', { name: 'Close Select club' }).click();
}

/** Cells whose text spills past the cell, or a page that scrolls sideways. */
async function overflowingCells(page: Page): Promise<string[]> {
  await page.evaluate(() => document.fonts.ready);
  return page.locator('.metric-cell').evaluateAll((cells) => {
    const slop = 2;
    const problems: string[] = [];
    for (const cell of cells) {
      const box = cell.getBoundingClientRect();
      for (const part of cell.querySelectorAll('.metric-cell__label, .metric-cell__value-row, .label-badge')) {
        const rect = part.getBoundingClientRect();
        if (rect.right > box.right + slop || rect.left < box.left - slop) {
          problems.push(`${cell.getAttribute('data-metric')}: ${part.className}`);
        }
      }
    }
    if (document.documentElement.scrollWidth > window.innerWidth + slop) problems.push('page scrolls sideways');
    return problems;
  });
}

/** Live tiles whose title row (label + badge) spills past the tile. */
async function overflowingLiveTitles(page: Page): Promise<string[]> {
  await page.evaluate(() => document.fonts.ready);
  return page.locator('.live-panel__grid .metric-card').evaluateAll((cards) => {
    const slop = 2;
    const problems: string[] = [];
    for (const card of cards) {
      const box = card.getBoundingClientRect();
      for (const part of card.querySelectorAll('.metric-card__label, .label-badge, .metric-card__reason')) {
        const rect = part.getBoundingClientRect();
        if (rect.right > box.right + slop || rect.bottom > box.bottom + slop) {
          problems.push(
            `${part.className} r${Math.round(rect.right)}/${Math.round(box.right)} b${Math.round(rect.bottom)}/${Math.round(box.bottom)}`
          );
        }
      }
    }
    return problems;
  });
}

for (const viewport of VIEWPORTS) {
  test.describe(`metric panels at ${viewport.width}×${viewport.height}`, () => {
    test.use({ hasTouch: true, viewport });

    test.beforeEach(async () => {
      await withControlSocket(async (socket) => {
        await resetSession(socket);
        await simulateShot(socket);
      });
    });

    for (const [tab, groups] of Object.entries(PANEL_GROUPS)) {
      test(`${tab} shows every metric in its groups with a label or a reason`, async ({ page }) => {
        await gotoApp(page);
        await dismissPicker(page);
        await page.getByRole('button', { name: tab, exact: true }).click();

        const expected = catalog.metrics.filter((spec) => groups.includes(spec.group)).map((spec) => spec.id);
        const cells = page.locator('.metric-cell');
        await expect(cells.first()).toBeVisible();
        // every metric once: in its own cell, or named in its group's one Coming later row
        const shown = await cells.evaluateAll((all) =>
          all.flatMap((cell) =>
            cell.getAttribute('data-metric') === 'coming-later'
              ? (cell.getAttribute('data-metrics') ?? '').split(' ')
              : [cell.getAttribute('data-metric')]
          )
        );
        expect([...shown].sort()).toEqual([...expected].sort());
        for (const cell of await cells.all()) {
          const status = await cell.getAttribute('data-status');
          expect(LABELS).toContain(status);
          if (status === 'not_measured') {
            await expect(cell.locator('.metric-cell__reason')).not.toBeEmpty();
          } else {
            await expect(cell.locator(`.label-badge--${status}`)).toBeVisible();
          }
        }
        expect(await overflowingCells(page)).toEqual([]);
      });
    }

    test('live tiles keep their label badges and reasons inside the tile', async ({ page }) => {
      // the live grid is laid out for the kiosk screens; a phone uses the Club and Flight panels
      test.skip(viewport.width < 800, 'live grid is a kiosk layout');
      await gotoApp(page);
      await dismissPicker(page);
      await expect(page.locator('.live-panel__grid .metric-card')).toHaveCount(10);
      await expect(page.locator('.live-panel__grid .label-badge').first()).toBeVisible();
      expect(await overflowingLiveTitles(page)).toEqual([]);
    });
  });
}
