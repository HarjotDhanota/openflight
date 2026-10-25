import { expect, test, type Page, type Route } from '@playwright/test';
import { DEFAULT_TESTER, foundBall, mockLens, PATCH_PROJECTION, placementBoxState } from './testerHelpers';

// Field 24 Oct (outdoor test 1), the owner: "I would do a step, and then it would keep
// resetting or the page would jump. This happened on step 3 and 4 repeatedly. It would
// find the ball, I'd click accept, and then it would cancel again and start over."
// One poll that took a step back (a LIS3DH blip) reset steps 3 and 4, hid their live
// views and threw the dragged aim away; step 6 took the hardware while step 5 still
// measured 640×400; the band's label wrapped and the page jumped; and free play started
// with no ball range, every camera number withheld, nothing said.
test.use({ hasTouch: true });

const TESTER = DEFAULT_TESTER;
const WIDE_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAABAAAAAKCAAAAACY1YFAAAAAD0lEQVR4nGPQQAMMg1UAAE4iGQEMndODAAAAAElFTkSuQmCC',
  'base64'
);

async function json(route: Route, body: object | null, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
}

function eligibility(eligible = true) {
  return {
    schema_version: 1,
    tester_id: TESTER,
    config_hash: 'measured-v3-hash',
    eligible,
    stage: 'tester_admission',
    checks: [],
    blockers: eligible
      ? []
      : [{ id: 'operator_confirmation', reason: 'the unit moved since the setup was confirmed', remedy: 'Confirm.' }],
    warnings: [],
    operator_confirmation: {
      confirmed: eligible,
      confirmed_at: eligible ? '2026-10-24T17:14:39Z' : null,
      confirmation_id: eligible ? '3920b41760d6b3e2' : null,
      config_hash: 'measured-v3-hash',
      authority: 'operator_physical_setup',
    },
    runtime_requirements: ['ops', 'camera', 'iwr6843', 'lis3dh'],
  };
}

const NOT_STALE = { stale: false, reason: null, handed_yaw_deg: null };
function aimState(state: string, extra: Record<string, unknown> = {}) {
  return {
    tester_id: TESTER,
    state,
    previewing: false,
    setup_blockers: state === 'setup_not_confirmed' ? [{ id: 'operator_confirmation' }] : [],
    runs: { arm5: NOT_STALE, arm6: NOT_STALE },
    ...(state === 'needs_confirmation' || state === 'confirmed'
      ? {
          projection: PATCH_PROJECTION,
          camera_tilt: { status: 'uncalibrated' },
          patch: { centre_lfu_m: [0, 2.35], centre_px: [640, 478], outline_px: [], frame_size_px: [1280, 800] },
        }
      : {}),
    ...(state === 'needs_confirmation'
      ? { reason: 'not_set', aim: { aim_px: [640, 400], source: 'default_straight_ahead' } }
      : {}),
    ...extra,
  };
}

const STUDY_ARMS = (arms: string[]) =>
  arms.map((arm_id) => ({
    arm_id,
    light_index: 4.2e-3,
    lighting_required: false,
    too_bright: false,
    mixed_light: false,
    gain_screen: { stale: false, age_s: 40, prompt: null },
  }));

type Mocks = {
  setup?: () => object;
  box?: () => object;
  aim?: () => object;
  range?: () => object | null;
  display?: () => object | null;
  status?: () => object;
  liveOwner?: () => string | null;
};

