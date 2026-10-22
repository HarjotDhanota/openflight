import { readFileSync } from 'node:fs';
import { expect, test, type Page, type Route } from '@playwright/test';
import { KIOSK_VIEWPORTS, keepManualLadderOpen, mockConfirmedPlacementBox, useTester, pickTester } from './helpers';

test.use({ hasTouch: true });

test.beforeEach(async ({ page }) => {
  // the manual ladder's controls sit in the closed maintainer section
  await keepManualLadderOpen(page);
  await useTester(page);
});

type PhotoTarget = { capture: string; rung_id: string };

const FULL_RUNGS = [
  'full-300',
  'full-200',
  'full-150',
  'full-100',
  'full-75',
  'full-50',
  'full-30',
  'full-20',
  'full-10',
  // 13 Oct: shorter settings down to the sensor's own minimum
  'full-7',
  'full-5',
];
const HALF_RUNGS = ['half-300', 'half-150', 'half-75', 'half-30', 'half-15', 'half-10', 'half-7', 'half-5', 'half-3'];
const ALL_RUNGS = [...FULL_RUNGS, ...HALF_RUNGS];
const NOT_SELECTED = 'not selected by the tester';

function ladderState(photoTarget: PhotoTarget | null, stopped = false) {
  return {
    ladder: {
      current: photoTarget ? 'half-300' : 'half-175',
      pending_photo: photoTarget,
      rungs: {
        'full-300': { status: 'complete', swings: [], reason: null },
        'half-300': { status: 'active', swings: [], reason: null },
        'full-150': { status: 'pending', swings: [], reason: null },
        'half-150': { status: 'pending', swings: [], reason: null },
      },
    },
    last_verdict: null,
    photo_target: photoTarget,
    stopped,
  };
}

async function fulfillJson(route: Route, payload: object, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(payload) });
}

