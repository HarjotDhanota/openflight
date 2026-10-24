import { expect, test, type Page, type Route } from '@playwright/test';
import { DEFAULT_TESTER, lensState, placementBoxState, useTester } from './testerHelpers';

// 23 Oct: the page "kept jumping around". Each step is worked out from its own poll, and
// every change of the worked-out step scrolled the page to it: the polls answering one by
// one on load walked it down step by step, and one missed or wobbling poll threw it back
// to step 2 or 3 and down again two seconds later. The page now scrolls by itself only
// once its polls have answered, and only on to a step it has not shown yet.
test.use({ hasTouch: true });

type World = {
  eligible: boolean;
  failSetup: number; // the next N setup polls fail
  refuseSetup: number; // the next N setup polls say the setup is not eligible
  failBox: number; // the next N ball box polls fail
  setupPolls: number;
};

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** The polls answer in a fixed order, as slowly as a busy unit's: the steps open one by one. */
async function mockUnit(page: Page, world: World) {
  const answer = async (route: Route, ms: number, body: object, status = 200) => {
    await delay(ms);
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) }).catch(() => {});
  };
  const tester = (route: Route) => new URL(route.request().url()).searchParams.get('tester_id') ?? DEFAULT_TESTER;
  await page.route('**/api/tester/lens?**', (route) => answer(route, 100, lensState(tester(route))));
  await page.route('**/api/tester/setup-eligibility?**', (route) => {
    world.setupPolls += 1;
    if (world.failSetup > 0) {
      world.failSetup -= 1;
      return answer(route, 400, { error: 'busy' }, 503);
    }
    const refused = world.refuseSetup > 0;
    if (refused) world.refuseSetup -= 1;
    const eligible = world.eligible && !refused;
    return answer(route, 400, {
      schema_version: 1,
      tester_id: tester(route),
      config_hash: 'measured-v3-hash',
      eligible,
      stage: 'tester_admission',
      checks: [],
      blockers: eligible ? [] : [{ id: 'enclosure_tilt', reason: 'tilt out of range', remedy: 'level the unit' }],
      operator_confirmation: {
        confirmed: true,
        confirmed_at: '2026-10-13T08:00:00Z',
        config_hash: 'measured-v3-hash',
        authority: 'operator_physical_setup',
      },
      runtime_requirements: [],
    });
  });
  await page.route('**/api/tester/lis3dh-check', (route) =>
    route.request().method() === 'GET' ? answer(route, 50, { state: 'passed' }) : route.fallback()
  );
  await page.route('**/api/tester/placement-box**', (route) => {
    if (world.failBox > 0) {
      world.failBox -= 1;
      return answer(route, 700, { error: 'busy' }, 503);
    }
    return answer(route, 700, placementBoxState(tester(route)));
  });
  await page.route('**/api/tester/aim-point**', (route) =>
    answer(route, 800, { tester_id: tester(route), state: 'skipped', runs: {} })
  );
  // the light is measured: the ball range (step 6) is next
  await page.route('**/api/tester/status**', (route) =>
    answer(route, 1000, {
      study: { arms: [{ arm_id: 'arm5', attempted: 0, accepted: 0, complete: false, gain_screen: { stale: false, age_s: 40, prompt: null } }] },
    })
  );
  await page.route('**/api/tester/tee-range?**', (route) => answer(route, 1200, { state: null }));
  await page.route('**/api/tester/tee-range', (route) => answer(route, 50, { ok: true }));
  await page.route('**/api/tester/attempts?**', (route) => answer(route, 50, { schema_version: 1, scopes: [] }));
  await page.route('**/api/tester/ladder?**', (route) => answer(route, 50, { ladder: null, stopped: true }));
}

/** Every scroll the page makes by itself, where the current step is drawn, and the layout shifts. */
async function watchJumps(page: Page) {
  await page.addInitScript(() => {
    const w = window as unknown as { __jumps: Record<string, unknown> };
    const jumps = { reveals: [] as string[], scrollY: [] as number[], stepTop: [] as number[], steps: [] as string[], cls: 0 };
    w.__jumps = jumps;
    const scrollIntoView = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (this: Element, ...args: [ScrollIntoViewOptions?]) {
      jumps.reveals.push(this.id || this.tagName);
      return scrollIntoView.apply(this, args);
    };
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries() as unknown as { value: number; hadRecentInput: boolean }[]) {
        if (!entry.hadRecentInput) jumps.cls += entry.value;
      }
    }).observe({ type: 'layout-shift', buffered: true });
    const sample = () => {
      jumps.scrollY.push(window.scrollY);
      const step = document.body?.dataset.watchStep;
      if (step) jumps.stepTop.push(document.getElementById(`step-${step}`)!.getBoundingClientRect().top);
      const now = document.body?.dataset.step;
      if (now && jumps.steps[jumps.steps.length - 1] !== now) jumps.steps.push(now);
      requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  });
}

