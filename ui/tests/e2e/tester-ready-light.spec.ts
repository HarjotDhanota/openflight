import { expect, test, type Page, type Route } from '@playwright/test';
import { mockConfirmedPlacementBox, useTester } from './helpers';

test.use({ hasTouch: true });

test.beforeEach(async ({ page }) => {
  await useTester(page);
});

// P7-14 (D13): the tester page's ready light, relayed by the tester server.

type Light = Record<string, unknown>;

const GREEN: Light = {
  schema_version: 1,
  state: 'green',
  word: 'SWING',
  cause: 'Ready for a swing',
  time_left_s: null,
  causes: [],
  since: 100,
  checked_at: 110,
  hold: null,
  swing: null,
  source: 'kiosk',
};

async function fulfillJson(route: Route, payload: object, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(payload) });
}

// the swing step: a ladder walking, so the band relays the kiosk's light
const WALKING = {
  ladder: { current: 'full-300', pending_photo: null, rungs: { 'full-300': { status: 'active', swings: [], reason: null } } },
  last_verdict: null,
  photo_target: null,
  stopped: false,
};

async function mockPage(
  page: Page,
  readLight: () => { status?: number; body: object },
  ladder: object = WALKING
) {
  await mockConfirmedPlacementBox(page);
  await page.route('**/api/tester/tee-range?**', (route) =>
    fulfillJson(route, {
      state: {
        epoch_id: 'fixture-range',
        phase: 'raw_only',
        reason: 'qualification_artifact_missing',
        evidence: {},
        solution: { status: 'unresolved', selected_range_m: null },
      },
    })
  );
  await page.route('**/api/tester/setup-eligibility?**', (route) =>
    fulfillJson(route, { schema_version: 1, eligible: false, checks: [], blockers: [] })
  );
  await page.route('**/api/tester/status**', (route) => fulfillJson(route, {}));
  await page.route('**/api/tester/attempts?**', (route) => fulfillJson(route, { schema_version: 1, scopes: [] }));
  await page.route('**/api/tester/ladder?**', (route) => fulfillJson(route, ladder));
  await page.route('**/api/tester/ready-light**', (route) => {
    const { status = 200, body } = readLight();
    return fulfillJson(route, body, status);
  });
}

test('shows NOT READY as a compact strip while no kiosk is running', async ({ page }) => {
  await mockPage(page, () => ({
    body: { ...GREEN, state: 'red', word: 'NOT READY', cause: 'kiosk not running', source: 'tester' },
  }));
  await page.goto('/tester.html');

  const band = page.locator('#ready-light');
  await expect(band).toHaveAttribute('data-state', 'red');
  await expect(band).toContainText('NOT READY');
  await expect(band).toContainText('kiosk not running');
  await expect(band).toHaveClass(/ready-light--compact/);
});

test('during setup (steps 1-6) the band never says ready, whatever the kiosk says', async ({ page }) => {
  let light: Light = GREEN;
  await mockPage(page, () => ({ body: light }), { ladder: null, stopped: true });
  await page.goto('/tester.html');

  const band = page.locator('#ready-light');
  await expect(band).toHaveAttribute('data-state', 'setup');
  await expect(band).toContainText('SETTING UP');
  await expect(band).toContainText('Step 2 · Check the setup');
  await expect(band).not.toContainText('SWING');
  await expect(band).toHaveClass(/ready-light--compact/);
  // a swing the kiosk hears during setup neither flashes nor shows
  light = {
    ...GREEN,
    swing: { id: 9, at: 109.5, source: 'edge', result: { kind: 'shot', text: '120.0 mph', ball_speed_mph: 120 } },
  };
  await page.waitForTimeout(900);
  await expect(band).toHaveAttribute('data-state', 'setup');
  await expect(band).not.toContainText('Last swing');
  await expect(page.locator('#ready-light-flash')).not.toHaveClass(/ready-light__flash--on/);
});

test('walks green, amber with its time left, and red with the cause', async ({ page }) => {
  let light: Light = GREEN;
  await mockPage(page, () => ({ body: light }));
  await page.goto('/tester.html');

  const band = page.locator('#ready-light');
  await expect(band).toHaveAttribute('data-state', 'green');
  await expect(band).toContainText('SWING');
  await expect(band).not.toHaveClass(/ready-light--compact/);

  light = { ...GREEN, state: 'amber', word: 'WAIT', cause: 'IWR6843 dumping', time_left_s: 4.2 };
  await expect(band).toHaveAttribute('data-state', 'amber');
  await expect(band).toContainText('WAIT');
  await expect(band).toContainText('IWR6843 dumping (about 5 s)');

  light = { ...GREEN, state: 'red', word: 'NOT READY', cause: 'ladder light check running' };
  await expect(band).toHaveAttribute('data-state', 'red');
  await expect(band).toContainText('ladder light check running');
});

test('flashes for a swing and then shows its result', async ({ page }) => {
  let light: Light = GREEN;
  await mockPage(page, () => ({ body: light }));
  await page.goto('/tester.html');
  const band = page.locator('#ready-light');
  await expect(band).toHaveAttribute('data-state', 'green');

  light = {
    ...GREEN,
    state: 'amber',
    word: 'WAIT',
    cause: 'OPS243 dumping',
    swing: { id: 7, at: 109.5, source: 'edge', result: null },
  };
  await expect(page.locator('#ready-light-flash')).toHaveClass(/ready-light__flash--on/);
  await expect(band).toContainText('Swing picked up');

  light = {
    ...light,
    swing: { id: 7, at: 109.5, source: 'edge', result: { kind: 'shot', text: '141.3 mph', ball_speed_mph: 141.3 } },
  };
  await expect(band).toContainText('Last swing: 141.3 mph');
});

test('an old swing on first load does not flash', async ({ page }) => {
  await mockPage(page, () => ({
    body: { ...GREEN, swing: { id: 3, at: 50, source: 'edge', result: { kind: 'not_a_shot', text: 'Not a shot' } } },
  }));
  await page.goto('/tester.html');

  await expect(page.locator('#ready-light')).toContainText('Last swing: Not a shot');
  await expect(page.locator('#ready-light-flash')).not.toHaveClass(/ready-light__flash--on/);
});

test('a tester server that does not answer is NOT READY', async ({ page }) => {
  await mockPage(page, () => ({ status: 500, body: { error: 'boom' } }));
  await page.goto('/tester.html');

  await expect(page.locator('#ready-light')).toHaveAttribute('data-state', 'red');
  await expect(page.locator('#ready-light')).toContainText('tester server not answering');
});

test('stays pinned while scrolling and never takes a tap', async ({ page }) => {
  await mockPage(page, () => ({ body: GREEN }));
  await page.setViewportSize({ width: 390, height: 700 });
  await page.goto('/tester.html');
  const band = page.locator('#ready-light');
  await expect(band).toHaveAttribute('data-state', 'green');

  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  const box = await band.boundingBox();
  expect(box?.y).toBe(0);
  expect(await band.evaluate((node) => getComputedStyle(node).pointerEvents)).toBe('none');
});
