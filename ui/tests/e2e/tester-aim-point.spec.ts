import { expect, test, type Page, type Route } from '@playwright/test';
import { mockConfirmedPlacementBox, PATCH_PROJECTION } from './helpers';

// D34: right after the patch, the tester drags an aim point onto the target in the
// live 1280×800 view (TrackMan's "Target Image"), optionally marks where the floor
// meets the screen, sees the target line from the patch through it and its yaw, and
// confirms it; the server hands that yaw to both kiosk yaw flags.
test.use({ hasTouch: true });

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
    patch: {
      centre_lfu_m: [0, 1.2486],
      centre_px: [640, 471.0],
      outline_px: [
        [338.3, 494.0],
        [941.7, 494.0],
        [823.2, 457.1],
        [456.8, 457.1],
      ],
      frame_size_px: [1280, 800],
    },
    ...(state === 'needs_confirmation'
      ? { reason: 'not_set', aim: { aim_px: [640, 400], floor_line_px: null, source: 'default_straight_ahead' } }
      : {}),
    ...overrides,
  };
}

function confirmedLine(aim: number[], floor: number[][] | null = null, yaw = 2.4) {
  return {
    source: 'aim_point',
    aim_px: aim,
    floor_line_px: floor,
    method: floor ? 'floor_point' : 'direction',
    yaw_deg: yaw,
    kiosk_offset_deg: -yaw,
    uncertainty: { total_deg: 0.3 },
    warnings: [],
  };
}

type AimOptions = { initial?: Record<string, unknown> };

