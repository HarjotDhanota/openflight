import { expect, test, type Page, type Route } from '@playwright/test';
import { BOX_OUTLINE_PX, foundBall, placementBoxState, rectOf, useTester } from './helpers';

// 1a (13 Oct): the TrackMan-style ball box. A FIXED box on the live view: the tester
// puts the ball in it and a check appears when the camera finds it; one horizontal
// line is dragged to where the floor meets the net or screen. Nothing is dragged
// to set a distance.
test.use({ hasTouch: true });

test.beforeEach(async ({ page }) => {
  await useTester(page);
});

const TESTER = '20260922-name';
const WIDE_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAABAAAAAKCAAAAACY1YFAAAAAD0lEQVR4nGPQQAMMg1UAAE4iGQEMndODAAAAAElFTkSuQmCC',
  'base64'
);

async function json(route: Route, body: object, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
}

function eligibility() {
  return {
    tester_id: TESTER,
    config_hash: 'fixture',
    eligible: true,
    checks: [],
    blockers: [],
    warnings: [],
    operator_confirmation: { confirmed: true, authority: 'operator', confirmed_at: 'now' },
    runtime_requirements: ['ops', 'camera', 'iwr6843', 'lis3dh'],
  };
}

type BoxOptions = {
  confirmed?: boolean;
  showFails?: string;
  ball?: Record<string, unknown>;
  netLine?: Record<string, unknown>;
};

type Range = { epoch_id: string; phase: string; reason: string; evidence: object; solution: object | null };

async function boxStep(page: Page, options: BoxOptions = {}) {
  let range: Range | null = null;
  let box: Record<string, unknown> = placementBoxState(TESTER, options.confirmed ? 'confirmed' : 'needs_confirmation', {
    ...(options.ball ? { ball: options.ball } : {}),
    net_line: options.netLine ?? { row_px: null, range_m: null, status: 'not_set', message: null },
  });
  let previewRunning = false;
  let brightness = 'settled';
  let showFails = options.showFails ?? null;
  let lightStale = false;
  const posts: Record<string, unknown>[] = [];
  const rangePosts: string[] = [];
  const runs: string[] = [];
  await page.route('**/api/tester/setup-eligibility?**', (route) => json(route, eligibility()));
  await page.route('**/api/tester/status**', (route) =>
    json(route, {
      study: {
        arms: lightStale
          ? [
              {
                arm_id: 'arm5',
                gain_screen: {
                  stale: true,
                  age_s: 120,
                  prompt: 'Measure the light again: this screen was measured in a box you have since moved.',
                },
              },
            ]
          : [],
      },
    })
  );
  await page.route('**/api/tester/attempts?**', (route) => json(route, { scopes: [] }));
  await page.route('**/api/tester/ladder?**', (route) => json(route, { ladder: null, stopped: true }));
  await page.route('**/api/tester/aim-point**', (route) => json(route, { tester_id: TESTER, state: 'needs_patch' }));
  await page.route('**/api/tester/run', (route) => {
    runs.push(route.request().postDataJSON().action as string);
    return json(route, { job: { state: 'running', action: 'preflight', message: 'checking' } }, 202);
  });
  await page.route('**/api/tester/live', (route) =>
    json(route, {
      running: previewRunning,
      arm_id: 'arm5',
      owner: previewRunning ? { kind: 'placement_box', tester_id: TESTER, epoch_id: null, arm_id: 'arm5' } : null,
      stats: previewRunning ? { mean: 90, p99: 200, max: 230, clipped_pct: 0 } : null,
      association: null,
      guided_display: null,
      placement_box: null,
      preview_exposure: previewRunning ? { purpose: 'box_preview', display_only: true, state: brightness } : null,
    })
  );
  await page.route('**/api/tester/live.png?**', (route) =>
    route.fulfill({ status: 200, contentType: 'image/png', body: WIDE_PNG })
  );
  await page.route('**/api/tester/tee-range**', (route) => {
    if (route.request().method() === 'GET') return json(route, { state: range });
    const action = route.request().postDataJSON().action as string;
    if (action === 'show_camera') return json(route, { state: range });
    rangePosts.push(action);
    range = {
      epoch_id: `epoch-${rangePosts.length}`,
      phase: 'needs_camera_arm5',
      reason: 'place_the_ball_in_the_box',
      evidence: {},
      solution: null,
    };
    return json(route, { state: range });
  });
  await page.route('**/api/tester/placement-box**', (route) => {
    if (route.request().method() === 'GET') return json(route, { ...box, previewing: previewRunning });
    const body = route.request().postDataJSON() as Record<string, unknown>;
    posts.push(body);
    if (body.action === 'show') {
      if (showFails) return json(route, { error: showFails }, 409);
      previewRunning = true;
      return json(route, { ...box, previewing: true });
    }
    if (body.action === 'set_net_line') {
      box = {
        ...box,
        net_line: {
          row_px: body.row_px,
          range_m: 4.2,
          status: 'set',
          message: 'Net line set: about 4.2 m beyond the ball.',
        },
      };
      return json(route, { ...box, previewing: previewRunning });
    }
    const moved = box.state === 'confirmed';
    box = placementBoxState(TESTER, 'confirmed', { change: moved ? 'moved' : 'first', net_line: box.net_line });
    previewRunning = false;
    if (moved) {
      lightStale = true;
      range = {
        epoch_id: 'epoch-moved',
        phase: 'needs_camera_arm5',
        reason: 'place_the_ball_in_the_box',
        evidence: {},
        solution: null,
      };
    }
    return json(route, { ...box, previewing: false, change: box.change, range_restarted: moved });
  });
  return {
    posts,
    rangePosts,
    runs,
    setBall: (ball: Record<string, unknown>) => {
      box = { ...box, ball };
    },
    setShowFails: (value: string | null) => {
      showFails = value;
    },
    setRange: (value: Range) => {
      range = value;
    },
    setBrightness: (value: string) => {
      brightness = value;
    },
  };
}