async function mockBaseApis(page: Page, readLadder: () => object) {
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
    fulfillJson(route, {
      schema_version: 1,
      tester_id: new URL(route.request().url()).searchParams.get('tester_id'),
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
  await page.route('**/api/tester/attempts?**', (route) => fulfillJson(route, { schema_version: 1, scopes: [] }));
  await page.route('**/api/tester/ladder?**', (route) => fulfillJson(route, readLadder()));
}

test('captures the returned target identity once and hides the handoff after success', async ({ page }) => {
  const target = { capture: 'camera-final-17', rung_id: 'full-300' };
  let state = ladderState(target);
  const photoBodies: object[] = [];
  let startRequests = 0;
  let finishPhoto!: () => void;
  const photoGate = new Promise<void>((resolve) => {
    finishPhoto = resolve;
  });

  await mockBaseApis(page, () => state);
  await page.route('**/api/tester/ladder/start', async (route) => {
    startRequests += 1;
    await fulfillJson(route, state);
  });
  await page.route('**/api/tester/ladder/photo', async (route) => {
    photoBodies.push(route.request().postDataJSON());
    await photoGate;
    state = { ...ladderState(null), photo_target: target };
    await fulfillJson(route, {
      photo: 'camera-final-17.jpg',
      skipped: false,
      pending_photo: null,
      photo_target: null,
      stopped: false,
    });
  });
  await page.goto('/tester.html');

  await expect(
    page.getByText(
      '1280×800 is complete. Do not swing. Photograph or skip the face image for 1280×800 · 300 µs (camera-final-17) to carry on with 640×400.'
    )
  ).toBeVisible();
  const capture = page.getByRole('button', { name: 'Photograph face' });
  await capture.tap();
  await capture.tap({ force: true });
  await expect(capture).toBeDisabled();
  const start = page.locator('#step-ladder');
  await expect(start).toBeDisabled();
  await start.evaluate((button: HTMLButtonElement) => button.click());
  expect(photoBodies).toEqual([
    { tester_id: '20260922-name', capture: 'camera-final-17', rung_id: 'full-300', action: 'capture' },
  ]);

  await page.waitForTimeout(1700);
  expect(photoBodies).toHaveLength(1);
  expect(startRequests).toBe(0);
  finishPhoto();
  await expect(page.locator('#photo-handoff')).toBeHidden();
  // (17) the ladder walks on after the photo, so Start stays off until Stop
  await expect(start).toBeDisabled();
  await expect(start).toHaveText('The ladder is running');
});

test('refreshes the face preview independently from the ladder status poll', async ({ page }) => {
  let previews = 0;
  await mockBaseApis(page, () => ladderState({ capture: 'camera-preview', rung_id: 'full-300' }));
  await page.unroute('**/api/camera/preview.jpg**');
  await page.route('**/api/camera/preview.jpg**', async (route) => {
    previews += 1;
    await route.fulfill({ status: 204 });
  });

  await page.goto('/tester.html');

  await expect.poll(() => previews, { timeout: 1400 }).toBeGreaterThanOrEqual(3);
});

test('does not overlap ladder status polls when the network stalls', async ({ page }) => {
  let active = 0;
  let maximumActive = 0;
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await mockBaseApis(page, () => ladderState(null));
  await page.unroute('**/api/tester/ladder?**');
  await page.route('**/api/tester/ladder?**', async (route) => {
    active += 1;
    maximumActive = Math.max(maximumActive, active);
    await gate;
    active -= 1;
    await fulfillJson(route, ladderState(null));
  });

  await page.goto('/tester.html');
  await page.waitForTimeout(3200);

  expect(maximumActive).toBe(1);
  release();
});

test('ignores a delayed status response for the previous tester ID', async ({ page }) => {
  let releaseOld!: () => void;
  const oldGate = new Promise<void>((resolve) => {
    releaseOld = resolve;
  });
  await mockBaseApis(page, () => ladderState(null));
  await page.unroute('**/api/tester/status**');
  await page.route('**/api/tester/status**', async (route) => {
    const testerId = route.request().postDataJSON().tester_id;
    if (testerId === '20260922-name') await oldGate;
    await fulfillJson(route, {
      job: { state: 'complete', message: testerId, output: [] },
    });
  });

  await page.goto('/tester.html');
  await pickTester(page, 'new-tester');
  releaseOld();

  await expect(page.locator('#status')).toHaveText('new-tester: done.');
  await expect(page.locator('#status')).not.toContainText('20260922-name');
});

test('changing tester ID cancels the remaining light-measurement sequence', async ({ page }) => {
  await mockBaseApis(page, () => ladderState(null));
  const runBodies: Record<string, unknown>[] = [];
  await page.route('**/api/tester/run', async (route) => {
    runBodies.push(route.request().postDataJSON());
    await fulfillJson(route, {});
  });
  let statusReads = 0;
  await page.route('**/api/tester/status?**', async (route) => {
    statusReads += 1;
    await fulfillJson(route, {
      job: statusReads === 1 ? { state: 'running', message: 'measuring' } : { state: 'complete', message: 'done' },
    });
  });
  await page.goto('/tester.html');
  await page.getByRole('button', { name: 'Measure the light (both modes)' }).tap();
  await expect.poll(() => statusReads).toBe(1);
  await pickTester(page, 'new-tester');
  await page.waitForTimeout(1200);

  expect(runBodies).toEqual([{ tester_id: '20260922-name', arm_id: 'arm5', environment: 'indoors', action: 'gain' }]);
  await expect(page.locator('#light-verdict')).toContainText('cancelled');
});

const LIT = { lighting_required: false };
for (const [arm5, arm6, verdict, problem] of [
  [LIT, LIT, '1280×800: light sufficient · 640×400: light sufficient', false],
  [
    { lighting_required: true },
    LIT,
    '1280×800: more light needed · 640×400: light sufficient — swings are still recorded, as evidence only',
    true,
  ],
  // wiring audit T1: outdoors the screen's "too bright" never reached the page
  [
    { lighting_required: false, too_bright: true },
    { lighting_required: false, mixed_light: true },
    '1280×800: too bright: shorter exposures used · 640×400: mixed light: gain kept below the sunlit patch',
    false,
  ],
] as const) {
  test(`the light verdict reports each mode's result (${verdict.slice(0, 40)})`, async ({ page }) => {
    await mockBaseApis(page, () => ladderState(null));
    await page.route('**/api/tester/run', (route) => fulfillJson(route, {}));
    await page.route('**/api/tester/status?**', (route) =>
      fulfillJson(route, {
        job: { state: 'complete', message: 'done' },
        study: {
          arms: [
            { arm_id: 'arm5', ...arm5 },
            { arm_id: 'arm6', ...arm6 },
          ],
        },
      })
    );
    await page.goto('/tester.html');
    await page.getByRole('button', { name: 'Measure the light (both modes)' }).tap();

    await expect(page.locator('#light-verdict')).toHaveText(verdict);
    if (problem) await expect(page.locator('#light-verdict')).toHaveClass('problem');
    else await expect(page.locator('#light-verdict')).not.toHaveClass('problem');
  });
}

test('a too-bright arm shows its light panel without a light index', async ({ page }) => {
  await mockBaseApis(page, () => ladderState(null));
  await page.unroute('**/api/tester/status**');
  await page.route('**/api/tester/status**', (route) =>
    fulfillJson(route, {
      study: {
        arms: [
          {
            arm_id: 'arm1',
            label: 'Full 1280×800',
            isolates: 'blur',
            exposure_us: 300,
            gain: 1.0,
            lighting_required: false,
            too_bright: true,
            mixed_light: false,
            gain_at_300_equivalent: 0.45,
            light_index: null,
          },
        ],
      },
    })
  );
  await page.goto('/tester.html');

  await expect(page.locator('#arms')).toContainText('too bright at this exposure');
  await expect(page.locator('#light')).toContainText('light-equivalent gain at 300 µs 0.45');
  await expect(page.locator('#light')).toContainText('the ladder uses its shorter exposures');
  await expect(page.locator('#light')).not.toContainText('light index');
});

for (const [screen, text, problem] of [
  [{ age_s: 12 * 60, stale: false, prompt: null }, 'Light measured 12 min ago.', false],
  [
    {
      age_s: 42 * 60,
      stale: true,
      prompt: 'Measure the light again (B): this screen is 42 min old, and outdoor light changes within half an hour.',
    },
    '1280×800: Measure the light again (B): this screen is 42 min old, and outdoor light changes within half an hour.',
    true,
  ],
] as const) {
  test(`the light step shows how old its screen is (${text.slice(0, 30)})`, async ({ page }) => {
    // wiring audit T14 with decision D5
    await mockBaseApis(page, () => ladderState(null));
    await page.unroute('**/api/tester/status**');
    await page.route('**/api/tester/status**', (route) =>
      fulfillJson(route, {
        study: {
          arms: [
            { arm_id: 'arm5', label: 'Full', isolates: 'blur', exposure_us: 300, gain: 1.0, gain_screen: screen },
            {
              arm_id: 'arm6',
              label: 'Half',
              isolates: 'blur',
              exposure_us: 300,
              gain: 1.0,
              gain_screen: { age_s: 60, stale: false, prompt: null },
            },
          ],
        },
      })
    );
    await page.goto('/tester.html');

    await expect(page.locator('#light-age')).toHaveText(text);
    if (problem) await expect(page.locator('#light-age')).toHaveClass('problem');
    else await expect(page.locator('#light-age')).toHaveClass('note');
  });
}

test('the light step says nothing about age before any screen', async ({ page }) => {
  await mockBaseApis(page, () => ladderState(null));
  await page.goto('/tester.html');
  await expect(page.locator('#light-age')).toBeHidden();
});

test('Stop cancels the remaining light-measurement sequence', async ({ page }) => {
  await mockBaseApis(page, () => ladderState(null));
  const runBodies: Record<string, unknown>[] = [];
  await page.route('**/api/tester/run', async (route) => {
    runBodies.push(route.request().postDataJSON());
    await fulfillJson(route, {});
  });
  let statusReads = 0;
  await page.route('**/api/tester/status?**', async (route) => {
    statusReads += 1;
    await fulfillJson(route, { job: { state: 'running', message: 'measuring' } });
  });
  await page.route('**/api/tester/stop', (route) => fulfillJson(route, {}));
  await page.goto('/tester.html');
  await page.getByRole('button', { name: 'Measure the light (both modes)' }).tap();
  await expect.poll(() => statusReads).toBe(1);
  await page.getByRole('button', { name: 'Stop all tester activity' }).tap();
  await page.waitForTimeout(1200);

  expect(runBodies).toHaveLength(1);
  await expect(page.locator('#light-verdict')).toContainText('cancelled');
});

test('analyse-and-package keeps its original tester context and ignores a duplicate click', async ({ page }) => {
  await mockBaseApis(page, () => ladderState(null));
  let comparatorUploads = 0;
  let releaseUpload!: () => void;
  const uploadGate = new Promise<void>((resolve) => {
    releaseUpload = resolve;
  });
  await page.route('**/api/tester/comparator', async (route) => {
    comparatorUploads += 1;
    await uploadGate;
    await fulfillJson(route, { saved: true });
  });
  const analysisPosts: Record<string, unknown>[] = [];
  const bundle = { name: '20260922-name-session-bundle-20260925T010203Z.zip', size_bytes: 10, sha256: 'c'.repeat(64) };
  await page.route('**/api/tester/analysis', async (route) => {
    analysisPosts.push(route.request().postDataJSON());
    await fulfillJson(
      route,
      {
        job: { state: 'running', action: 'analyze' },
        analysis: { state: 'complete', review_ready: true, latest_bundle: bundle, bundles: [bundle], job: {} },
      },
      202
    );
  });
  await page.goto('/tester.html');
  // 19 Oct: packaging by hand is the maintainer's, folded under step 8
  await page.locator('#advanced-package > summary').click();
  await page.locator('#comparator-file').setInputFiles({
    name: 'comparison.json',
    mimeType: 'application/json',
    buffer: Buffer.from('{}'),
  });

  const packageButton = page.getByRole('button', { name: 'Analyse, review & package' });
  await packageButton.dispatchEvent('click');
  await packageButton.dispatchEvent('click');
  await expect.poll(() => comparatorUploads).toBe(1);
  await pickTester(page, 'new-tester');
  releaseUpload();

  await expect.poll(() => analysisPosts).toHaveLength(1);
  expect(analysisPosts[0]).toEqual({ tester_id: '20260922-name' });
});

test('a finished analysis offers the review and the bundle download', async ({ page }) => {
  const bundle = { name: '20260922-name-session-bundle-20260925T010203Z.zip', size_bytes: 10, sha256: 'c'.repeat(64) };
  await mockBaseApis(page, () => ladderState(null));
  await page.route('**/api/tester/status?**', (route) =>
    fulfillJson(route, {
      job: { state: 'idle', message: 'Ready' },
      analysis: { state: 'complete', review_ready: true, latest_bundle: bundle, bundles: [bundle], job: {} },
    })
  );
  await page.route('**/api/tester/status', (route) =>
    fulfillJson(route, {
      job: { state: 'idle', message: 'Ready' },
      analysis: { state: 'complete', review_ready: true, latest_bundle: bundle, bundles: [bundle], job: {} },
    })
  );
  await page.goto('/tester.html');
  await expect(page.locator('#package-status')).toContainText('Packaged: ready to send');
  await expect(page.getByRole('link', { name: 'Open the session review' })).toHaveAttribute(
    'href',
    '/session-review.html?tester_id=20260922-name'
  );
  await expect(page.getByRole('link', { name: `Download ${bundle.name}` })).toHaveAttribute(
    'href',
    `/api/tester/bundle?tester_id=20260922-name&name=${encodeURIComponent(bundle.name)}`
  );
});

test('keeps a stale-target error visible through polling and retries with the same identity', async ({ page }) => {
  const target = { capture: 'camera-final-18', rung_id: 'full-300' };
  let attempts = 0;
  let state = ladderState(target);

  await mockBaseApis(page, () => state);
  await page.route('**/api/tester/ladder/photo', async (route) => {
    attempts += 1;
    if (attempts === 1) {
      await fulfillJson(route, { error: 'photo target is stale' }, 409);
      return;
    }
    expect(route.request().postDataJSON()).toEqual({
      tester_id: '20260922-name',
      capture: 'camera-final-18',
      rung_id: 'full-300',
      action: 'capture',
    });
    state = ladderState(null);
    await fulfillJson(route, state);
  });
  await page.goto('/tester.html');

  await page.getByRole('button', { name: 'Photograph face' }).tap();
  await expect(page.getByRole('alert')).toContainText('photo target is stale');
  await page.waitForTimeout(1700);
  await expect(page.getByRole('alert')).toContainText('photo target is stale');
  await page.getByRole('button', { name: 'Photograph face' }).tap();

  await expect(page.locator('#photo-handoff')).toBeHidden();
  expect(attempts).toBe(2);
});

test('shows capture alone for an ordinary full-resolution photo target', async ({ page }) => {
  const base = ladderState(null);
  const state = {
    ...base,
    ladder: { ...base.ladder, current: 'full-150' },
    photo_target: { capture: 'camera-swing-4', rung_id: 'full-150' },
  };
  await mockBaseApis(page, () => state);
  await page.goto('/tester.html');

  await expect(page.getByRole('button', { name: 'Photograph face' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Pause ladder' })).toBeHidden();
  await expect(page.getByRole('button', { name: 'Skip photo' })).toBeHidden();
});

test('reloads a stopped handoff, resumes it, and skips the exact target', async ({ page }) => {
  const target = { capture: 'camera-final-19', rung_id: 'full-300' };
  const restoredTesterId = '20260923-reconnect';
  let state = ladderState(target, true);
  let startBody: object | undefined;
  let skipBody: object | undefined;
  const ladderTesterIds: string[] = [];

  await mockBaseApis(page, () => state);
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (request.method() === 'GET' && url.pathname === '/api/tester/ladder') {
      ladderTesterIds.push(url.searchParams.get('tester_id') || '');
    }
  });
  await page.route('**/api/tester/ladder/start', async (route) => {
    startBody = route.request().postDataJSON();
    state = ladderState(target, false);
    await fulfillJson(route, state);
  });
  await page.route('**/api/tester/ladder/photo', async (route) => {
    skipBody = route.request().postDataJSON();
    state = ladderState(null);
    await fulfillJson(route, state);
  });
  await page.goto('/tester.html');
  await pickTester(page, restoredTesterId);
  await page.reload();

  await expect(page.locator('#tester-id')).toHaveValue(restoredTesterId);
  await expect(page.getByText('Photo held for 1280×800 · 300 µs. Resume the ladder to use the camera.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Photograph face' })).toBeDisabled();
  await page.getByRole('button', { name: 'Resume ladder' }).tap();
  await expect(page.getByRole('button', { name: 'Pause ladder' })).toBeVisible();
  await page.getByRole('button', { name: 'Skip photo' }).tap();

  expect(ladderTesterIds).toContain(restoredTesterId);
  expect(startBody).toEqual({
    tester_id: restoredTesterId,
    arm_id: 'arm5',
    environment: 'indoors',
    rungs: ALL_RUNGS,
  });
  expect(skipBody).toEqual({
    tester_id: restoredTesterId,
    capture: 'camera-final-19',
    rung_id: 'full-300',
    action: 'skip',
  });
  await expect(page.locator('#photo-handoff')).toBeHidden();
});

function choiceState(statuses: Record<string, [string, string | null]>, stopped: boolean): Record<string, unknown> {
  const rungs = Object.fromEntries(
    ALL_RUNGS.map((id) => {
      const [status, reason] = statuses[id] || ['pending', null];
      return [id, { status, reason, swings: [] }];
    })
  );
  return {
    ladder: { current: 'full-150', pending_photo: null, rungs },
    last_verdict: null,
    photo_target: null,
    stopped,
  };
}

const setting = (page: Page, size: string, us: number) => page.getByRole('checkbox', { name: `${size} at ${us} µs` });

test('every setting is ticked by default and the ticked ones are sent on Start', async ({ page }) => {
  await mockBaseApis(page, () => choiceState({}, true));
  const startBodies: Record<string, unknown>[] = [];
  await page.route('**/api/tester/ladder/start', async (route) => {
    startBodies.push(route.request().postDataJSON());
    await fulfillJson(route, choiceState({}, false));
  });
  await page.goto('/tester.html');

  await expect(page.locator('#ladder-choice')).toContainText('1280×800');
  await expect(page.locator('#ladder-choice')).toContainText('640×400');
  for (const box of await page.locator('#ladder-choice input').all()) await expect(box).toBeChecked();
  await expect(page.locator('#ladder-choice input')).toHaveCount(20);

  await setting(page, '1280×800', 50).uncheck();
  await setting(page, '640×400', 75).uncheck();
  await page.locator('#step-ladder').tap();

  await expect.poll(() => startBodies.length).toBe(1);
  expect(startBodies[0]).toEqual({
    tester_id: '20260922-name',
    arm_id: 'arm5',
    environment: 'indoors',
    rungs: [
      'full-300',
      'full-200',
      'full-150',
      'full-100',
      'full-75',
      'full-30',
      'full-20',
      'full-10',
      'full-7',
      'full-5',
      'half-300',
      'half-150',
      'half-30',
      'half-15',
      'half-10',
      'half-7',
      'half-5',
      'half-3',
    ],
  });
});

test('settings that already ran are greyed out and the rest can be changed', async ({ page }) => {
  await mockBaseApis(page, () =>
    choiceState(
      {
        'full-300': ['done', null],
        'full-200': ['skipped', 'too bright for the ball: 40% of it is clipped'],
        'full-150': ['failed', '2 of the first 3 swings red'],
        'full-100': ['skipped', NOT_SELECTED],
      },
      true
    )
  );
  await page.goto('/tester.html');

  await expect(setting(page, '1280×800', 300)).toBeDisabled();
  await expect(setting(page, '1280×800', 200)).toBeDisabled();
  await expect(setting(page, '1280×800', 150)).toBeDisabled();
  await expect(setting(page, '1280×800', 100)).toBeEnabled();
  await expect(setting(page, '640×400', 300)).toBeEnabled();
  await expect(page.locator('#ladder-choice-hint')).toBeHidden();
});

test('the settings cannot change while the ladder walks', async ({ page }) => {
  let stopped = false;
  await mockBaseApis(page, () => choiceState({}, stopped));
  await page.goto('/tester.html');

  await expect(page.locator('#ladder-choice-hint')).toHaveText(
    'The ladder is running: press Stop to change the settings.'
  );
  for (const box of await page.locator('#ladder-choice input').all()) await expect(box).toBeDisabled();
  stopped = true;
  await expect(page.locator('#ladder-choice-hint')).toBeHidden();
  await expect(setting(page, '640×400', 150)).toBeEnabled();
});

test('the choice is remembered after a reload and sent when the ladder resumes', async ({ page }) => {
  const target = { capture: 'camera-final-20', rung_id: 'full-300' };
  let state = ladderState(target, true);
  let startBody: Record<string, unknown> | undefined;
  await mockBaseApis(page, () => state);
  await page.route('**/api/tester/ladder/start', async (route) => {
    startBody = route.request().postDataJSON();
    state = ladderState(target, false);
    await fulfillJson(route, state);
  });
  await page.goto('/tester.html');
  await setting(page, '1280×800', 30).uncheck();
  await setting(page, '640×400', 150).uncheck();
  await page.reload();

  await expect(setting(page, '1280×800', 30)).not.toBeChecked();
  await expect(setting(page, '640×400', 150)).not.toBeChecked();
  await expect(setting(page, '640×400', 300)).toBeChecked();
  await page.getByRole('button', { name: 'Resume ladder' }).tap();

  await expect.poll(() => startBody).toBeTruthy();
  expect(startBody?.rungs).toEqual([
    'full-300',
    'full-200',
    'full-150',
    'full-100',
    'full-75',
    'full-50',
    'full-20',
    'full-10',
    'full-7',
    'full-5',
    'half-300',
    'half-75',
    'half-30',
    'half-15',
    'half-10',
    'half-7',
    'half-5',
    'half-3',
  ]);
});

test('the page works when browser storage is unavailable', async ({ page }) => {
  await page.addInitScript(() => {
    const broken = () => {
      throw new Error('storage disabled');
    };
    Object.defineProperty(window, 'localStorage', { get: broken });
  });
  await mockBaseApis(page, () => choiceState({}, true));
  await page.goto('/tester.html');

  await expect(setting(page, '1280×800', 300)).toBeChecked();
  await setting(page, '1280×800', 300).uncheck();
  await expect(setting(page, '1280×800', 300)).not.toBeChecked();
});

test('Start with nothing ticked asks for a setting and is refused', async ({ page }) => {
  await mockBaseApis(page, () => choiceState({}, true));
  let startRequests = 0;
  await page.route('**/api/tester/ladder/start', async (route) => {
    startRequests += 1;
    await fulfillJson(route, { error: 'choose at least one setting' }, 400);
  });
  await page.goto('/tester.html');
  for (const box of await page.locator('#ladder-choice input').all()) await box.uncheck();
  await page.locator('#step-ladder').tap();

  await expect(page.locator('#ladder-panel')).toHaveText('Choose at least one setting.');
  expect(startRequests).toBe(0);
});

test('Start shows why the ladder needs the setup to find the ball', async ({ page }) => {
  // P6-2 (D8): Outdoors-test-5 started a ladder whose setup camera never found the ball
  const refusal =
    "The camera hasn't found the ball, so the ladder can't judge your swings. Run the setup again with the ball " +
    '1.0 to 1.3 m from the lens, on the same surface as the unit (not a raised mat), and nothing ball-like or ' +
    'white in view (spare balls, a cloth).';
  await mockBaseApis(page, () => choiceState({}, true));
  await page.route('**/api/tester/ladder/start', (route) =>
    fulfillJson(route, { error: refusal, setup_ball_missing: true }, 409)
  );
  await page.goto('/tester.html');
  await page.locator('#step-ladder').tap();

  await expect(page.locator('#ladder-panel')).toHaveText(refusal);
});

test('with no 640×400 setting to follow, the last photo finishes the ladder', async ({ page }) => {
  const target = { capture: 'camera-final-21', rung_id: 'full-30' };
  const base = ladderState(target);
  await mockBaseApis(page, () => ({ ...base, ladder: { ...base.ladder, current: null } }));
  await page.goto('/tester.html');

  await expect(
    page.getByText(
      '1280×800 is complete. Do not swing. Photograph or skip the face image for 1280×800 · 30 µs (camera-final-21) to finish the ladder.'
    )
  ).toBeVisible();
});

test('a refused start shows the server message', async ({ page }) => {
  await mockBaseApis(page, () => choiceState({}, true));
  await page.route('**/api/tester/ladder/start', (route) =>
    fulfillJson(route, { error: 'choose at least one setting' }, 400)
  );
  await page.goto('/tester.html');
  await page.locator('#step-ladder').tap();

  await expect(page.locator('#ladder-panel')).toHaveText('choose at least one setting');
});

// P7-13 (D12): "let me switch between them freely. this is separate from the checkbox system."
const settingButton = (page: Page, size: string, us: number) =>
  page.getByRole('button', { name: `Switch to ${size} at ${us} µs` });

function jumpState(
  statuses: Record<string, [string, string | null]>,
  current: string | null,
  stopped: boolean,
  extra: Record<string, Record<string, unknown>> = {}
) {
  const base = choiceState(statuses, stopped) as { ladder: { rungs: Record<string, object> } };
  const rungs = Object.fromEntries(
    Object.entries(base.ladder.rungs).map(([id, rung]) => [id, { ...rung, ...(extra[id] || {}) }])
  );
  return { ...base, ladder: { ...base.ladder, rungs, current } };
}

test('pressing a setting on a running ladder switches to it and sends the ticked order', async ({ page }) => {
  let state = jumpState({ 'full-300': ['active', null] }, 'full-300', false);
  const jumpBodies: Record<string, unknown>[] = [];
  await mockBaseApis(page, () => state);
  await page.route('**/api/tester/ladder/jump', async (route) => {
    jumpBodies.push(route.request().postDataJSON());
    state = jumpState({ 'full-300': ['pending', null], 'half-150': ['active', null] }, 'half-150', false);
    await fulfillJson(route, state);
  });
  await page.goto('/tester.html');

  await expect(page.locator('#ladder-jump button')).toHaveCount(20);
  await expect(settingButton(page, '1280×800', 300)).toHaveClass(/now/);
  await settingButton(page, '640×400', 150).tap();

  await expect.poll(() => jumpBodies.length).toBe(1);
  expect(jumpBodies[0]).toEqual({
    tester_id: '20260922-name',
    arm_id: 'arm5',
    environment: 'indoors',
    rungs: ALL_RUNGS,
    rung_id: 'half-150',
  });
  await expect(settingButton(page, '640×400', 150)).toHaveClass(/now/);
  await expect(settingButton(page, '1280×800', 300)).not.toHaveClass(/now/);
  await expect(page.locator('#ladder-panel')).toContainText('Now: 640×400 · 150 µs');
});

test('a setting button starts a stopped ladder on that setting, ticked or not', async ({ page }) => {
  await mockBaseApis(page, () => jumpState({}, 'full-300', true));
  const jumpBodies: Record<string, unknown>[] = [];
  let startRequests = 0;
  await page.route('**/api/tester/ladder/start', async (route) => {
    startRequests += 1;
    await fulfillJson(route, jumpState({}, 'full-300', false));
  });
  await page.route('**/api/tester/ladder/jump', async (route) => {
    jumpBodies.push(route.request().postDataJSON());
    await fulfillJson(route, jumpState({ 'full-50': ['active', null] }, 'full-50', false));
  });
  await page.goto('/tester.html');
  await setting(page, '1280×800', 50).uncheck();
  await setting(page, '640×400', 75).uncheck();

  await settingButton(page, '1280×800', 50).tap();

  await expect.poll(() => jumpBodies.length).toBe(1);
  expect(jumpBodies[0]).toEqual({
    tester_id: '20260922-name',
    arm_id: 'arm5',
    environment: 'indoors',
    rungs: ALL_RUNGS.filter((id) => id !== 'full-50' && id !== 'half-75'),
    rung_id: 'full-50',
  });
  expect(startRequests).toBe(0);
});

test('with nothing ticked a pressed setting is sent as the only one', async ({ page }) => {
  await mockBaseApis(page, () => jumpState({}, 'full-300', true));
  const jumpBodies: Record<string, unknown>[] = [];
  await page.route('**/api/tester/ladder/jump', async (route) => {
    jumpBodies.push(route.request().postDataJSON());
    await fulfillJson(route, jumpState({ 'half-30': ['active', null] }, 'half-30', false));
  });
  await page.goto('/tester.html');
  for (const box of await page.locator('#ladder-choice input').all()) await box.uncheck();

  await settingButton(page, '640×400', 30).tap();

  await expect.poll(() => jumpBodies.length).toBe(1);
  expect(jumpBodies[0]).toMatchObject({ rungs: ['half-30'], rung_id: 'half-30' });
});

test('a setting with its good swings shows done and cannot be pressed; failed ones can', async ({ page }) => {
  await mockBaseApis(page, () =>
    jumpState(
      {
        'full-300': ['done', null],
        'full-200': ['failed', '3 red swings'],
        'full-150': ['skipped', 'too bright for the ball: 40% of it is clipped'],
        'full-100': ['active', null],
      },
      'full-100',
      false
    )
  );
  await page.goto('/tester.html');

  await expect(settingButton(page, '1280×800', 300)).toBeDisabled();
  await expect(settingButton(page, '1280×800', 300)).toHaveText('300 µs done');
  await expect(settingButton(page, '1280×800', 200)).toBeEnabled();
  await expect(settingButton(page, '1280×800', 150)).toBeEnabled();
  await expect(settingButton(page, '640×400', 300)).toBeEnabled();
});

test('a refused press shows why and keeps saying so while the ladder polls', async ({ page }) => {
  await mockBaseApis(page, () => jumpState({ 'full-300': ['active', null] }, 'full-300', false));
  await page.route('**/api/tester/ladder/jump', (route) =>
    fulfillJson(route, { error: 'the ladder is changing camera mode; press the setting again in a moment' }, 409)
  );
  await page.goto('/tester.html');

  await settingButton(page, '640×400', 300).tap();

  const error = page.locator('#ladder-jump-error');
  await expect(error).toHaveText('the ladder is changing camera mode; press the setting again in a moment');
  await page.waitForTimeout(1700);
  await expect(error).toBeVisible();
});

test("a pressed setting's bad light is shown as a warning", async ({ page }) => {
  await mockBaseApis(page, () =>
    jumpState({ 'full-10': ['active', null] }, 'full-10', false, {
      'full-10': { light_warning: 'too dark for the ball: 5 DN above black' },
    })
  );
  await page.goto('/tester.html');

  const row = page.locator('#ladder-panel .rung[data-rung="full-10"]');
  await expect(row).toContainText('1280×800 · 10 µs');
  await expect(row).toContainText('active: 0/5 usable pictures');
  await expect(row).toContainText('runs anyway: too dark for the ball: 5 DN above black');
});

for (const viewport of KIOSK_VIEWPORTS) {
  test(`the setting buttons fit at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockBaseApis(page, () => jumpState({ 'full-300': ['done', null] }, 'full-200', false));
    await page.goto('/tester.html');

    const buttons = page.locator('#ladder-jump');
    await buttons.scrollIntoViewIfNeeded();
    await expect(settingButton(page, '1280×800', 300)).toBeDisabled();
    const outside = await buttons
      .locator('button')
      .evaluateAll((items) => items.some((item) => item.getBoundingClientRect().right > window.innerWidth));
    expect(outside).toBe(false);
  });

  test(`photo handoff controls fit at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockBaseApis(page, () => ladderState({ capture: 'camera-final-layout', rung_id: 'full-300' }));
    await page.goto('/tester.html');

    const handoff = page.locator('#photo-handoff');
    await handoff.scrollIntoViewIfNeeded();
    await expect(handoff).toBeVisible();
    const clipped = await handoff.locator('button').evaluateAll((buttons) =>
      buttons.some((button) => {
        const rect = button.getBoundingClientRect();
        return rect.left < 0 || rect.right > window.innerWidth || rect.top < 0 || rect.bottom > window.innerHeight;
      })
    );
    expect(clipped).toBe(false);
  });

  test(`the setting boxes fit at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockBaseApis(page, () => choiceState({ 'full-300': ['done', null] }, true));
    await page.goto('/tester.html');

    const choice = page.locator('#ladder-choice');
    await choice.scrollIntoViewIfNeeded();
    await expect(setting(page, '1280×800', 300)).toBeDisabled();
    const outside = await choice
      .locator('label')
      .evaluateAll((labels) => labels.some((label) => label.getBoundingClientRect().right > window.innerWidth));
    expect(outside).toBe(false);
  });
}

test('the hardware check records whether RESET was pressed', async ({ page }) => {
  await mockBaseApis(page, () => ladderState(null));
  const runBodies: Record<string, unknown>[] = [];
  await page.route('**/api/tester/run', async (route) => {
    runBodies.push(route.request().postDataJSON());
    await fulfillJson(route, {});
  });
  await page.route('**/api/tester/status?**', (route) =>
    fulfillJson(route, { job: { state: 'complete', message: 'done', output: ['IWR6843 CLI ready'] } })
  );
  await page.goto('/tester.html');
  const reset = page.locator('#iwr-reset-pressed');

  await reset.check();
  await page.getByRole('button', { name: 'Check the unit' }).tap();
  await expect(page.locator('#check-verdict')).toContainText('ready');
  await expect(reset).not.toBeChecked();
  await page.getByRole('button', { name: 'Check the unit' }).tap();
  await expect.poll(() => runBodies.length).toBe(2);

  expect(runBodies.map((body) => body.operator_reset)).toEqual([true, false]);
  expect(runBodies.every((body) => body.action === 'preflight')).toBe(true);
});

test('shows the kiosk shot beside the swing verdict, every metric with its label', async ({ page }) => {
  const catalog = JSON.parse(readFileSync(new URL('../../src/data/metricCatalog.json', import.meta.url), 'utf8')) as {
    metrics: Array<{ id: string; label: string; unit: string }>;
  };
  const cycle = ['measured', 'calculated', 'model_geometry', 'estimated', 'experimental', 'not_measured'];
  const metrics = Object.fromEntries(
    catalog.metrics.map((spec, index) => {
      const status = cycle[index % cycle.length];
      return [
        spec.id,
        status === 'not_measured'
          ? { value: null, unit: spec.unit, status, source: null, reason: `${spec.id} reason` }
          : { value: 1 + index, unit: spec.unit, status, source: 'fixture', reason: null },
      ];
    })
  );
  await mockBaseApis(page, () => ladderState(null));
  await page.route('**/api/shots/latest', (route) =>
    fulfillJson(route, { shot: { shot_number: 7, club: '7-iron', metrics }, catalog })
  );
  await page.goto('/tester.html');

  const box = page.locator('#kiosk-shot');
  await expect(box).toContainText('Kiosk numbers for its latest shot #7 (7-iron)');
  // 13 Oct: the ball distance joins the key line
  await expect(box.locator('.kiosk-shot__key [data-kiosk-metric]')).toHaveCount(13);
  // a metric not built yet with no value goes to the one "Coming later" row
  const comingLater = new Set(['club_speed_full', 'gyro', 'impact_offset', 'impact_height', 'dynamic_lie']);
  const later = catalog.metrics.filter(
    (spec, index) => comingLater.has(spec.id) && cycle[index % cycle.length] === 'not_measured'
  );
  const rows = box.locator('tr[data-kiosk-metric]:not(.coming-later)');
  await expect(rows).toHaveCount(catalog.metrics.length - later.length);
  for (const [index, spec] of catalog.metrics.entries()) {
    if (later.includes(spec)) continue;
    const status = cycle[index % cycle.length];
    const row = box.locator(`tr[data-kiosk-metric="${spec.id}"]`);
    await expect(row.locator(`.kiosk-label.l-${status}`)).toHaveCount(1);
    if (status === 'not_measured') await expect(row).toContainText(`${spec.id} reason`);
  }
});

test('says so when the kiosk does not answer for its shot', async ({ page }) => {
  await mockBaseApis(page, () => ladderState(null));
  await page.route('**/api/shots/latest', (route) => route.abort());
  await page.goto('/tester.html');
  await expect(page.locator('#kiosk-shot')).toContainText('Kiosk numbers unavailable');
});

// 13 Oct: each swing's ball distance, with where it came from, on the verdict card
const VERDICT_SHOT_DIR = process.env.UX_SWING_RANGE_DIR;

function ballDistanceShot(status: 'measured' | 'estimated') {
  const catalog = JSON.parse(readFileSync(new URL('../../src/data/metricCatalog.json', import.meta.url), 'utf8')) as {
    metrics: Array<{ id: string; label: string; unit: string }>;
  };
  const metrics = Object.fromEntries(
    catalog.metrics.map((spec) => [
      spec.id,
      { value: null, unit: spec.unit, status: 'not_measured', source: null, reason: 'not on this fixture' },
    ])
  );
  metrics.ball_speed = { value: 101.1, unit: 'mph', status: 'measured', source: 'OPS243 radial', reason: null };
  metrics.club_speed = { value: 75.1, unit: 'mph', status: 'measured', source: 'OPS243 radial', reason: null };
  metrics.launch_vertical = {
    value: 18.9,
    unit: 'deg',
    status: status === 'measured' ? 'measured' : 'estimated',
    source: 'radar',
    reason: null,
  };
  metrics.ball_range =
    status === 'measured'
      ? {
          value: 2.08,
          unit: 'm',
          status,
          source: "IWR post-impact launch point (this shot's own capture)",
          reason: null,
        }
      : { value: 2.11, unit: 'm', status, source: 'camera prior: the resting ball’s size, coarse', reason: null };
  return { shot: { shot_number: 12, club: '7-iron', metrics }, catalog };
}

test('the verdict card shows the swing’s ball distance and where it came from', async ({ page }) => {
  await mockBaseApis(page, () => ladderState(null));
  let body = ballDistanceShot('measured');
  await page.route('**/api/shots/latest', (route) => fulfillJson(route, body));
  await page.goto('/tester.html');

  const distance = page.locator('#kiosk-shot .kiosk-shot__key [data-kiosk-metric="ball_range"]');
  await expect(distance).toContainText('Ball distance: 2.08 m');
  await expect(distance).toContainText('radar, this swing');
  await expect(distance.locator('.kiosk-label.l-measured')).toHaveCount(1);

  body = ballDistanceShot('estimated');
  await expect(distance).toContainText('Ball distance: 2.11 m', { timeout: 10000 });
  await expect(distance).toContainText('camera estimate (coarse)');
  await expect(distance.locator('.kiosk-label.l-estimated')).toHaveCount(1);
});

for (const viewport of [
  { width: 1024, height: 600 },
  { width: 800, height: 480 },
]) {
  test(`the verdict card with the ball distance fits at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockBaseApis(page, () => ladderState(null));
    await page.route('**/api/shots/latest', (route) => fulfillJson(route, ballDistanceShot('measured')));
    await page.goto('/tester.html');

    const card = page.locator('#verdict-card');
    await card.scrollIntoViewIfNeeded();
    await expect(card).toContainText('Ball distance: 2.08 m');
    const outside = await card
      .locator('.kiosk-metric')
      .evaluateAll((nodes) => nodes.some((node) => node.getBoundingClientRect().right > window.innerWidth + 1));
    expect(outside).toBe(false);
    if (VERDICT_SHOT_DIR) {
      await card.screenshot({ path: `${VERDICT_SHOT_DIR}/verdict-${viewport.width}x${viewport.height}.png` });
    }
  });
}

