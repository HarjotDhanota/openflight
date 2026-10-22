import { expect, test, type Page, type Route } from '@playwright/test';
import { DEFAULT_TESTER, lensState, mockConfirmedPlacementBox, PATCH_PROJECTION, useTester } from './helpers';

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

const FRAME_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAABAAAAAKCAAAAACY1YFAAAAAD0lEQVR4nGPQQAMMg1UAAE4iGQEMndODAAAAAElFTkSuQmCC',
  'base64'
);

async function mockFreePlayCamera(page: Page) {
  const views: string[] = [];
  await page.route('**/api/tester/free-play/view.jpg**', (route) => {
    views.push(route.request().url());
    return route.fulfill({ status: 200, contentType: 'image/png', body: FRAME_PNG });
  });
  return views;
}

test.describe('free play on the tester page', () => {
  test.use({ viewport: { width: 1024, height: 600 }, hasTouch: true });

  test('a face photo pauses free play, shows the camera in the box, and saves for the latest shot', async ({
    page,
  }) => {
    await useTester(page);
    const world: { freePlay: object } = { freePlay: RECORDING };
    await mockSetupDone(page, world);
    const views = await mockFreePlayCamera(page);
    const posts: [string, object][] = [];
    await page.route('**/api/tester/free-play/pause', async (route) => {
      posts.push(['pause', route.request().postDataJSON()]);
      world.freePlay = { ...RECORDING, paused: { cause: 'photo' } };
      await fulfillJson(route, { pause: { cause: 'photo' }, paused: { cause: 'photo' } });
    });
    await page.route('**/api/tester/free-play/photo', async (route) => {
      posts.push(['photo', route.request().postDataJSON()]);
      world.freePlay = RECORDING;
      await fulfillJson(route, { photo: { shot_number: 7, path: 'face-photos/shot-0007.pgm' }, paused: null });
    });
    await page.goto('/tester.html');

    await expect(page.locator('#free-play-status')).toContainText('Free play is recording.');
    await expect(page.locator('#free-play-note')).toContainText("Face photos aren't required, but they help");
    const start = page.locator('#free-play-photo-start');
    await expect(start).toHaveText('Photograph the face');
    await start.tap();
    const panel = page.locator('#free-play-photo');
    await expect(panel).toBeVisible();
    await expect(page.locator('#free-play-photo-box')).toBeVisible();
    await expect(page.locator('#free-play-status')).toHaveText(
      'Free play is paused: taking a face photo. Shots are not counted.'
    );
    await expect.poll(() => views.length).toBeGreaterThan(0);
    expect(views[0]).toContain(`tester_id=${DEFAULT_TESTER}`);
    await expect(start).toBeHidden();
    await page.screenshot({ path: test.info().outputPath('free-play-face-photo.png') });

    await page.locator('#free-play-photo-take').tap();
    await expect(page.locator('#free-play-photo-note')).toHaveText('Photo saved for shot 7.');
    await expect(panel).toBeHidden();
    await expect(start).toBeVisible();
    expect(posts).toEqual([
      ['pause', { tester_id: DEFAULT_TESTER, cause: 'photo' }],
      ['photo', { tester_id: DEFAULT_TESTER, action: 'capture' }],
    ]);
  });

  test('a pause started on the scoreboard shows the photo; Skip goes on without one', async ({ page }) => {
    await useTester(page);
    const world: { freePlay: object } = { freePlay: { ...RECORDING, paused: { cause: 'photo' } } };
    await mockSetupDone(page, world);
    await mockFreePlayCamera(page);
    const actions: string[] = [];
    await page.route('**/api/tester/free-play/photo', async (route) => {
      actions.push(route.request().postDataJSON().action);
      world.freePlay = RECORDING;
      await fulfillJson(route, { photo: null, paused: null });
    });
    await page.goto('/tester.html');

    await expect(page.locator('#free-play-photo')).toBeVisible();
    await expect(page.locator('#free-play-photo-skip')).toHaveText('Skip');
    await page.locator('#free-play-photo-skip').tap();
    await expect(page.locator('#free-play-photo')).toBeHidden();
    await expect(page.locator('#free-play-photo-note')).toHaveText('No photo this time.');
    expect(actions).toEqual(['skip']);
  });

  test('moving the aim during free play uses the kiosk camera; the next shot measures from it', async ({ page }) => {
    await useTester(page);
    const confirmed = {
      tester_id: DEFAULT_TESTER,
      state: 'confirmed',
      previewing: false,
      preview_source: 'tester',
      setup_blockers: [],
      runs: {},
      projection: PATCH_PROJECTION,
      camera_tilt: { status: 'uncalibrated' },
      target_line: { source: 'aim_point', aim_px: [640, 400], yaw_deg: 0, kiosk_offset_deg: 0, warnings: [] },
      total_uncertainty_deg: 0.5,
      confirmed_at_utc: '2026-10-21T10:00:00+00:00',
    };
    let aim: Record<string, unknown> = confirmed;
    const world: { freePlay: object; aim: object } = { freePlay: RECORDING, aim };
    await mockSetupDone(page, world);
    const views = await mockFreePlayCamera(page);
    const posts: Record<string, unknown>[] = [];
    await page.unroute('**/api/tester/aim-point**');
    await page.route('**/api/tester/aim-point**', async (route) => {
      if (route.request().method() === 'GET') return fulfillJson(route, aim);
      const body = route.request().postDataJSON();
      posts.push(body);
      if (body.action === 'show') {
        world.freePlay = { ...RECORDING, paused: { cause: 'aim' } };
        aim = { ...confirmed, previewing: true, preview_source: 'kiosk' };
        return fulfillJson(route, aim);
      }
      world.freePlay = RECORDING;
      aim = { ...confirmed, target_line: { ...confirmed.target_line, aim_px: body.aim_px, yaw_deg: 3 } };
      return fulfillJson(route, {
        ...aim,
        change: 'moved',
        free_play: { applied: true, target_line: { revision: 1 } },
      });
    });
    let testerFrames = 0;
    await page.route('**/api/tester/live.png**', (route) => {
      testerFrames += 1;
      return route.fulfill({ status: 503 });
    });
    await page.goto('/tester.html');

    await page.locator('#aim-move').tap();
    await expect(page.locator('#aim-camera-frame')).toBeVisible();
    expect(views.length).toBeGreaterThan(0);
    expect(testerFrames).toBe(0);
    await expect(page.locator('#aim-camera-status')).toContainText('the kiosk camera');
    await page.locator('#aim-confirm').tap();
    await expect(page.locator('#aim-summary')).toContainText('The next shot is measured from the new target line.');
    expect(posts.map((body) => body.action)).toEqual(['show', 'confirm']);
  });

  test('a kiosk that did not take the new aim says so', async ({ page }) => {
    await useTester(page);
    const confirmed = {
      tester_id: DEFAULT_TESTER,
      state: 'confirmed',
      previewing: false,
      setup_blockers: [],
      runs: {},
      projection: PATCH_PROJECTION,
      target_line: { source: 'aim_point', aim_px: [640, 400], yaw_deg: 0, kiosk_offset_deg: 0, warnings: [] },
    };
    let aim: Record<string, unknown> = confirmed;
    const world: { freePlay: object } = { freePlay: RECORDING };
    await mockSetupDone(page, world);
    await mockFreePlayCamera(page);
    await page.unroute('**/api/tester/aim-point**');
    await page.route('**/api/tester/aim-point**', (route) => {
      if (route.request().method() === 'GET') return fulfillJson(route, aim);
      if (route.request().postDataJSON().action === 'show') {
        world.freePlay = { ...RECORDING, paused: { cause: 'aim' } };
        aim = { ...confirmed, previewing: true, preview_source: 'kiosk' };
        return fulfillJson(route, aim);
      }
      aim = confirmed;
      return fulfillJson(route, {
        ...aim,
        change: 'moved',
        free_play: { applied: false, error: 'a shot is still being processed' },
      });
    });
    await page.goto('/tester.html');
    await page.locator('#aim-move').tap();
    await expect(page.locator('#aim-camera-frame')).toBeVisible();
    await page.locator('#aim-confirm').tap();
    await expect(page.locator('#aim-summary')).toContainText(
      'Saved, but the kiosk did not take it (a shot is still being processed). Set it again in a moment.'
    );
  });

  test('step 8 takes a launch monitor export in the normal flow', async ({ page }) => {
    await useTester(page);
    await mockSetupDone(page, {
      freePlay: {
        ...RECORDING,
        phase: 'ready',
        bundle: { name: `${DEFAULT_TESTER}-session-bundle-20261021T120000Z.zip` },
      },
    });
    const uploads: string[] = [];
    await page.route('**/api/tester/comparator', async (route) => {
      uploads.push(route.request().postData() ?? '');
      await fulfillJson(route, {
        saved: 'trackman.csv',
        in_package: false,
        beside: { bundle: 'b.zip', file: 'b-trackman.csv', folder: '~/OpenFlight-to-send' },
      });
    });
    await page.goto('/tester.html');

    await expect(page.locator('#launch-monitor-label')).toHaveText(
      'Hit beside a TrackMan or another launch monitor? Attach its export (CSV)'
    );
    await expect(page.locator('#advanced-package')).not.toHaveAttribute('open');
    await page.locator('#launch-monitor-file').setInputFiles({
      name: 'trackman.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from('Club,Ball Speed\n7i,120\n'),
    });
    await expect(page.locator('#launch-monitor-status')).toHaveText(
      'Attached trackman.csv: saved beside the package in ~/OpenFlight-to-send as b-trackman.csv. Send both.'
    );
    expect(uploads[0]).toContain(`name="tester_id"`);
    expect(uploads[0]).toContain(DEFAULT_TESTER);
  });

  test('Kiosk view opens the scoreboard in this window when the kiosk shell refuses new windows', async ({ page }) => {
    await useTester(page);
    await mockSetupDone(page, { freePlay: RECORDING });
    await page.unroute('**/api/tester/status**');
    await page.route('**/api/tester/status**', (route) =>
      fulfillJson(route, { free_play: RECORDING, kiosk: { state: 'running', url: 'http://localhost:8080/' } })
    );
    await page.addInitScript(() => {
      window.open = () => null;
    });
    await page.route('http://127.0.0.1:8080/', (route) => route.fulfill({ status: 200, body: 'scoreboard' }));
    await page.goto('/tester.html');
    await expect(page.locator('#kiosk-view')).toBeEnabled();
    await page.locator('#kiosk-view').tap();
    await expect(page).toHaveURL('http://127.0.0.1:8080/');
  });
});
