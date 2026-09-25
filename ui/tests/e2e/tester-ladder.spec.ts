import { expect, test, type Page, type Route } from '@playwright/test';
import { KIOSK_VIEWPORTS } from './helpers';

test.use({ hasTouch: true });

type PhotoTarget = { capture: string; rung_id: string };

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
      'Arm 5 is complete. Do not swing. Photograph or skip the face image for full-300 (camera-final-17) to continue to Arm 6.'
    )
  ).toBeVisible();
  const capture = page.getByRole('button', { name: 'Photograph face' });
  await capture.tap();
  await capture.tap({ force: true });
  await expect(capture).toBeDisabled();
  const start = page.getByRole('button', { name: 'C. Start the exposure ladder' });
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
  await expect(start).toBeEnabled();
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
  await page.locator('#tester-id').fill('new-tester');
  releaseOld();

  await expect(page.locator('#status')).toHaveText('new-tester (complete)');
  await expect(page.locator('#status')).not.toContainText('20260922-name');
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
  await page.locator('#tester-id').fill(restoredTesterId);
  await page.reload();

  await expect(page.locator('#tester-id')).toHaveValue(restoredTesterId);
  await expect(page.getByText('Photo held for full-300. Resume the ladder to use the camera.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Photograph face' })).toBeDisabled();
  await page.getByRole('button', { name: 'Resume ladder' }).tap();
  await expect(page.getByRole('button', { name: 'Pause ladder' })).toBeVisible();
  await page.getByRole('button', { name: 'Skip photo' }).tap();

  expect(ladderTesterIds).toContain(restoredTesterId);
  expect(startBody).toEqual({ tester_id: restoredTesterId, arm_id: 'arm5', environment: 'indoors' });
  expect(skipBody).toEqual({
    tester_id: restoredTesterId,
    capture: 'camera-final-19',
    rung_id: 'full-300',
    action: 'skip',
  });
  await expect(page.locator('#photo-handoff')).toBeHidden();
});

for (const viewport of KIOSK_VIEWPORTS) {
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
}
