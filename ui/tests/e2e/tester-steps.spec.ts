import { expect, test, type Page, type Route } from '@playwright/test';
import { DEFAULT_TESTER, lensState, mockConfirmedPlacementBox, mockLens, mockTesters, useTester } from './helpers';

// 13 Oct UX pass: one step sequence with a sticky “You are here”, the tester ID and the
// lens picked by touch (no typing anywhere in setup), per-lens settings, and 44 px targets.
test.use({ hasTouch: true });

async function json(route: Route, payload: object, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(payload) });
}

function eligibility(testerId: string, eligible = true) {
  return {
    schema_version: 1,
    tester_id: testerId,
    config_hash: 'measured-v3-hash',
    eligible,
    stage: 'tester_admission',
    checks: [],
    blockers: eligible ? [] : [{ id: 'operator_physical_setup', reason: 'not confirmed', remedy: 'confirm' }],
    operator_confirmation: {
      confirmed: eligible,
      confirmed_at: eligible ? '2026-10-13T08:00:00Z' : null,
      config_hash: 'measured-v3-hash',
      authority: 'operator_physical_setup',
    },
    runtime_requirements: ['ops', 'camera', 'iwr6843', 'lis3dh'],
  };
}

async function mockBase(page: Page, options: { eligible?: boolean } = {}) {
  await mockConfirmedPlacementBox(page);
  await page.route('**/api/tester/setup-eligibility?**', (route) =>
    json(route, eligibility(new URL(route.request().url()).searchParams.get('tester_id') ?? '', options.eligible ?? true))
  );
  await page.route('**/api/tester/tee-range?**', (route) => json(route, { state: null }));
  await page.route('**/api/tester/status**', (route) => json(route, {}));
  await page.route('**/api/tester/attempts?**', (route) => json(route, { schema_version: 1, scopes: [] }));
  await page.route('**/api/tester/ladder?**', (route) => json(route, { ladder: null, stopped: true }));
  await page.route('**/api/tester/aim-point**', (route) =>
    json(route, {
      tester_id: new URL(route.request().url()).searchParams.get('tester_id'),
      state: 'skipped',
      runs: {},
    })
  );
}

test('the page is the OpenFlight Tester, with one numbered sequence', async ({ page }) => {
  await useTester(page);
  await mockBase(page);
  await page.goto('/tester.html');
  await expect(page).toHaveTitle('OpenFlight Tester');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('OpenFlight Tester');
  await expect(page.locator('section.step h2')).toHaveText([
    '1 Tester and lens',
    '2 Check the setup',
    '3 Ball in the box',
    '4 Aim at your target',
    '5 Check the hardware and light',
    '6 Ball range',
    '7 Free play',
    '8 Review and package',
  ].map((text) => new RegExp(`^${text}`)));
});

test('with no tester chosen, the picker lists the saved IDs and a new one; a tap picks one, no typing', async ({
  page,
}) => {
  await mockBase(page);
  await mockTesters(page, ['harjot-indoor-test-2', DEFAULT_TESTER], 'tester-2026-10-13-1');
  const asked: string[] = [];
  page.on('request', (request) => {
    if (request.url().includes('/api/tester/setup-eligibility?')) asked.push(new URL(request.url()).searchParams.get('tester_id') ?? '');
  });
  await page.goto('/tester.html');

  await expect(page.locator('#tester-current-id')).toHaveText('not chosen');
  await expect(page.locator('#tester-picker')).toBeVisible();
  await expect(page.locator('#setup-summary')).toHaveText('Pick your tester ID in step 1 first.');
  await expect(page.locator('#stepbar-step')).toHaveText('Step 1 · Tester and lens');
  const chips = page.locator('#tester-list').getByRole('button');
  await expect(chips).toHaveText(['harjot-indoor-test-2', DEFAULT_TESTER]);
  for (const chip of await chips.all()) {
    expect((await chip.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
  }
  // no typed field anywhere in the steps (the advanced tools keep their old controls)
  await expect(page.locator('section.step input:not([type=hidden]):not([type=checkbox]):not([type=file])')).toHaveCount(0);

  await page.locator('#tester-new').tap();
  await expect(page.locator('#tester-current-id')).toHaveText('tester-2026-10-13-1');
  await expect(page.locator('#tester-picker')).toBeHidden();
  await expect.poll(() => asked).toContain('tester-2026-10-13-1');
  expect(await page.evaluate(() => localStorage.getItem('openflight-tester-id'))).toBe('tester-2026-10-13-1');

  await page.locator('#tester-change').tap();
  await chips.filter({ hasText: 'harjot-indoor-test-2' }).tap();
  await expect(page.locator('#tester-current-id')).toHaveText('harjot-indoor-test-2');
  await expect(page.locator('#tester-id')).toHaveValue('harjot-indoor-test-2');
});

test('a tester list the server cannot give still offers a new ID by touch', async ({ page }) => {
  await mockBase(page);
  await page.route('**/api/tester/testers**', (route) => json(route, { error: 'boom' }, 500));
  await page.goto('/tester.html');
  await expect(page.locator('#tester-picker-note')).toContainText('The saved tester IDs are unavailable');
  await expect(page.locator('#tester-new')).toHaveText(/^New tester: tester-\d{4}-\d{2}-\d{2}-1$/);
  await page.locator('#tester-new').tap();
  await expect(page.locator('#tester-current-id')).toHaveText(/^tester-\d{4}-\d{2}-\d{2}-1$/);
});

test('where is two buttons, sent with the hardware check', async ({ page }) => {
  await useTester(page);
  await mockBase(page);
  const bodies: Record<string, unknown>[] = [];
  await page.route('**/api/tester/run', (route) => {
    bodies.push(route.request().postDataJSON());
    return json(route, { job: { state: 'complete', message: 'ok', output: [] } });
  });
  await page.goto('/tester.html');
  await expect(page.getByRole('button', { name: 'Indoors' })).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: 'Outdoors' }).tap();
  await expect(page.getByRole('button', { name: 'Outdoors' })).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: 'Check the hardware' }).tap();
  await expect.poll(() => bodies[0]?.environment).toBe('outdoors');
});

