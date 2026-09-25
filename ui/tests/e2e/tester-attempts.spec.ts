import { expect, test, type Page, type Route } from '@playwright/test';
import { KIOSK_VIEWPORTS } from './helpers';

test.use({ hasTouch: true });

type Scope = { tester_id: string; arm_id: string; run: string; run_dir: string; rung_id?: string };
type Entry = { entry_id: string; kind: string; operator_missed: boolean; status: string };

const testerId = '20260922-name';
const runOne: Scope = {
  tester_id: testerId,
  arm_id: 'arm5',
  run: 'run-01',
  run_dir: '/sessions/tester/arm5/run-01',
  rung_id: 'full-300',
};
const runTwo: Scope = {
  tester_id: testerId,
  arm_id: 'arm6',
  run: 'run-02',
  run_dir: '/sessions/tester/arm6/run-02',
  rung_id: 'half-300',
};

function attemptState(scope: Scope, entries: Entry[] = [], sensorShots = 0) {
  const active = entries.filter((entry) => entry.status !== 'void');
  const swings = active.filter((entry) => entry.kind === 'swing');
  const misses = swings.filter((entry) => entry.operator_missed).length;
  return {
    schema_version: 1,
    scope: { tester_id: scope.tester_id, arm_id: scope.arm_id, run: scope.run },
    entries,
    audit: [],
    counts: {
      physical_operator_swings: swings.length,
      operator_reported_misses: misses,
      warmups: active.filter((entry) => entry.kind === 'warmup').length,
      false_triggers: active.filter((entry) => entry.kind === 'false_trigger').length,
      logged_sensor_shots: sensorShots,
    },
    reconciliation: {
      status: swings.length === sensorShots ? 'count_match' : 'count_mismatch',
      difference: swings.length - sensorShots,
      physical_availability: null,
    },
  };
}

async function fulfillJson(route: Route, payload: object, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(payload) });
}

async function mockPage(page: Page, scopes: Scope[], states: Map<string, ReturnType<typeof attemptState>>) {
  await page.route('**/api/tester/setup-eligibility?**', (route) =>
    fulfillJson(route, {
      schema_version: 1,
      tester_id: testerId,
      config_hash: 'measured-v3-hash',
      eligible: true,
      stage: 'tester_admission',
      checks: [],
      blockers: [],
      operator_confirmation: {
        confirmed: true,
        confirmed_at: '2026-09-24T00:00:00Z',
        config_hash: 'measured-v3-hash',
        authority: 'operator_physical_setup',
      },
      runtime_requirements: ['ops', 'camera', 'iwr6843', 'lis3dh'],
    })
  );
  await page.route('**/api/tester/status**', (route) => fulfillJson(route, {}));
  await page.route('**/api/camera/preview.jpg**', (route) => route.fulfill({ status: 204 }));
  await page.route('**/api/tester/ladder?**', (route) =>
    fulfillJson(route, {
      ladder: null,
      stopped: true,
      capture_scope: scopes[0] || null,
      saved_attempt_scopes: scopes,
    })
  );
  await page.route('**/api/tester/attempts?**', (route) => {
    const url = new URL(route.request().url());
    const runDir = url.searchParams.get('run_dir');
    if (!runDir) return fulfillJson(route, { schema_version: 1, scopes });
    const state = states.get(runDir);
    return state ? fulfillJson(route, state) : fulfillJson(route, { error: 'unknown run' }, 404);
  });
}

test('retries the exact scoped request and undoes through the append-only API', async ({ page }) => {
  const states = new Map([[runOne.run_dir, attemptState(runOne)]]);
  const requests: Record<string, unknown>[] = [];
  let addCalls = 0;
  await mockPage(page, [runOne], states);
  await page.route('**/api/tester/attempts', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    const body = route.request().postDataJSON() as Record<string, unknown>;
    requests.push(body);
    if (body.action === 'add') {
      addCalls += 1;
      if (addCalls === 1) return fulfillJson(route, { error: 'response lost' }, 503);
      const entry = { entry_id: String(body.entry_id), kind: 'swing', operator_missed: false, status: 'active' };
      const state = attemptState(runOne, [entry], 0);
      states.set(runOne.run_dir, state);
      return fulfillJson(route, state, 200);
    }
    expect(body.action).toBe('void');
    expect(body.target_entry_id).toBe(requests[0].entry_id);
    return fulfillJson(route, attemptState(runOne, [], 0), 201);
  });
  await page.goto('/tester.html');

  await expect(page.locator('#attempt-scope')).toContainText('arm5 · run-01');
  await page.getByRole('button', { name: 'Record swing' }).tap();
  await expect(page.getByRole('alert').last()).toContainText('Retry to confirm it');
  await page.reload();
  await expect(page.getByRole('button', { name: 'Retry unresolved entry' })).toBeEnabled();
  await page.getByRole('button', { name: 'Retry unresolved entry' }).tap();
  await expect(page.locator('#attempt-counts')).toContainText('recorded swings 1');
  expect(requests[1]).toEqual(requests[0]);
  expect(requests[0]).toMatchObject({
    tester_id: testerId,
    arm_id: 'arm5',
    run_dir: runOne.run_dir,
    action: 'add',
    kind: 'swing',
    operator_missed: false,
  });
  expect(requests[0]).not.toHaveProperty('rung_id');

  await page.getByRole('button', { name: 'Undo last' }).tap();
  await expect(page.locator('#attempt-counts')).toContainText('recorded swings 0');
  expect(requests).toHaveLength(3);
});