test('the ball box is step 3: its camera opens by itself, the box is fixed, and every check waits for it', async ({
  page,
}) => {
  const mocks = await boxStep(page, {
    ball: foundBall({ state: 'no_ball', center_px: null, diameter_px: null, message: null }),
  });
  await page.goto('/tester.html');

  await expect(page.getByRole('heading', { level: 2, name: '3 Ball in the box' })).toBeVisible();
  await expect(page.locator('#placement-guide')).toContainText(
    'Set the unit 1.8–2.9 m behind the ball, pointing down the target line'
  );
  const overlay = page.locator('#placement-box');
  await expect(overlay).toBeVisible();
  await expect(overlay).toHaveAttribute('data-outline', BOX_OUTLINE_PX.map(([x, y]) => `${x},${y}`).join(' '));
  // nothing to drag but the net line: the box itself takes no pointer
  expect(await page.locator('#placement-outline').evaluate((node) => getComputedStyle(node).pointerEvents)).toBe(
    'none'
  );
  expect(mocks.posts.map((item) => item.action)).toEqual(['show']);
  await expect(page.locator('#placement-ball-status')).toContainText('No ball in the box yet');
  await expect(page.locator('#placement-check')).toHaveAttribute('display', 'none');
  await expect(page.locator('#placement-confirm')).toBeDisabled();
  // the hardware check, the light and the ball range wait for the box
  await expect(page.locator('#step-check')).toBeDisabled();
  await expect(page.locator('#step-light')).toBeDisabled();
  await expect(page.locator('#tee-range-action')).toBeDisabled();
  await expect(page.locator('#suite-box-hint')).toBeVisible();
  await expect(page.locator('#automatic-range-plain')).toHaveText('Ball range: confirm the ball box (step 3) first');

  // the tester puts the ball in the box: a check appears and Confirm opens
  mocks.setBall(foundBall());
  await expect(page.locator('#placement-check')).toHaveAttribute('display', 'inline');
  await expect(page.locator('#placement-ball')).toHaveAttribute('data-state', 'found');
  await expect(page.locator('#placement-ball-status')).toContainText('✓ Ball found in the box');
  await expect(page.locator('#placement-ball-status')).toContainText('2.31 m away (camera and radar agree)');
  await expect(page.locator('#placement-confirm')).toBeEnabled();
  await page.locator('#placement-confirm').click();

  await expect(page.locator('#placement-summary')).toContainText('Ball box confirmed');
  await expect(page.locator('#placement-summary')).toContainText('ball 2.31 m from the unit');
  const confirm = mocks.posts.find((item) => item.action === 'confirm');
  expect(confirm).toBeTruthy();
  expect(confirm?.centre_m).toBeUndefined();
  expect(typeof confirm?.request_id).toBe('string');
  await expect(overlay).toBeHidden();
  await expect(page.locator('#placement-move')).toBeVisible();
  await expect(page.locator('#suite-box-hint')).toBeHidden();
  await expect(page.locator('#step-check')).toBeEnabled();
  await expect(page.locator('#step-light')).toBeEnabled();
  const action = page.locator('#tee-range-action');
  await expect(action).toBeEnabled();
  await action.click();
  await expect(action).toHaveText('Open the camera');
  expect(mocks.rangePosts).toEqual(['start']);
});