test('the lens is picked by touch, its settings shown, and a change warns before the box, aim and tilt reset', async ({
  page,
}) => {
  await useTester(page);
  await mockBase(page);
  let state = lensState(DEFAULT_TESTER, 'stock-2.8mm');
  const posts: Record<string, unknown>[] = [];
  await page.route('**/api/tester/lens**', (route) => {
    if (route.request().method() === 'GET') return json(route, state);
    const body = route.request().postDataJSON() as Record<string, unknown>;
    posts.push(body);
    if (body.action === 'select') {
      state = { ...state, lens_id: body.lens_id as string };
      return json(route, { ...state, reset: ['placement_box', 'aim_point', 'camera_tilt'] });
    }
    state = {
      ...state,
      lenses: state.lenses.map((lens) =>
        lens.lens_id === body.lens_id ? { ...lens, settings: body.settings as typeof lens.settings } : lens
      ),
    };
    return json(route, state);
  });
  await page.goto('/tester.html');

  const cards = page.locator('#lens-list .lens-card');
  await expect(cards).toHaveCount(3);
  await expect(cards.nth(0)).toContainText('Stock 2.8 mm');
  await expect(cards.nth(0)).toContainText('f/2.2 · 118° wide · nominal model');
  await expect(cards.nth(0)).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#lens')).toHaveAttribute('data-ready', 'true');
  await expect(page.locator('#lens-settings')).toContainText('Modes: 1280×800 (default), 640×400');
  await expect(page.locator('#ladder-source')).toContainText('The settings the Stock 2.8 mm lens runs');
  // the backend's own numbers, shown as they come: the page computes none of them
  await expect(page.locator('#lens-settings')).toContainText(
    'Focal length 933 px at 1280×800, 467 px at 640×400 (nominal)'
  );
  await expect(cards.nth(1)).toContainText('f/2 · 35.5° wide · nominal model');
  await expect(cards.nth(2)).toContainText('27° wide · nominal model');
  await expect(page.locator('#lens-settings')).toContainText('1280×800 ladder: 300, 200, 150, 100, 75, 50, 30, 20, 10 µs');
  for (const card of await cards.all()) {
    expect((await card.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
  }

  // a change warns first, and nothing is sent until the tester accepts it
  await cards.nth(1).tap();
  const warn = page.locator('#lens-confirm');
  await expect(warn).toBeVisible();
  await expect(warn).toContainText('Change to the 6 mm lens? The ball box, the aim point and the camera tilt will be set again');
  expect(posts).toEqual([]);
  await page.getByRole('button', { name: 'Keep the current lens' }).tap();
  await expect(warn).toBeHidden();
  await cards.nth(1).tap();
  await page.getByRole('button', { name: 'Change lens' }).tap();
  await expect.poll(() => posts).toEqual([{ tester_id: DEFAULT_TESTER, action: 'select', lens_id: '6mm' }]);
  await expect(page.locator('#lens-status')).toHaveText(
    'Lens set to 6 mm. Set again: the ball box, the aim point, the camera tilt.'
  );
  await expect(cards.nth(1)).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#lens-settings')).toContainText('Focal length 2000 px at 1280×800, 1000 px at 640×400');
  // the swing step's settings follow the lens
  await expect(page.locator('#ladder-choice input')).toHaveCount(5);
  await expect(page.locator('#ladder-source')).toContainText('The settings the 6 mm lens runs');

  // its settings: modes, default mode and ladder rungs, all by tapping
  await page.getByRole('button', { name: "Change this lens's settings" }).tap();
  const editor = page.locator('#lens-editor');
  await expect(editor).toBeVisible();
  await editor.locator('#lens-modes').getByRole('button', { name: '640×400' }).tap();
  await expect(editor.locator('#lens-default button')).toHaveText(['1280×800']);
  await editor.getByRole('button', { name: '1280×800 at 50 µs' }).tap();
  await editor.getByRole('button', { name: '1280×800 at 150 µs' }).tap();
  await expect(editor.getByRole('button', { name: '1280×800 at 50 µs' })).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: 'Save lens settings' }).tap();
  await expect.poll(() => posts.length).toBe(2);
  expect(posts[1]).toEqual({
    tester_id: DEFAULT_TESTER,
    action: 'settings',
    lens_id: '6mm',
    settings: { modes: ['arm5'], default_arm: 'arm5', ladder: { arm5: [300, 75, 50] } },
  });
  await expect(editor).toBeHidden();
  await expect(page.locator('#lens-settings')).toContainText('1280×800 ladder: 300, 75, 50 µs');
  await expect(page.locator('#ladder-choice input')).toHaveCount(3);
  await expect(page.locator('#ladder-jump button')).toHaveText(['300 µs', '75 µs', '50 µs']);
});