async function aimStep(page: Page, options: AimOptions = {}) {
  let state: Record<string, unknown> = options.initial ?? aimState('needs_confirmation');
  let previewRunning = false;
  const posts: Record<string, unknown>[] = [];
  await mockConfirmedPlacementBox(page);
  await page.route('**/api/tester/setup-eligibility?**', (route) => json(route, eligibility()));
  await page.route('**/api/tester/tee-range?**', (route) =>
    json(route, {
      state: {
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
  await page.route('**/api/tester/live', (route) =>
    json(route, {
      running: previewRunning,
      arm_id: 'arm5',
      owner: previewRunning ? { kind: 'aim_point', tester_id: TESTER, epoch_id: null, arm_id: 'arm5' } : null,
      stats: previewRunning ? { mean: 90, p99: 200, max: 230, clipped_pct: 0 } : null,
      association: null,
      guided_display: null,
      placement_box: null,
      preview_exposure: previewRunning ? { purpose: 'box_preview', display_only: true, state: 'settled' } : null,
    })
  );
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
    const aim = body.aim_px as number[];
    const floor = (body.floor_line_px as number[][] | null) ?? null;
    const before = state.state === 'confirmed' ? (state.target_line as { aim_px: number[] }) : null;
    const moved = before !== null && Math.hypot(aim[0] - before.aim_px[0], aim[1] - before.aim_px[1]) > 4;
    const change = before === null ? 'first' : moved ? 'moved' : 'unchanged';
    state = aimState('confirmed', {
      target_line: change === 'unchanged' ? before : confirmedLine(aim, floor, moved ? 5.1 : 2.4),
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

// where a 1280×800 frame pixel lands on the screen
async function screenPoint(page: Page, x: number, y: number) {
  await expect(page.locator('#aim-camera-frame')).toBeVisible();
  // each new frame replaces the picture element, so it is found and measured in one step
  const picture = await page.evaluate(() => {
    const frame = document.getElementById('aim-camera-frame') as HTMLElement;
    frame.scrollIntoView({ block: 'center' });
    const rect = frame.getBoundingClientRect();
    return { x: rect.left, y: rect.top, width: rect.width, height: rect.height };
  });
  if (!picture.width) throw new Error('the picture has no size');
  return { x: picture.x + (x * picture.width) / 1280, y: picture.y + (y * picture.height) / 800 };
}

const aimOf = async (page: Page) =>
  ((await page.locator('#aim-overlay').getAttribute('data-aim')) ?? '').split(',').map(Number);

test('right after the patch the aim step opens its camera, and a dragged aim point is confirmed', async ({ page }) => {
  const mocks = await aimStep(page);
  await page.goto('/tester.html');

  // the aim step sits after the patch and before A, the hardware check
  const order = await page.evaluate(() => {
    const at = (id: string) => document.getElementById(id) as HTMLElement;
    const follows = (a: string, b: string) =>
      Boolean(at(a).compareDocumentPosition(at(b)) & Node.DOCUMENT_POSITION_FOLLOWING);
    return [follows('placement', 'aim'), follows('aim', 'step-check')];
  });
  expect(order).toEqual([true, true]);
  const overlay = page.locator('#aim-overlay');
  await expect(overlay).toBeVisible();
  expect(mocks.posts.map((item) => item.action)).toEqual(['show']);
  await expect(overlay).toHaveAttribute('data-aim', '640.0,400.0');
  await expect(page.locator('#aim-yaw')).toContainText('straight down where the unit points');
  await expect(page.locator('#aim-summary')).toContainText('Tap or drag the red cross onto your target');

  // a mouse drag carries the aim point to the right of the picture's middle
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
  expect(Number(await overlay.getAttribute('data-yaw'))).toBeCloseTo(
    (Math.atan((x - 640) / 933.3334) * 180) / Math.PI,
    1
  );
  await expect(page.locator('#aim-yaw')).toContainText('right of where the unit points');
  await expect(overlay).toHaveAttribute('data-method', 'direction');
  // the target line runs from the patch centre through the aim point
  const line = page.locator('#aim-line');
  expect(Number(await line.getAttribute('x1'))).toBeCloseTo(640, 0);
  expect(Number(await line.getAttribute('y1'))).toBeCloseTo(471, 0);
  expect(Number(await line.getAttribute('x2'))).toBeGreaterThan(x);

  await page.locator('#aim-confirm').click();

  const confirm = mocks.posts.find((item) => item.action === 'confirm');
  const sent = confirm?.aim_px as number[];
  expect(sent[0]).toBeCloseTo(x, 0);
  expect(sent[1]).toBeCloseTo(y, 0);
  expect(confirm?.floor_line_px).toBeNull();
  expect(typeof confirm?.request_id).toBe('string');
  const summary = page.locator('#aim-summary');
  await expect(summary).toContainText('Aim point confirmed');
  await expect(summary).toContainText('2.4° right of where the unit points (±0.3°)');
  await expect(summary).toContainText('the aim taken as a direction');
  await expect(overlay).toBeHidden();
  await expect(page.locator('#aim')).toHaveAttribute('data-ready', 'true');
  await expect(page.locator('#aim-move')).toBeVisible();
});

test('a finger taps the aim point and drags the floor line onto the screen', async ({ page }) => {
  const mocks = await aimStep(page);
  await page.goto('/tester.html');
  const surface = page.locator('#aim-surface');
  await expect(page.locator('#aim-overlay')).toBeVisible();
  const touch = async (node: typeof surface, kind: string, x: number, y: number) => {
    const point = await screenPoint(page, x, y);
    await node.dispatchEvent(kind, {
      pointerId: 9,
      pointerType: 'touch',
      isPrimary: true,
      clientX: point.x,
      clientY: point.y,
      bubbles: true,
    });
  };

  await touch(surface, 'pointerdown', 560, 300);
  await touch(surface, 'pointermove', 540, 310);
  await touch(surface, 'pointerup', 540, 310);

  const [x] = await aimOf(page);
  expect(x).toBeCloseTo(540, -1);
  await expect(page.locator('#aim-yaw')).toContainText('left of where the unit points');

  const toggle = page.locator('#aim-floor-toggle');
  await expect(toggle).toHaveText('Mark where the floor meets the screen');
  await toggle.click();
  const end = page.locator('#aim-floor-a');
  await expect(end).toHaveAttribute('display', 'inline');
  await expect(toggle).toHaveText('Remove the floor line');
  // the floor meets the screen 3 m out: 30 rows under the horizon at 95 mm
  await touch(end, 'pointerdown', 320, 430);
  await touch(end, 'pointermove', 300, 430);
  await touch(end, 'pointerup', 300, 430);
  await expect(page.locator('#aim-overlay')).toHaveAttribute('data-method', 'floor_point');
  await expect(page.locator('#aim-yaw')).toContainText('from the ball to the screen');

  await page.locator('#aim-confirm').click();

  const confirm = mocks.posts.find((item) => item.action === 'confirm');
  const floor = confirm?.floor_line_px as number[][];
  expect(floor).toHaveLength(2);
  expect(floor[0][0]).toBeCloseTo(300, -1);
  await expect(page.locator('#aim-summary')).toContainText('from the ball to the screen');
});

test('the aim point starts where it was last confirmed when the patch moves', async ({ page }) => {
  await aimStep(page, {
    initial: aimState('needs_confirmation', {
      reason: 'patch_moved',
      aim: { aim_px: [700, 380], floor_line_px: null, source: 'last_confirmed' },
    }),
  });
  await page.goto('/tester.html');

  await expect(page.locator('#aim-overlay')).toHaveAttribute('data-aim', '700.0,380.0');
  await expect(page.locator('#aim-summary')).toContainText('The patch moved, so confirm the aim point again');
});

test('the same aim again changes nothing; moving it marks swings taken before as stale', async ({ page }) => {
  const mocks = await aimStep(page, {
    initial: aimState('confirmed', {
      target_line: confirmedLine([700, 380]),
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