async function stage(page: Page, o: Mocks) {
  await page.addInitScript((id) => sessionStorage.setItem('openflight-tester-id', id), TESTER);
  await mockLens(page);
  const posts: { path: string; body: Record<string, unknown> }[] = [];
  await page.route('**/api/tester/setup-eligibility?**', (route) => json(route, (o.setup ?? (() => eligibility()))()));
  await page.route('**/api/tester/lis3dh-check', (route) => json(route, { state: 'passed', passed: true }));
  await page.route('**/api/tester/placement-box**', (route) => {
    if (route.request().method() !== 'GET') posts.push({ path: 'placement-box', body: route.request().postDataJSON() });
    return json(route, (o.box ?? (() => placementBoxState(TESTER, 'confirmed')))());
  });
  await page.route('**/api/tester/aim-point**', (route) => {
    if (route.request().method() !== 'GET') posts.push({ path: 'aim-point', body: route.request().postDataJSON() });
    return json(route, (o.aim ?? (() => aimState('skipped')))());
  });
  await page.route('**/api/tester/tee-range**', (route) => {
    if (route.request().method() !== 'GET') posts.push({ path: 'tee-range', body: route.request().postDataJSON() });
    return json(route, { state: o.range ? o.range() : null, display: o.display ? o.display() : null });
  });
  await page.route('**/api/tester/status**', (route) => json(route, o.status ? o.status() : { study: { arms: [] } }));
  await page.route('**/api/tester/attempts?**', (route) => json(route, { schema_version: 1, scopes: [] }));
  await page.route('**/api/tester/ladder?**', (route) => json(route, { ladder: null, stopped: true }));
  await page.route('**/api/tester/ready-light**', (route) =>
    json(route, { state: 'red', cause: 'kiosk not running', source: 'tester', swing: null, time_left_s: null })
  );
  await page.route('**/api/tester/live', (route) => {
    const owner = o.liveOwner ? o.liveOwner() : null;
    return json(route, {
      running: Boolean(owner),
      arm_id: 'arm5',
      owner: owner ? { kind: owner, tester_id: TESTER, epoch_id: null, arm_id: 'arm5' } : null,
      stats: owner ? { mean: 90, p99: 200, max: 230, clipped_pct: 0 } : null,
      association: null,
      guided_display: null,
      placement_box: null,
      preview_exposure: owner ? { purpose: 'box_preview', display_only: true, state: 'settled' } : null,
    });
  });
  await page.route('**/api/tester/live.png?**', (route) =>
    route.fulfill({ status: 200, contentType: 'image/png', body: WIDE_PNG })
  );
  return { posts };
}

// every label the bar showed, and every time a live view was hidden or lost its picture
async function watchJumps(page: Page) {
  await page.evaluate(() => {
    const w = window as unknown as { labels: string[]; lost: string[] };
    w.labels = [];
    w.lost = [];
    const bar = document.getElementById('stepbar-step') as HTMLElement;
    new MutationObserver(() => w.labels.push(bar.textContent || '')).observe(bar, {
      childList: true,
      characterData: true,
      subtree: true,
    });
    for (const id of ['placement-preview', 'aim-preview']) {
      const node = document.getElementById(id) as HTMLElement;
      // a view that was showing and is hidden (setting hidden again on a hidden one is no jump)
      new MutationObserver((records) => {
        for (const record of records) if (record.oldValue === null && node.hidden) w.lost.push(`${id} hidden`);
      }).observe(node, { attributes: true, attributeOldValue: true, attributeFilter: ['hidden'] });
    }
    new MutationObserver((records) => {
      for (const record of records)
        if (record.oldValue && !(record.target as HTMLImageElement).getAttribute('src'))
          w.lost.push(`${(record.target as HTMLElement).id} src removed`);
    }).observe(document.body, { attributes: true, attributeOldValue: true, subtree: true, attributeFilter: ['src'] });
  });
}
const jumps = (page: Page) =>
  page.evaluate(() => {
    const w = window as unknown as { labels: string[]; lost: string[] };
    return { labels: w.labels, lost: w.lost };
  });