for (const [state, text] of [
  ['outside_box', 'The ball is outside the box: move it into the yellow box'],
  ['ambiguous', 'More than one ball-like thing in the box'],
  ['waiting', 'Looking for the ball in the box'],
] as const) {
  test(`a ball that is ${state.replace('_', ' ')} keeps Confirm off and says why`, async ({ page }) => {
    await boxStep(page, { ball: foundBall({ state, message: null, range_m: null, range_status: null }) });
    await page.goto('/tester.html');

    await expect(page.locator('#placement-ball-status')).toContainText(text);
    await expect(page.locator('#placement-ball-status')).toHaveAttribute('data-state', state);
    await expect(page.locator('#placement-check')).toHaveAttribute('display', 'none');
    await expect(page.locator('#placement-confirm')).toBeDisabled();
  });
}

test("the server's plain message for the ball is shown as it is", async ({ page }) => {
  await boxStep(page, { ball: foundBall({ state: 'outside_box', message: 'The ball is 30 cm left of the box.' }) });
  await page.goto('/tester.html');
  await expect(page.locator('#placement-ball-status')).toHaveText('The ball is 30 cm left of the box.');
});

test('a finger drags the net line from where it is, and it is saved when the finger lifts', async ({ page }) => {
  const mocks = await boxStep(page);
  await page.goto('/tester.html');

  const overlay = page.locator('#placement-box');
  await expect(overlay).toBeVisible();
  await expect(page.locator('#placement-net-status')).toHaveText(
    'Drag the blue line to where the floor meets the net or screen.'
  );
  await expect(page.locator('#placement-net')).toHaveAttribute('data-status', 'not_set');
  const startRow = Number(await overlay.getAttribute('data-net-row'));
  const picture = await page.locator('#placement-camera-frame').boundingBox();
  if (!picture) throw new Error('no picture');
  const scale = 800 / picture.height;
  const grab = page.locator('#placement-net-grab');
  // a drag that starts anywhere on the picture moves the line by the finger's travel
  const at = (dy: number) => ({
    pointerId: 3,
    pointerType: 'touch',
    isPrimary: true,
    clientX: picture.x + picture.width / 2,
    clientY: picture.y + picture.height * 0.8 + dy,
    bubbles: true,
  });
  await grab.dispatchEvent('pointerdown', at(0));
  await grab.dispatchEvent('pointermove', at(-30));
  await grab.dispatchEvent('pointermove', at(-60));
  await expect(page.locator('#placement-net')).toHaveAttribute('data-status', 'dragging');
  await grab.dispatchEvent('pointerup', at(-60));

  await expect.poll(() => mocks.posts.filter((item) => item.action === 'set_net_line').length).toBe(1);
  const sent = mocks.posts.find((item) => item.action === 'set_net_line')?.row_px as number;
  expect(Number.isInteger(sent)).toBe(true);
  expect(sent).toBeCloseTo(startRow - 60 * scale, -1);
  await expect(page.locator('#placement-net-status')).toHaveText('Net line set: about 4.2 m beyond the ball.');
  await expect(page.locator('#placement-net')).toHaveAttribute('data-status', 'set');
  // the live view stays up after the line is saved
  await expect(overlay).toBeVisible();
});

test('a tap on the picture puts the net line on the tapped row', async ({ page }) => {
  const mocks = await boxStep(page);
  await page.goto('/tester.html');
  await expect(page.locator('#placement-box')).toBeVisible();
  const picture = await page.locator('#placement-camera-frame').boundingBox();
  if (!picture) throw new Error('no picture');
  const tap = {
    pointerId: 4,
    pointerType: 'touch',
    isPrimary: true,
    clientX: picture.x + picture.width / 2,
    clientY: picture.y + picture.height / 4,
    bubbles: true,
  };
  await page.locator('#placement-net-grab').dispatchEvent('pointerdown', tap);
  await page.locator('#placement-net-grab').dispatchEvent('pointerup', tap);
  await expect.poll(() => mocks.posts.find((item) => item.action === 'set_net_line')?.row_px).toBe(200);
});