test('the ladder tells the tester which settings their light supports, before any swing', async ({ page }) => {
  await mockBaseApis(page, () => ({
    ...ladderState(null),
    light_warning: {
      status: 'measured',
      supported: ['full-300', 'full-200'],
      unsupported: ['full-150', 'full-100'],
      message:
        'Your light supports 1280x800 300 us, 1280x800 200 us. Shorter settings need at least 1.33x more light on the ball; they are recorded but will read too dark.',
    },
  }));
  await page.goto('/tester.html');

  await expect(page.locator('#ladder-light-warning')).toContainText(
    'Your light supports 1280x800 300 us, 1280x800 200 us'
  );
});

test('the ladder marks the setting the kiosk would use and greys out ones the sensor cannot take', async ({ page }) => {
  await mockBaseApis(page, () => ({
    ...ladderState(null),
    light_warning: {
      status: 'measured',
      supported: ['full-300'],
      unsupported: ['full-150'],
      message: 'Your light supports 1280x800 300 us.',
      kiosk_would_use: {
        arm5: {
          arm_id: 'arm5',
          mode: '1280×800',
          status: 'ball_driven',
          exposure_us: 294,
          gain: 15.9375,
          rung_id: 'full-300',
          text: 'kiosk would use: 1280×800 · 294 µs × gain 15.94',
        },
        arm6: { arm_id: 'arm6', mode: '640×400', status: 'needs_sensor_minimum', reason: 'not read yet' },
      },
    },
    below_sensor_minimum: { 'full-10': "below the sensor's shortest exposure at 1280x800 (12 us)" },
  }));
  await page.goto('/tester.html');

  await expect(page.locator('#ladder-kiosk-mark')).toContainText(
    'kiosk would use: 1280×800 · 294 µs × gain 15.94 (nearest setting: 1280×800 · 300 µs)'
  );
  await expect(page.locator('#ladder-panel .rung[data-rung="full-300"] .kiosk-mark')).toHaveText('kiosk would use');
  const short = page.locator('#ladder-choice input[value="full-10"]');
  await expect(short).toBeDisabled();
  await expect(short).not.toBeChecked();
});

test('the verdict card says what to do about the light', async ({ page }) => {
  await mockBaseApis(page, () => ({
    ...ladderState(null),
    last_verdict: {
      color: 'red',
      rung_id: 'full-300',
      reasons: ['light: too bright for the ball: 40% of it is clipped'],
      light_cause: 'ball_clipped',
      light_label: 'too bright: shade the ball area (or an ND filter)',
    },
  }));
  await page.goto('/tester.html');

  await expect(page.locator('#verdict-light-label')).toHaveText(
    'Light: too bright: shade the ball area (or an ND filter)'
  );
});