test('a delayed response cannot move an entry or counters to a newly selected run', async ({ page }) => {
  const states = new Map([
    [runOne.run_dir, attemptState(runOne)],
    [runTwo.run_dir, attemptState(runTwo, [], 3)],
  ]);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const bodies: Record<string, unknown>[] = [];
  await mockPage(page, [runOne, runTwo], states);
  await page.route('**/api/tester/attempts', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    const body = route.request().postDataJSON() as Record<string, unknown>;
    bodies.push(body);
    if (body.run_dir === runOne.run_dir) await gate;
    const scope = body.run_dir === runOne.run_dir ? runOne : runTwo;
    const entry = {
      entry_id: String(body.entry_id),
      kind: 'swing',
      operator_missed: Boolean(body.operator_missed),
      status: 'active',
    };
    await fulfillJson(route, attemptState(scope, [entry], scope === runTwo ? 3 : 0), 201);
  });
  await page.goto('/tester.html');
  await expect(page.locator('#attempt-scope')).toContainText('run-01');

  await page.getByRole('button', { name: 'Record swing' }).tap();
  await page.locator('#attempt-run').selectOption({ label: 'arm6 · run-02' });
  await expect(page.locator('#attempt-counts')).toContainText('logged sensor shots 3');
  release();
  await page.waitForTimeout(100);
  await expect(page.locator('#attempt-scope')).toContainText('run-02');
  await expect(page.locator('#attempt-counts')).toContainText('recorded swings 0');
  expect(bodies[0].run_dir).toBe(runOne.run_dir);
});

test('same-run ladder advancement does not infer a rung for a manual swing', async ({ page }) => {
  const scope = { ...runOne };
  const states = new Map([[scope.run_dir, attemptState(scope)]]);
  let posted: Record<string, unknown> | undefined;
  await mockPage(page, [scope], states);
  await page.route('**/api/tester/attempts', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    posted = route.request().postDataJSON() as Record<string, unknown>;
    const entry = { entry_id: String(posted.entry_id), kind: 'swing', operator_missed: false, status: 'active' };
    await fulfillJson(route, attemptState(scope, [entry]), 201);
  });
  await page.goto('/tester.html');
  await expect(page.locator('#attempt-scope')).toHaveText('arm5 · run-01');
  await expect(page.locator('#attempt-reconciliation')).toContainText('physical availability');

  scope.rung_id = 'full-175';
  await page.waitForTimeout(1700);
  await page.getByRole('button', { name: 'Record swing' }).tap();

  expect(posted).toMatchObject({ tester_id: testerId, arm_id: 'arm5', run_dir: scope.run_dir });
  expect(posted).not.toHaveProperty('rung_id');
  await expect(page.locator('#attempt-scope')).toHaveText('arm5 · run-01');
});

test('restores a saved stopped run and its counters after reload', async ({ page }) => {
  const saved = attemptState(
    runOne,
    [
      { entry_id: 's1', kind: 'swing', operator_missed: false, status: 'active' },
      { entry_id: 's2', kind: 'swing', operator_missed: true, status: 'active' },
      { entry_id: 'w1', kind: 'warmup', operator_missed: false, status: 'active' },
    ],
    1
  );
  const states = new Map([[runOne.run_dir, saved]]);
  await mockPage(page, [runOne], states);
  await page.goto('/tester.html');
  await expect(page.locator('#attempt-counts')).toContainText('recorded swings 2');
  await page.reload();

  await expect(page.locator('#attempt-run')).toHaveValue(encodeURIComponent(`${testerId}\narm5\n${runOne.run_dir}`));
  await expect(page.locator('#attempt-counts')).toContainText('reported misses 1');
  await expect(page.locator('#attempt-counts')).toContainText('warm-ups / false triggers 1 / 0');
  await expect(page.locator('#attempt-reconciliation')).toContainText('availability and accuracy are unknown');

  states.set(runOne.run_dir, attemptState(runOne, saved.entries, 4));
  await page.getByRole('button', { name: 'Refresh' }).tap();
  await expect(page.locator('#attempt-counts')).toContainText('logged sensor shots 4');
});

for (const viewport of KIOSK_VIEWPORTS) {
  test(`tally controls remain touchable and horizontally fit at ${viewport.width}x${viewport.height}`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    const states = new Map([[runOne.run_dir, attemptState(runOne)]]);
    await mockPage(page, [runOne], states);
    await page.route('**/api/tester/attempts', async (route) => {
      if (route.request().method() !== 'POST') return route.fallback();
      const body = route.request().postDataJSON();
      const entry = { entry_id: body.entry_id, kind: 'swing', operator_missed: true, status: 'active' };
      await fulfillJson(route, attemptState(runOne, [entry]), 201);
    });
    await page.goto('/tester.html');
    const panel = page.locator('#attempts');
    await panel.scrollIntoViewIfNeeded();
    const badControls = await panel.locator('button, select').evaluateAll((controls) =>
      controls
        .filter((control) => {
          const rect = control.getBoundingClientRect();
          if (rect.width === 0 && rect.height === 0) return false;
          return rect.left < 0 || rect.right > window.innerWidth || rect.width < 44 || rect.height < 44;
        })
        .map((control) => control.id)
    );
    expect(badControls).toEqual([]);
    await page.getByRole('button', { name: 'Record missed shot' }).tap();
    await expect(page.locator('#attempt-counts')).toContainText('reported misses 1');
  });
}