test('the preview says it is adjusting its brightness until it settles', async ({ page }) => {
  const mocks = await boxStep(page);
  mocks.setBrightness('adjusting');
  await page.goto('/tester.html');

  const note = page.locator('#placement-brightness');
  await expect(note).toBeVisible();
  await expect(note).toHaveText('Adjusting brightness…');
  mocks.setBrightness('settled');
  await expect(note).toBeHidden();
  await expect(page.locator('#placement-box')).toBeVisible();
});

test('a camera that does not answer is the first camera check, said plainly', async ({ page }) => {
  const mocks = await boxStep(page, { showFails: 'no camera found on the CSI port' });
  await page.goto('/tester.html');

  const summary = page.locator('#placement-summary');
  await expect(summary).toContainText('The camera did not start: no camera found on the CSI port');
  await expect(summary).toHaveClass('problem');
  const show = page.locator('#placement-show');
  await expect(show).toBeVisible();
  await expect(page.locator('#step-check')).toBeDisabled();

  mocks.setShowFails(null);
  await show.click();

  await expect(page.locator('#placement-box')).toBeVisible();
  await expect(show).toBeHidden();
  await expect(summary).toContainText('Put the ball in the yellow box and confirm when the check is green');
});

test('redoing the box later makes the light stale and starts the ball range over', async ({ page }) => {
  const mocks = await boxStep(page, { confirmed: true });
  mocks.setRange({
    epoch_id: 'epoch-done',
    phase: 'raw_only',
    reason: 'raw_only',
    evidence: {},
    solution: { status: 'unresolved', selected_range_m: null },
  });
  await page.goto('/tester.html');
  await expect(page.locator('#tee-range-action')).toHaveText('Setup saved — no ball distance');
  // a confirmed box opens no camera by itself
  await expect(page.locator('#placement-box')).toBeHidden();
  expect(mocks.posts).toEqual([]);

  await page.locator('#placement-move').click();
  await expect(page.locator('#placement-box')).toBeVisible();
  await page.locator('#placement-confirm').click();

  await expect(page.locator('#placement-summary')).toContainText('The ball box moved');
  await expect(page.locator('#placement-summary')).toContainText('the ball range has started over');
  await expect(page.locator('#tee-range-action')).toHaveText('Open the camera');
  await expect(page.locator('#light-age')).toContainText('Measure the light again');
  expect(mocks.posts.map((item) => item.action)).toEqual(['show', 'confirm']);
});

for (const viewport of [
  { width: 800, height: 480 },
  { width: 1024, height: 600 },
]) {
  test(`the live view, box and check are fully visible on arrival at ${viewport.width}x${viewport.height}`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    await boxStep(page);
    await page.goto('/tester.html');
    const picture = page.locator('#placement-camera-frame');
    await expect(picture).toBeVisible();
    await expect(page.locator('#placement-check')).toHaveAttribute('display', 'inline');
    // on arrival, with no scrolling: the whole live view, box and check sit under the sticky bars
    await page.waitForTimeout(300);
    const rect = await rectOf(page, '#placement-camera-frame');
    const bars = await rectOf(page, '#sticky-top');
    if (!rect || !bars) throw new Error('nothing drawn');
    expect(rect.y).toBeGreaterThanOrEqual(bars.y + bars.height - 1);
    expect(rect.y + rect.height).toBeLessThanOrEqual(viewport.height + 1);
    expect(rect.x).toBeGreaterThanOrEqual(0);
    expect(rect.x + rect.width).toBeLessThanOrEqual(viewport.width);
    for (const id of ['#placement-outline', '#placement-check']) {
      const part = await rectOf(page, id);
      expect(part, id).not.toBeNull();
      expect(part!.y, id).toBeGreaterThanOrEqual(bars.y + bars.height - 1);
      expect(part!.y + part!.height, id).toBeLessThanOrEqual(viewport.height + 1);
    }
    // as large as fits: within a few pixels of the room under the bars, or the full width
    const room = viewport.height - (bars.y + bars.height);
    expect(rect.height >= room - 24 || rect.width >= viewport.width - 120).toBe(true);
    expect(rect.width).toBeGreaterThan(viewport.height * 0.44 * 1.6 + 20);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    for (const id of ['#placement-confirm']) {
      const button = await page.locator(id).boundingBox();
      expect(button?.height ?? 0).toBeGreaterThanOrEqual(44);
    }
  });
}