test('one poll that says the setup is not confirmed leaves step 3 and its live view in place', async ({ page }) => {
  let flips = 0;
  let boxPolls = 0;
  await stage(page, {
    box: () => {
      boxPolls += 1;
      if (flips > 0) {
        flips -= 1;
        return placementBoxState(TESTER, 'setup_not_confirmed', { setup_blockers: [{ id: 'operator_confirmation' }] });
      }
      return placementBoxState(TESTER, 'needs_confirmation', { previewing: true, ball: foundBall({ confirmable: true }) });
    },
    aim: () => aimState('needs_patch'),
    liveOwner: () => 'placement_box',
  });
  await page.goto('/tester.html');
  const bar = page.locator('#stepbar-step');
  await expect(bar).toHaveText('Step 3 · Ball in the box');
  await expect(page.locator('#placement-camera-frame')).toBeVisible();
  await expect(page.locator('#placement-confirm')).toBeEnabled();
  await watchJumps(page);

  // one blip, as a LIS3DH reading past 0.5° made on 24 Oct
  const before = boxPolls;
  flips = 1;
  await expect.poll(() => boxPolls).toBeGreaterThan(before + 2);
  await page.waitForTimeout(500);

  const seen = await jumps(page);
  expect(seen.labels.every((label) => label === 'Step 3 · Ball in the box')).toBe(true);
  expect(seen.lost).toEqual([]);
  await expect(page.locator('#placement-camera-frame')).toBeVisible();
  await expect(page.locator('#placement-confirm')).toBeEnabled();

  // a setup that stays unconfirmed is believed: the box step waits for step 2 again
  flips = 1000;
  await expect(page.locator('#placement-summary')).toContainText('Finish step 2');
  await expect(page.locator('#placement-preview')).toBeHidden();
});

test('one ineligible setup poll keeps step 2 done', async ({ page }) => {
  let flips = 0;
  let setupPolls = 0;
  await stage(page, {
    setup: () => {
      setupPolls += 1;
      if (flips > 0) {
        flips -= 1;
        return eligibility(false);
      }
      return eligibility(true);
    },
    box: () => placementBoxState(TESTER, 'needs_confirmation', { ball: foundBall({ confirmable: true }) }),
    aim: () => aimState('needs_patch'),
  });
  await page.goto('/tester.html');
  const bar = page.locator('#stepbar-step');
  await expect(bar).toHaveText('Step 3 · Ball in the box');
  await watchJumps(page);
  const before = setupPolls;
  flips = 1;
  await expect.poll(() => setupPolls, { timeout: 8000 }).toBeGreaterThan(before + 2);
  const seen = await jumps(page);
  expect(seen.labels.filter((label) => label !== 'Step 3 · Ball in the box')).toEqual([]);
  await expect(page.locator('#step-2')).toHaveAttribute('data-state', 'done');
});

test('a found ball the camera has not checked twice, or not lately, cannot be confirmed yet', async ({ page }) => {
  let ball: Record<string, unknown> = foundBall({ confirmable: false, look_age_s: 0.4 });
  await stage(page, {
    box: () => placementBoxState(TESTER, 'needs_confirmation', { previewing: true, ball }),
    aim: () => aimState('needs_patch'),
    liveOwner: () => 'placement_box',
  });
  await page.goto('/tester.html');
  const status = page.locator('#placement-ball-status');
  await expect(status).toContainText('checking it again before you confirm');
  await expect(status).toHaveAttribute('data-state', 'waiting');
  await expect(page.locator('#placement-confirm')).toBeDisabled();

  ball = foundBall({ confirmable: false, look_age_s: 13.2 });
  await expect(status).toContainText('the camera has not seen it for a moment');
  await expect(page.locator('#placement-confirm')).toBeDisabled();

  ball = foundBall({ confirmable: true, look_age_s: 0.3 });
  await expect(status).toContainText('✓ Ball found in the box.');
  await expect(page.locator('#placement-confirm')).toBeEnabled();
});