test('the first lens pick needs no warning', async ({ page }) => {
  await useTester(page);
  await mockBase(page);
  const posts: Record<string, unknown>[] = [];
  await page.route('**/api/tester/lens**', (route) => {
    if (route.request().method() === 'GET') return json(route, lensState(DEFAULT_TESTER, null));
    posts.push(route.request().postDataJSON());
    return json(route, { ...lensState(DEFAULT_TESTER, '8mm'), reset: [] });
  });
  await page.goto('/tester.html');
  await expect(page.locator('#lens-status')).toHaveText('Tap the lens fitted to the unit.');
  await expect(page.locator('#stepbar-step')).toHaveText('Step 1 · Tester and lens');
  await page.locator('#lens-list .lens-card').nth(2).tap();
  await expect(page.locator('#lens-confirm')).toBeHidden();
  await expect.poll(() => posts.length).toBe(1);
  await expect(page.locator('#lens-status')).toHaveText('Lens set to 8 mm.');
});

test('the sticky bar says where you are and scrolls to the next open step', async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 480 });
  await useTester(page);
  await mockBase(page);
  await mockLens(page);
  await page.goto('/tester.html');
  // tester, lens, setup, box and aim (skipped) are done: the light is next
  await expect(page.locator('#stepbar-step')).toHaveText('Step 5 · Hardware and light');
  await expect(page.locator('#step-1')).toHaveAttribute('data-state', 'done');
  await expect(page.locator('#step-4')).toHaveAttribute('data-state', 'done');
  await expect(page.locator('#step-5')).toHaveAttribute('data-state', 'current');
  await page.locator('#stepbar-here').tap();
  await expect
    .poll(async () => (await page.locator('#step-5').boundingBox())?.y ?? 9999)
    .toBeLessThan(200);
  const bar = page.locator('#stepbar');
  for (const name of ['Kiosk view', 'Stop all tester activity']) {
    const box = await bar.getByRole('button', { name }).boundingBox();
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
  }
  // the sticky bars stay compact on the 800×480 screen
  expect((await page.locator('#sticky-top').boundingBox())?.height ?? 999).toBeLessThanOrEqual(112);
});

test('the setup notes fold away once the setup is confirmed', async ({ page }) => {
  await useTester(page);
  await mockBase(page, { eligible: false });
  await page.goto('/tester.html');
  await expect(page.locator('#setup-notes')).toHaveAttribute('open', '');
  await page.unroute('**/api/tester/setup-eligibility?**');
  await page.route('**/api/tester/setup-eligibility?**', (route) => json(route, eligibility(DEFAULT_TESTER, true)));
  await expect(page.locator('#setup-gate')).toHaveAttribute('data-ready', 'true', { timeout: 4000 });
  await expect(page.locator('#setup-notes')).not.toHaveAttribute('open', '');
});

for (const viewport of [
  { width: 800, height: 480 },
  { width: 1024, height: 600 },
]) {
  test(`every checkbox is a 44 px touch row at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await useTester(page);
    await mockBase(page, { eligible: false });
    await mockLens(page);
    await page.goto('/tester.html');
    await expect(page.locator('#ladder-choice input')).toHaveCount(14);
    const rows = page.locator('section.step label:has(input[type=checkbox])');
    expect(await rows.count()).toBeGreaterThan(14);
    for (const row of await rows.all()) {
      await row.scrollIntoViewIfNeeded();
      const box = await row.boundingBox();
      expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}