type Jumps = { reveals: string[]; scrollY: number[]; stepTop: number[]; steps: string[]; cls: number };
const jumps = (page: Page) => page.evaluate(() => (window as unknown as { __jumps: Jumps }).__jumps);
async function restartWatch(page: Page, step: number) {
  await page.evaluate((watched) => {
    const j = (window as unknown as { __jumps: Jumps }).__jumps;
    j.reveals = [];
    j.scrollY = [];
    j.stepTop = [];
    j.steps = [document.body.dataset.step ?? ''];
    j.cls = 0;
    document.body.dataset.watchStep = String(watched);
  }, step);
}
const spread = (values: number[]) => Math.max(...values) - Math.min(...values);

for (const viewport of [
  { width: 1024, height: 600 },
  { width: 800, height: 480 },
]) {
  test(`20 s on a step while the polls run, some missed or wobbling: the page never moves by itself at ${viewport.width}x${viewport.height}`, async ({
    page,
  }) => {
    test.setTimeout(60_000);
    await page.setViewportSize(viewport);
    const world: World = { eligible: true, failSetup: 0, refuseSetup: 0, failBox: 0, setupPolls: 0 };
    await useTester(page);
    await mockUnit(page, world);
    await watchJumps(page);
    await page.goto('/tester.html');
    await expect(page.locator('#stepbar-step')).toHaveText('Step 6 · Ball range');
    await page.waitForTimeout(2500);
    // on load the page went straight to step 6, once
    expect((await jumps(page)).reveals).toEqual(['step-6']);

    await restartWatch(page, 6);
    const startY = await page.evaluate(() => window.scrollY);
    await page.waitForTimeout(3000);
    world.failSetup = 1; // one setup poll times out or the unit is busy
    await page.waitForTimeout(6000);
    world.refuseSetup = 1; // one reading wobbles out of range
    await page.waitForTimeout(5000);
    world.failBox = 2; // the box poll misses twice
    await page.waitForTimeout(6000);

    const seen = await jumps(page);
    // the misses happened, and one of them did throw the worked-out step back to step 2
    expect(world.failSetup + world.refuseSetup + world.failBox).toBe(0);
    expect(seen.steps).toContain('2');
    await expect(page.locator('#stepbar-step')).toHaveText('Step 6 · Ball range');
    // yet nothing scrolled the page: no reveal, and step 6 stayed where it was drawn
    expect(seen.reveals).toEqual([]);
    expect(spread(seen.stepTop)).toBeLessThanOrEqual(1);
    // the scroll position moves only by the browser's scroll anchoring, which holds what is on
    // the screen still while a note above it changes height (it was 1500 px, to step 2 and back)
    expect(Math.max(...seen.scrollY.map((y) => Math.abs(y - startY)))).toBeLessThanOrEqual(40);
    expect(await page.evaluate(() => window.scrollY)).toBe(startY);
    expect(seen.cls).toBeLessThan(0.01);
  });
}

test('a typed ID whose steps are done scrolls the page once, straight to the next open step', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 600 });
  const world: World = { eligible: true, failSetup: 0, refuseSetup: 0, failBox: 0, setupPolls: 0 };
  await mockUnit(page, world);
  await watchJumps(page);
  await page.goto('/tester.html');
  await expect(page.locator('#stepbar-step')).toHaveText('Step 1 · Tester and lens');
  await page.waitForTimeout(500);
  // no tester yet: the page opens at the top and stays there
  expect((await jumps(page)).reveals).toEqual([]);
  expect(await page.evaluate(() => window.scrollY)).toBe(0);

  await page.locator('#tester-entry').tap();
  await page.locator('#tester-entry').fill('tester-2026-10-23-1');
  await page.locator('#tester-use').tap();
  await expect(page.locator('#stepbar-step')).toHaveText('Step 6 · Ball range');
  await page.waitForTimeout(2500);
  const seen = await jumps(page);
  // the polls answered one by one (steps 2, 3, 5, 6 opened in turn), and the page moved once
  expect(seen.steps.length).toBeGreaterThan(2);
  expect(seen.reveals).toEqual(['step-6']);
  const top = (await page.locator('#step-6').boundingBox())?.y ?? -1;
  const bars = (await page.locator('#sticky-top').boundingBox())?.height ?? 0;
  expect(top).toBeGreaterThanOrEqual(bars - 1);
  expect(top).toBeLessThan(bars + 20);
});
