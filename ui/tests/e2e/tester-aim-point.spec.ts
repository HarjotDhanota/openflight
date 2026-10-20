import { expect, test, type Page, type Route } from '@playwright/test';
import { mockConfirmedPlacementBox, PATCH_PROJECTION, rectOf, useTester } from './helpers';

// D34 (13 Oct pass): right after the ball box, the tester drags the aim point onto the
// target in the live 1280×800 view, drawn as TrackMan's vertical yellow line with an
// open reticle that never hides the target. A full-screen view with a 2–4× magnifier
// finds a far flag. The floor-line method is retired from the page; the only ± shown
// is the server's total_uncertainty_deg, and the page sends display_scale (frame px per
// screen px). Skipping is allowed: swings are then measured from where the unit points.
test.use({ hasTouch: true });

test.beforeEach(async ({ page }) => {
  await useTester(page);
});

const TESTER = '20260922-name';
const WIDE_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAABAAAAAKCAAAAACY1YFAAAAAD0lEQVR4nGPQQAMMg1UAAE4iGQEMndODAAAAAElFTkSuQmCC',
  'base64'
);
const NOT_STALE = { stale: false, reason: null, handed_yaw_deg: null };

async function json(route: Route, payload: object, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(payload) });
}

function eligibility() {
  return {
    schema_version: 1,
    tester_id: TESTER,
    config_hash: 'measured-v3-hash',
    eligible: true,
    stage: 'tester_admission',
    checks: [],
    blockers: [],
    operator_confirmation: {
      confirmed: true,
      confirmed_at: '2026-09-24T01:00:00Z',
      config_hash: 'measured-v3-hash',
      authority: 'operator_physical_setup',
    },
    runtime_requirements: ['ops', 'camera', 'iwr6843', 'lis3dh'],
  };
}

function aimState(state: string, overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    tester_id: TESTER,
    state,
    previewing: false,
    setup_blockers: [],
    runs: { arm5: NOT_STALE, arm6: NOT_STALE },
    projection: PATCH_PROJECTION,
    camera_tilt: { status: 'uncalibrated' },
    ...(state === 'needs_confirmation'
      ? { reason: 'not_set', aim: { aim_px: [640, 400], source: 'default_straight_ahead' } }
      : {}),
    ...overrides,
  };
}

function confirmedLine(aim: number[], yaw = 2.4) {
  return {
    source: 'aim_point',
    aim_px: aim,
    method: 'direction',
    yaw_deg: yaw,
    kiosk_offset_deg: -yaw,
    // an old client's own spread: never shown now
    uncertainty: { total_deg: 0.3 },
    warnings: [],
  };
}

type AimOptions = { initial?: Record<string, unknown>; range?: Record<string, unknown>; liveOwner?: string };