test('the dragged aim line survives the aim step dropping back and returning', async ({ page }) => {
  let state = 'needs_confirmation';
  let previewing = false;
  const mocks = await stage(page, {
    aim: () => ({ ...aimState(state), previewing }),
    liveOwner: () => (previewing ? 'aim_point' : null),
  });
  await page.unroute('**/api/tester/aim-point**');
  await page.route('**/api/tester/aim-point**', (route) => {
    if (route.request().method() === 'GET') return json(route, { ...aimState(state), previewing });
    const body = route.request().postDataJSON() as Record<string, unknown>;
    mocks.posts.push({ path: 'aim-point', body });
    if (body.action === 'show') previewing = true;
    return json(route, { ...aimState(state), previewing });
  });
  await page.goto('/tester.html');
  const overlay = page.locator('#aim-overlay');
  await expect(overlay).toHaveAttribute('data-aim', '640.0,400.0');

  const picture = await page.evaluate(() => {
    const frame = document.getElementById('aim-camera-frame') as HTMLElement;
    frame.scrollIntoView({ block: 'center' });
    const rect = frame.getBoundingClientRect();
    return { x: rect.left, y: rect.top, width: rect.width, height: rect.height };
  });
  const at = (x: number, y: number) => ({
    x: picture.x + (x * picture.width) / 1280,
    y: picture.y + (y * picture.height) / 800,
  });
  const start = at(640, 400);
  const end = at(760, 380);
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(end.x, end.y, { steps: 5 });
  await page.mouse.up();
  const dragged = ((await overlay.getAttribute('data-aim')) ?? '').split(',').map(Number);
  expect(dragged[0]).toBeCloseTo(760, -1);

  // the setup drops for long enough to be believed, then comes back
  state = 'setup_not_confirmed';
  previewing = false;
  await expect(page.locator('#aim-preview')).toBeHidden({ timeout: 10000 });
  state = 'needs_confirmation';
  await expect(page.locator('#aim-preview')).toBeVisible({ timeout: 10000 });
  await expect(overlay).toBeVisible();
  const kept = ((await overlay.getAttribute('data-aim')) ?? '').split(',').map(Number);
  expect(kept[0]).toBeCloseTo(dragged[0], 0);
  expect(kept[1]).toBeCloseTo(dragged[1], 0);
});

test('step 6 waits for step 5 to measure both modes before it takes the camera', async ({ page }) => {
  const runs: string[] = [];
  let job: Record<string, unknown> | null = null;
  let screened: string[] = [];
  let jobPolls = 0;
  let arm6Done = false;
  const mocks = await stage(page, {
    status: () => {
      // 1280×800 runs for two status polls; 640×400 until the test lets it finish
      if (job && job.state === 'running') {
        jobPolls += 1;
        if (job.arm === 'arm5' ? jobPolls >= 2 : arm6Done) {
          job = { ...job, state: 'complete' };
          screened = [...new Set([...screened, job.arm as string])];
        }
      }
      return { job, study: { arms: STUDY_ARMS(screened) } };
    },
  });
  await page.route('**/api/tester/run', (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    runs.push(`${body.action}:${body.arm_id}`);
    job = { state: 'running', action: body.action, arm: body.arm_id, message: 'measuring' };
    jobPolls = 0;
    return json(route, { job }, 202);
  });
  await page.goto('/tester.html');
  await expect(page.locator('#stepbar-step')).toHaveText('Step 5 · Check the light');
  await page.locator('#step-light').click();
  await expect.poll(() => runs).toEqual(['gain:arm5', 'gain:arm6']);
  // 1280×800 is measured while 640×400 still runs: the ball range must not start
  expect(screened).toContain('arm5');
  await page.waitForTimeout(3000);
  expect(mocks.posts.filter((post) => post.path === 'tee-range')).toEqual([]);
  await expect(page.locator('#stepbar-step')).toHaveText('Step 5 · Check the light');
  // once both are done, step 6 opens its camera by itself
  arm6Done = true;
  await expect(page.locator('#stepbar-step')).toHaveText('Step 6 · Ball range', { timeout: 10000 });
  await expect
    .poll(() => mocks.posts.filter((post) => post.path === 'tee-range').map((post) => post.body.action))
    .toContain('start');
});

