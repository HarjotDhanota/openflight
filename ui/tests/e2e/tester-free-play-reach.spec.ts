import { expect, test, type Page, type Route } from '@playwright/test';
import { DEFAULT_TESTER, lensState, mockConfirmedPlacementBox, useTester } from './helpers';

/**
 * What a tester reaches from the tester page around free play: the hand in step 1,
 * the first club in step 7, the face photo and the aim during free play, and a
 * launch monitor's export in step 8.
 */

async function fulfillJson(route: Route, payload: object, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(payload) });
}

const RECORDING = { enabled: true, tester_id: DEFAULT_TESTER, run_id: 'run-1', phase: 'recording' };

async function mockSetupDone(page: Page, world: { freePlay: object; aim?: object }) {
  await mockConfirmedPlacementBox(page);
  await page.route('**/api/tester/lens?**', (route) => fulfillJson(route, lensState()));
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
  await page.route('**/api/tester/aim-point**', (route) =>
    fulfillJson(route, world.aim ?? { tester_id: DEFAULT_TESTER, state: 'skipped', runs: {} })
  );
  await page.route('**/api/camera/preview.jpg**', (route) => route.fulfill({ status: 204 }));
  await page.route('**/api/tester/attempts?**', (route) => fulfillJson(route, { schema_version: 1, scopes: [] }));
  await page.route('**/api/tester/ladder?**', (route) => fulfillJson(route, { ladder: { rungs: {} }, stopped: true }));
  await page.route('**/api/tester/status**', (route) => fulfillJson(route, { free_play: world.freePlay }));
}

test.describe('the tester page around free play', () => {
  test.use({ viewport: { width: 1024, height: 600 }, hasTouch: true });

  test('step 1 keeps the hand per tester: right-handed until the tester taps left', async ({ page }) => {
    await useTester(page);
    await mockSetupDone(page, { freePlay: { enabled: false } });
    let saved = 'RH';
    const posts: object[] = [];
    await page.route('**/api/tester/handedness**', async (route) => {
      if (route.request().method() === 'POST') {
        const body = route.request().postDataJSON();
        posts.push(body);
        saved = body.handed;
      }
      await fulfillJson(route, {
        tester_id: DEFAULT_TESTER,
        handed: saved,
        source: saved === 'RH' ? 'default' : 'tester',
      });
    });
    await page.goto('/tester.html');

    const right = page.locator('#handed-choice button[data-handed="RH"]');
    const left = page.locator('#handed-choice button[data-handed="LH"]');
    await expect(right).toHaveText('Right-handed');
    await expect(right).toHaveAttribute('aria-pressed', 'true');
    await expect(left).toHaveAttribute('aria-pressed', 'false');
    await left.tap();
    await expect(left).toHaveAttribute('aria-pressed', 'true');
    await expect(right).toHaveAttribute('aria-pressed', 'false');
    expect(posts).toEqual([{ tester_id: DEFAULT_TESTER, handed: 'LH' }]);

    await page.reload();
    await expect(left).toHaveAttribute('aria-pressed', 'true');
  });

  test('step 7 asks for the first club from the kiosk grid, and starts on it', async ({ page }) => {
    await useTester(page);
    const world: { freePlay: object } = { freePlay: { enabled: false } };
    await mockSetupDone(page, world);
    const starts: Record<string, unknown>[] = [];
    await page.route('**/api/tester/run', async (route) => {
      starts.push(route.request().postDataJSON());
      world.freePlay = RECORDING;
      await fulfillJson(route, { job: { state: 'running', action: 'free_play' }, free_play: RECORDING }, 202);
    });
    await page.goto('/tester.html');

    await expect(page.locator('#first-club-label')).toHaveText('Which club will you hit first?');
    const grid = page.locator('#first-club');
    for (const group of ['Irons', 'Hybrids', 'Woods'])
      await expect(grid.getByText(group, { exact: true })).toBeVisible();
    await expect(grid.locator('button')).toHaveCount(20);
    const start = page.locator('#step-free-play');
    await page.locator('#free-play-resolution button[data-resolution="arm5"]').tap();
    await expect(start).toBeDisabled();
    const pw = grid.locator('button[data-club="pw"]');
    await expect(pw).toHaveText('PW');
    expect((await pw.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    await pw.tap();
    await expect(pw).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('#first-club-hint')).toHaveText('Free play starts on the Pitching Wedge.');
    await expect(start).toBeEnabled();
    await start.tap();
    await expect.poll(() => starts.length).toBe(1);
    expect(starts[0]).toMatchObject({ action: 'free_play', resolution: 'arm5', club: 'pw' });
    // once free play runs the kiosk's own CHANGE CLUB takes over
    await expect(page.locator('#first-club-row')).toBeHidden();
  });
});