async function aimStep(page: Page, options: AimOptions = {}) {
  let state: Record<string, unknown> = options.initial ?? aimState('needs_confirmation');
  let previewRunning = false;
  const posts: Record<string, unknown>[] = [];
  await mockConfirmedPlacementBox(page);
  await page.route('**/api/tester/setup-eligibility?**', (route) => json(route, eligibility()));
  await page.route('**/api/tester/tee-range?**', (route) =>
    json(route, {
      state: options.range ?? {
        epoch_id: 'fixture-range',
        phase: 'raw_only',
        reason: 'qualification_artifact_missing',
        evidence: {},
        solution: { status: 'unresolved', selected_range_m: null },
      },
    })
  );
  await page.route('**/api/tester/status**', (route) => json(route, {}));
  await page.route('**/api/tester/attempts?**', (route) => json(route, { schema_version: 1, scopes: [] }));
  await page.route('**/api/tester/ladder?**', (route) =>
    json(route, { ladder: null, stopped: true, photo_target: null })
  );
  await page.route('**/api/tester/live', (route) => {
    const owner = options.liveOwner ?? (previewRunning ? 'aim_point' : null);
    return json(route, {
      running: Boolean(owner),
      arm_id: 'arm5',
      owner: owner ? { kind: owner, tester_id: TESTER, epoch_id: options.range?.epoch_id ?? null, arm_id: 'arm5' } : null,
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
  await page.route('**/api/tester/aim-point**', (route) => {
    if (route.request().method() === 'GET') return json(route, { ...state, previewing: previewRunning });
    const body = route.request().postDataJSON() as Record<string, unknown>;
    posts.push(body);
    if (body.action === 'show') {
      previewRunning = true;
      return json(route, { ...state, previewing: true });
    }
    if (body.action === 'skip') {
      state = aimState('skipped');
      previewRunning = false;
      return json(route, { ...state, previewing: false });
    }
    const aim = body.aim_px as number[];
    const before = state.state === 'confirmed' ? (state.target_line as { aim_px: number[] }) : null;
    const moved = before !== null && Math.hypot(aim[0] - before.aim_px[0], aim[1] - before.aim_px[1]) > 4;
    const change = before === null ? 'first' : moved ? 'moved' : 'unchanged';
    state = aimState('confirmed', {
      target_line: change === 'unchanged' ? before : confirmedLine(aim, moved ? 5.1 : 2.4),
      total_uncertainty_deg: 0.8,
      confirmed_at_utc: '2026-09-30T18:05:00+00:00',
      change,
      runs: moved
        ? {
            arm5: {
              stale: true,
              reason: 'used an aim point you have since moved (yaw +2.4°, now +5.1°)',
              handed_yaw_deg: 2.4,
            },
            arm6: NOT_STALE,
          }
        : { arm5: NOT_STALE, arm6: NOT_STALE },
    });
    previewRunning = false;
    return json(route, { ...state, previewing: false, change });
  });
  return { posts };
}

// where a 1280×800 frame pixel lands on the screen, in the step's picture or the full-screen one
async function screenPoint(page: Page, x: number, y: number, pictureId = 'aim-camera-frame') {
  await expect(page.locator(`#${pictureId}`)).toBeVisible();
  // each new frame replaces the picture element, so it is found and measured in one step
  const picture = await page.evaluate((id) => {
    const frame = document.getElementById(id) as HTMLElement;
    if (id === 'aim-camera-frame') frame.scrollIntoView({ block: 'center' });
    const rect = frame.getBoundingClientRect();
    return { x: rect.left, y: rect.top, width: rect.width, height: rect.height };
  }, pictureId);
  if (!picture.width) throw new Error('the picture has no size');
  return { x: picture.x + (x * picture.width) / 1280, y: picture.y + (y * picture.height) / 800 };
}

const aimOf = async (page: Page, overlay = '#aim-overlay') =>
  ((await page.locator(overlay).getAttribute('data-aim')) ?? '').split(',').map(Number);

test('right after the box the aim step opens its camera; the dragged yellow line is confirmed', async ({ page }) => {
  const mocks = await aimStep(page);
  await page.goto('/tester.html');

  // the aim step sits after the ball box and before the light (19 Oct: the unit check is step 2)
  const order = await page.evaluate(() => {
    const at = (id: string) => document.getElementById(id) as HTMLElement;
    const follows = (a: string, b: string) =>
      Boolean(at(a).compareDocumentPosition(at(b)) & Node.DOCUMENT_POSITION_FOLLOWING);
    return [follows('placement', 'aim'), follows('aim', 'step-light')];
  });
  expect(order).toEqual([true, true]);
  const overlay = page.locator('#aim-overlay');
  await expect(overlay).toBeVisible();
  expect(mocks.posts.map((item) => item.action)).toEqual(['show']);
  await expect(overlay).toHaveAttribute('data-aim', '640.0,400.0');
  await expect(page.locator('#aim-yaw')).toContainText('straight down where the unit points');
  await expect(page.locator('#aim-summary')).toContainText('Drag the yellow line onto your target');

  // a mouse drag that starts on the reticle carries it, with no jump
  const start = await screenPoint(page, 640, 400);
  const end = await screenPoint(page, 760, 380);
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(end.x, end.y, { steps: 5 });
  await page.mouse.up();
  const [x, y] = await aimOf(page);
  expect(x).toBeCloseTo(760, -1);
  expect(y).toBeCloseTo(380, -1);
  // atan(120 / 933.3) is 7.3°, right
  expect(Number(await overlay.getAttribute('data-yaw'))).toBeCloseTo((Math.atan((x - 640) / 933.3334) * 180) / Math.PI, 1);
  await expect(page.locator('#aim-yaw')).toContainText('right of where the unit points');
  // TrackMan's yellow line: vertical, top to bottom of the picture, through the aim point
  const line = page.locator('#aim-line');
  expect(Number(await line.getAttribute('x1'))).toBeCloseTo(x, 0);
  expect(Number(await line.getAttribute('x2'))).toBeCloseTo(x, 0);
  expect(Number(await line.getAttribute('y1'))).toBe(0);
  expect(Number(await line.getAttribute('y2'))).toBe(800);
  // an open reticle: a ring and four ticks, none of them crossing the aim point
  const ticks = await page.locator('#aim-handle .mark line').evaluateAll((nodes) =>
    nodes.map((node) => ['x1', 'y1', 'x2', 'y2'].map((name) => Number(node.getAttribute(name))))
  );
  expect(ticks).toHaveLength(4);
  for (const [x1, y1, x2, y2] of ticks) {
    expect(Math.min(Math.hypot(x1 - x, y1 - y), Math.hypot(x2 - x, y2 - y))).toBeGreaterThan(10);
  }

  const picture = await page.locator('#aim-stage').boundingBox();
  await page.locator('#aim-confirm').click();

  const confirm = mocks.posts.find((item) => item.action === 'confirm');
  const sent = confirm?.aim_px as number[];
  expect(sent[0]).toBeCloseTo(x, 0);
  expect(sent[1]).toBeCloseTo(y, 0);
  expect('floor_line_px' in (confirm ?? {})).toBe(false);
  // frame px per screen px of the picture the point was set on
  expect(confirm?.display_scale as number).toBeCloseTo(1280 / (picture?.width ?? 1), 2);
  expect(typeof confirm?.request_id).toBe('string');
  const summary = page.locator('#aim-summary');
  await expect(summary).toContainText('Aim point confirmed');
  // the server's total uncertainty is the only ±; the old line spread is never shown
  await expect(summary).toContainText('2.4° right of where the unit points (±0.8°)');
  await expect(summary).not.toContainText('±0.3');
  await expect(overlay).toBeHidden();
  await expect(page.locator('#aim')).toHaveAttribute('data-ready', 'true');
  await expect(page.locator('#aim-move')).toBeVisible();
});

for (const viewport of [
  { width: 1024, height: 600 },
  { width: 800, height: 480 },
]) {
  test(`the aim picture and its line are fully visible on arrival at ${viewport.width}x${viewport.height}`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    await aimStep(page);
    await page.goto('/tester.html');
    await expect(page.locator('#aim-overlay')).toHaveAttribute('data-aim', '640.0,400.0');
    await page.waitForTimeout(300);
    const rect = await rectOf(page, '#aim-camera-frame');
    const bars = await rectOf(page, '#sticky-top');
    if (!rect || !bars) throw new Error('nothing drawn');
    expect(rect.y).toBeGreaterThanOrEqual(bars.y + bars.height - 1);
    expect(rect.y + rect.height).toBeLessThanOrEqual(viewport.height + 1);
    expect(rect.x + rect.width).toBeLessThanOrEqual(viewport.width);
    const room = viewport.height - (bars.y + bars.height);
    expect(rect.height >= room - 24 || rect.width >= viewport.width - 120).toBe(true);
  });
}

test('a finger away from the reticle puts it above the fingertip; near it, it drags without a jump', async ({
  page,
}) => {
  await aimStep(page);
  await page.goto('/tester.html');
  const surface = page.locator('#aim-surface');
  await expect(page.locator('#aim-overlay')).toHaveAttribute('data-aim', '640.0,400.0');
  const touch = async (kind: string, point: { x: number; y: number }) =>
    surface.dispatchEvent(kind, {
      pointerId: 9,
      pointerType: 'touch',
      isPrimary: true,
      clientX: point.x,
      clientY: point.y,
      bubbles: true,
    });

  // far from the reticle: it lands 64 screen px above the finger, so the target stays in sight
  const finger = await screenPoint(page, 400, 600);
  await touch('pointerdown', finger);
  await touch('pointerup', finger);
  const picture = await page.locator('#aim-stage').boundingBox();
  const k = 1280 / (picture?.width ?? 1);
  const [x1, y1] = await aimOf(page);
  expect(x1).toBeCloseTo(400, -1);
  expect(y1).toBeCloseTo(600 - 64 * k, -1);
  await expect(page.locator('#aim-yaw')).toContainText('left of where the unit points');

  // a finger just below the reticle grabs it: it moves with the finger, keeping its offset
  const near = await screenPoint(page, x1, y1 + 40 * k);
  await touch('pointerdown', near);
  await touch('pointermove', { x: near.x + 30, y: near.y });
  await touch('pointerup', { x: near.x + 30, y: near.y });
  const [x2, y2] = await aimOf(page);
  expect(x2).toBeCloseTo(x1 + 30 * k, -1);
  expect(y2).toBeCloseTo(y1, 0);
});

test('the floor-line method is retired from the page', async ({ page }) => {
  const mocks = await aimStep(page);
  await page.goto('/tester.html');
  await expect(page.locator('#aim-overlay')).toBeVisible();
  await expect(page.locator('#aim-floor-toggle')).toHaveCount(0);
  await expect(page.getByText('Mark where the floor meets the screen')).toHaveCount(0);
  await page.locator('#aim-confirm').click();
  await expect(page.locator('#aim-summary')).toContainText('Aim point confirmed');
  const confirm = mocks.posts.find((item) => item.action === 'confirm');
  expect(confirm && 'floor_line_px' in confirm).toBe(false);
});

for (const viewport of [
  { width: 800, height: 480 },
  { width: 1024, height: 600 },
]) {
  test(`full screen with a magnifier finds a far target at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const mocks = await aimStep(page);
    await page.goto('/tester.html');
    await expect(page.locator('#aim-overlay')).toBeVisible();
    const open = page.locator('#aim-fullscreen');
    await expect(open).toBeVisible();
    await open.click();

    const full = page.locator('#aim-full');
    await expect(full).toBeVisible();
    // the picture fills the width or the height of the screen above the controls
    const stage = await page.locator('#aim-full-stage').boundingBox();
    if (!stage) throw new Error('no full-screen picture');
    expect(stage.width >= viewport.width - 2 || stage.height >= viewport.height * 0.6).toBe(true);
    const loupe = page.locator('#aim-loupe');
    await expect(loupe).toBeVisible();
    await expect(loupe).toHaveAttribute('data-zoom', '3');
    await page.getByRole('button', { name: '4×' }).click();
    await expect(loupe).toHaveAttribute('data-zoom', '4');
    await expect(page.getByRole('button', { name: '4×' })).toHaveAttribute('aria-pressed', 'true');

    // a finger drag in the full-screen picture moves the point there too
    const surface = page.locator('#aim-full-surface');
    const from = await screenPoint(page, 640, 400, 'aim-full-frame');
    const step = { pointerId: 5, pointerType: 'mouse', isPrimary: true, bubbles: true };
    await surface.dispatchEvent('pointerdown', { ...step, clientX: from.x, clientY: from.y });
    await surface.dispatchEvent('pointermove', { ...step, clientX: from.x + 50, clientY: from.y });
    await surface.dispatchEvent('pointerup', { ...step, clientX: from.x + 50, clientY: from.y });
    const [moved, movedY] = await aimOf(page, '#aim-full-overlay');
    expect(moved).toBeGreaterThan(660);

    // one camera pixel at a time, by eye in the magnifier
    await page.getByRole('button', { name: 'Nudge right' }).click();
    await page.getByRole('button', { name: 'Nudge right' }).click();
    await page.getByRole('button', { name: 'Nudge up' }).click();
    const [nx, ny] = await aimOf(page, '#aim-full-overlay');
    expect(nx).toBeCloseTo(moved + 2, 1);
    expect(ny).toBeCloseTo(movedY - 1, 1);
    for (const name of ['Nudge left', 'Nudge right', 'Confirm the aim point', 'Close']) {
      const box = await full.getByRole('button', { name, exact: true }).boundingBox();
      expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
      expect(box?.width ?? 0).toBeGreaterThanOrEqual(44);
    }

    await full.getByRole('button', { name: 'Confirm the aim point' }).click();
    await expect(full).toBeHidden();
    const confirm = mocks.posts.find((item) => item.action === 'confirm');
    expect((confirm?.aim_px as number[])[0]).toBeCloseTo(nx, 0);
    // set by eye in the 4× magnifier: a quarter of a camera pixel per screen pixel
    expect(confirm?.display_scale).toBe(0.25);
    await expect(page.locator('#aim-summary')).toContainText('Aim point confirmed');
  });
}

test('skip for now: no red error, swings are measured from where the unit points, and it can be set later', async ({
  page,
}) => {
  const mocks = await aimStep(page);
  await page.goto('/tester.html');
  const skip = page.locator('#aim-skip');
  await expect(skip).toBeVisible();
  await expect(skip).toHaveText('Skip for now (left/right from where the unit points)');
  await skip.click();

  expect(mocks.posts.map((item) => item.action)).toEqual(['show', 'skip']);
  const summary = page.locator('#aim-summary');
  await expect(summary).toContainText('Skipped for now: left and right are measured from where the unit points');
  await expect(summary).toHaveClass('note');
  await expect(page.locator('#aim')).toHaveAttribute('data-ready', 'true');
  await expect(page.locator('#aim')).toHaveAttribute('data-state', 'skipped');
  await expect(page.locator('#step-4')).toHaveAttribute('data-state', 'done');
  await expect(page.locator('#aim-overlay')).toBeHidden();
  await expect(skip).toBeHidden();
  const later = page.locator('#aim-move');
  await expect(later).toHaveText('Set the aim point now');
  await later.click();
  await expect(page.locator('#aim-overlay')).toBeVisible();
  await expect(page.locator('#aim-confirm')).toBeVisible();
});

test('the aim step never takes the camera from the ball range, and says so without an error', async ({ page }) => {
  const mocks = await aimStep(page, {
    range: { epoch_id: 'epoch-busy', phase: 'camera_arm5_capturing', reason: 'camera_arm5_warming', evidence: {}, solution: null },
    liveOwner: 'guided_tee_range',
  });
  await page.goto('/tester.html');
  await expect(page.locator('#aim-preview')).toBeVisible();
  const status = page.locator('#aim-camera-status');
  await expect(status).toContainText('The camera is busy with the ball range (step 6)');
  await expect(status).toHaveClass('note');
  await expect(page.locator('#aim-summary')).not.toHaveClass('problem');
  await expect(page.locator('#aim-skip')).toBeVisible();
  await page.waitForTimeout(2500);
  expect(mocks.posts.filter((item) => item.action === 'show')).toEqual([]);
});

test('the aim point starts where it was last confirmed when the box moves', async ({ page }) => {
  await aimStep(page, {
    initial: aimState('needs_confirmation', {
      reason: 'patch_moved',
      aim: { aim_px: [700, 380], source: 'last_confirmed' },
    }),
  });
  await page.goto('/tester.html');

  await expect(page.locator('#aim-overlay')).toHaveAttribute('data-aim', '700.0,380.0');
  await expect(page.locator('#aim-summary')).toContainText('The ball box moved, so confirm the aim point again');
});

test('the same aim again changes nothing; moving it marks swings taken before as stale', async ({ page }) => {
  const mocks = await aimStep(page, {
    initial: aimState('confirmed', {
      target_line: confirmedLine([700, 380]),
      total_uncertainty_deg: 0.8,
      confirmed_at_utc: '2026-09-30T18:00:00+00:00',
    }),
  });
  await page.goto('/tester.html');
  await expect(page.locator('#aim-summary')).toContainText('Aim point confirmed');
  // a confirmed aim point opens no camera by itself
  await expect(page.locator('#aim-overlay')).toBeHidden();
  expect(mocks.posts).toEqual([]);

  await page.locator('#aim-move').click();
  await expect(page.locator('#aim-overlay')).toHaveAttribute('data-aim', '700.0,380.0');
  await page.locator('#aim-confirm').click();
  await expect(page.locator('#aim-summary')).toContainText('Same aim point as before: nothing changed.');
  await expect(page.locator('#aim-stale')).toBeHidden();

  await page.locator('#aim-move').click();
  await expect(page.locator('#aim-overlay')).toBeVisible();
  const from = await screenPoint(page, 700, 380);
  const to = await screenPoint(page, 800, 380);
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(to.x, to.y, { steps: 4 });
  await page.mouse.up();
  await page.locator('#aim-confirm').click();

  await expect(page.locator('#aim-summary')).toContainText('The aim point moved');
  const stale = page.locator('#aim-stale');
  await expect(stale).toBeVisible();
  await expect(stale).toContainText('Swings in 1280×800 used an aim point you have since moved');
  expect(mocks.posts.map((item) => item.action)).toEqual(['show', 'confirm', 'show', 'confirm']);
});