// at 520 px the old band wrapped at step 4 only (79 -> 94 -> 79 px), and the views jumped
for (const viewport of [
  { width: 800, height: 480 },
  { width: 520, height: 700 },
]) {
  test(`the sticky band keeps one height whatever the step at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    let box = 'needs_confirmation';
    let aim = 'needs_patch';
    await stage(page, {
      box: () => placementBoxState(TESTER, box),
      aim: () => aimState(aim),
    });
    await page.goto('/tester.html');
    const bar = page.locator('#stepbar-step');
    const heights: number[] = [];
    const measure = async () =>
      heights.push(await page.evaluate(() => (document.getElementById('sticky-top') as HTMLElement).offsetHeight));
    await expect(bar).toHaveText('Step 3 · Ball in the box');
    await measure();
    box = 'confirmed';
    await expect(bar).toHaveText('Step 4 · Aim at your target');
    await measure();
    aim = 'skipped';
    await expect(bar).toHaveText('Step 5 · Check the light');
    await measure();
    expect(new Set(heights).size).toBe(1);
    const cause = await page.evaluate(() => {
      const node = document.getElementById('ready-light-cause') as HTMLElement;
      return { height: node.offsetHeight, line: parseFloat(getComputedStyle(node).lineHeight) || 0 };
    });
    // one line of the band's text, never two
    expect(cause.height).toBeLessThan(2 * (cause.line || 16));
  });
}

test('free play says plainly that camera numbers are withheld without a ball range, and retries it', async ({ page }) => {
  let range: Record<string, unknown> = {
    epoch_id: 'setup-20261025',
    phase: 'raw_only',
    reason: 'patch_ball_withheld_ambiguous',
    evidence: {},
    solution: { status: 'unresolved', selected_range_m: null },
  };
  const mocks = await stage(page, {
    range: () => range,
    display: () => ({
      summary: 'more than one ball-like thing was in the box',
      canonical: { state: 'withheld', range_m: null, reason: 'patch_ball_withheld_ambiguous' },
    }),
    status: () => ({ study: { arms: STUDY_ARMS(['arm5', 'arm6']) }, free_play: { enabled: false } }),
  });
  await page.unroute('**/api/tester/tee-range**');
  await page.route('**/api/tester/tee-range**', (route) => {
    if (route.request().method() !== 'GET') {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      mocks.posts.push({ path: 'tee-range', body });
      range = { epoch_id: 'setup-again', phase: 'needs_camera_arm5', reason: 'place_the_ball_in_the_box', evidence: {} };
    }
    return json(route, {
      state: range,
      display:
        range.phase === 'raw_only'
          ? {
              summary: 'more than one ball-like thing was in the box',
              canonical: { state: 'withheld', range_m: null, reason: 'patch_ball_withheld_ambiguous' },
            }
          : null,
    });
  });
  await page.goto('/tester.html');
  await expect(page.locator('#stepbar-step')).toHaveText('Step 7 · Free play');
  const warning = page.locator('#free-play-range-warning');
  await expect(warning).toBeVisible();
  await expect(warning).toContainText('Camera numbers will be withheld for every shot this session');
  await expect(warning).toContainText('more than one ball-like thing was in the box');
  // a warning, never a block: free play can still start once its choices are made
  await expect(page.locator('#step-free-play')).toBeVisible();

  await page.locator('#free-play-range-retry').click();
  await expect
    .poll(() => mocks.posts.filter((post) => post.path === 'tee-range').map((post) => post.body.action))
    .toContain('start_over');
  await expect(page.locator('#stepbar-step')).toHaveText('Step 6 · Ball range');
  await expect(warning).toBeHidden();
});
