import { readFileSync } from 'node:fs';
import { expect, test, type Page, type Route } from '@playwright/test';
import { keepManualLadderOpen, mockConfirmedPlacementBox, useTester } from './helpers';

// 13 Oct UX pass, step 7: the per-shot verdict as a big card at the top (mirrored on the
// band), live numbers while the ladder runs, “Ready for a swing” from the ball at
// address, the kiosk in a normal window with visible mode-switch progress, the ladder in
// its own order with plain labels, and the kiosk's numbers signed left/right.
test.use({ hasTouch: true });

test.beforeEach(async ({ page }) => {
  // the manual ladder's controls sit in the closed maintainer section
  await keepManualLadderOpen(page);
  await useTester(page);
});

async function json(route: Route, payload: object, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(payload) });
}

const RUNG_IDS = [
  'full-300',
  'full-200',
  'full-150',
  'full-100',
  'full-75',
  'full-50',
  'full-30',
  'full-20',
  'full-10',
  'half-300',
  'half-150',
  'half-75',
  'half-30',
  'half-15',
];

function ladder(current: string | null, statuses: Record<string, string>, verdict: object | null, stopped = false) {
  // the server sends its rungs in any order: alphabetical here, as the audit saw
  const rungs = Object.fromEntries(
    [...RUNG_IDS]
      .sort()
      .map((id) => [
        id,
        {
          status: statuses[id] ?? 'pending',
          swings: statuses[id] === 'done' ? Array(5).fill({ color: 'green' }) : [],
          reason: null,
        },
      ])
  );
  return { ladder: { current, pending_photo: null, rungs }, last_verdict: verdict, photo_target: null, stopped };
}

type Status = Record<string, unknown>;

async function swingStep(page: Page, readLadder: () => object, readStatus: () => Status = () => ({})) {
  await mockConfirmedPlacementBox(page);
  await page.route('**/api/tester/tee-range?**', (route) =>
    json(route, {
      state: {
        epoch_id: 'fixture-range',
        phase: 'resolved',
        reason: 'resolved',
        evidence: {},
        solution: { status: 'resolved', selected_range_m: 2.31 },
      },
      display: { canonical: { state: 'resolved', range_m: 2.31 } },
    })
  );
  await page.route('**/api/tester/setup-eligibility?**', (route) =>
    json(route, {
      schema_version: 1,
      tester_id: '20260922-name',
      config_hash: 'h',
      eligible: true,
      checks: [],
      blockers: [],
      operator_confirmation: { confirmed: true, confirmed_at: 'now', config_hash: 'h', authority: 'operator' },
      runtime_requirements: [],
    })
  );
  await page.route('**/api/tester/status**', (route) => json(route, readStatus()));
  await page.route('**/api/tester/attempts?**', (route) => json(route, { schema_version: 1, scopes: [] }));
  await page.route('**/api/tester/ladder?**', (route) => json(route, readLadder()));
  await page.route('**/api/tester/aim-point**', (route) =>
    json(route, {
      tester_id: '20260922-name',
      state: 'confirmed',
      target_line: { yaw_deg: 2.1 },
      total_uncertainty_deg: 0.8,
    })
  );
  await page.route('**/api/camera/preview.jpg**', (route) => route.fulfill({ status: 204 }));
}

const RED = {
  color: 'red',
  rung_id: 'full-150',
  capture: 'camera_1',
  reasons: [
    'light: the ball zone is too dark (signal 6 DN above black)',
    'resting ball not found in the pre-impact frames',
  ],
  delivered_fps: 118.4,
  signal_dn: 6.2,
  clipped_pct: 0.4,
};

test('the advanced ladder keeps its verdict card and mirrors its colour on the band', async ({ page }) => {
  let verdict: object = RED;
  await swingStep(page, () =>
    ladder('full-150', { 'full-300': 'done', 'full-200': 'done', 'full-150': 'active' }, verdict)
  );
  await page.goto('/tester.html');

  const card = page.locator('#verdict-card');
  await expect(page.locator('#step-7').getByRole('button', { name: 'Start free play', exact: true })).toBeVisible();
  await expect(card).toBeVisible();
  await expect(card).toHaveAttribute('data-color', 'red');
  await expect(page.locator('#verdict-word')).toHaveText('Not usable');
  await expect(page.locator('#verdict-setting')).toHaveText('1280×800 · 150 µs');
  await expect(page.locator('#verdict-nums')).toHaveText(/frame rate 118 fps.*light 6 DN above black.*clipped 0\.4%/);
  // plain reasons, the full one a tap away
  const reasons = page.locator('#verdict-reasons li');
  await expect(reasons).toHaveCount(2);
  await expect(reasons.nth(0).locator('summary')).toHaveText('too dark ▸');
  await expect(reasons.nth(1).locator('summary')).toHaveText('the ball was not seen before impact ▸');
  await reasons.nth(0).locator('summary').tap();
  await expect(reasons.nth(0)).toContainText('the ball zone is too dark (signal 6 DN above black)');
  const band = page.locator('#ready-light-verdict');
  await expect(band).toHaveAttribute('data-color', 'red');
  await expect(band).toHaveText('Last picture: Not usable');

  verdict = { ...RED, color: 'green', reasons: [] };
  await expect(card).toHaveAttribute('data-color', 'green');
  await expect(page.locator('#verdict-word')).toHaveText('Good picture');
  await expect(band).toHaveAttribute('data-color', 'green');
});

test('the rungs run in ladder order with plain labels, and the ones still to run fold into one line', async ({
  page,
}) => {
  await swingStep(page, () =>
    ladder('full-150', { 'full-300': 'done', 'full-200': 'done', 'full-150': 'active' }, null)
  );
  await page.goto('/tester.html');

  const rows = page.locator('#ladder-panel .rung');
  await expect(rows.nth(0)).toHaveText('Now: 1280×800 · 150 µs');
  await expect(page.locator('#ladder-panel .rung[data-rung]')).toHaveText([
    /^1280×800 · 300 µs/,
    /^1280×800 · 200 µs/,
    /^1280×800 · 150 µs/,
  ]);
  await expect(page.locator('#ladder-panel .rung[data-status="pending"]')).toHaveText(
    'Still to run: 1280×800 · 100, 75, 50, 30, 20, 10 µs; 640×400 · 300, 150, 75, 30, 15 µs'
  );
  await expect(page.locator('#ladder-panel')).not.toContainText('full-');
  // the switch buttons are a clear row per mode, in the same order
  await expect(page.locator('#ladder-jump .chips').nth(0).locator('.ladder-mode')).toHaveText('1280×800');
  await expect(page.locator('#ladder-jump .chips').nth(0).locator('button')).toHaveText([
    '300 µs done',
    '200 µs done',
    '150 µs',
    '100 µs',
    '75 µs',
    '50 µs',
    '30 µs',
    '20 µs',
    '10 µs',
    '7 µs',
    '5 µs',
  ]);
  await expect(page.getByRole('button', { name: 'Switch to 1280×800 at 150 µs' })).toHaveAttribute(
    'aria-pressed',
    'true'
  );
});

test('Start is off while the ladder runs, and back once it stops', async ({ page }) => {
  let stopped = false;
  await swingStep(page, () => ladder(stopped ? null : 'full-150', { 'full-150': 'active' }, null, stopped));
  await page.goto('/tester.html');
  const start = page.locator('#step-ladder');
  await expect(start).toBeDisabled();
  await expect(start).toHaveText('The ladder is running');
  stopped = true;
  await expect(start).toBeEnabled();
  await expect(start).toHaveText('Start the exposure ladder');
});

test('live numbers, the ball at address and the kiosk come from the status poll', async ({ page }) => {
  let status: Status = {
    live_metrics: {
      ball_px: 21.6,
      ball_peak_dn: 188,
      clipped_pct: 0.2,
      exposure_us: 150,
      blur_ceiling_us: 200,
      fps: 120,
      gain: 4,
    },
    ball_at_address: { state: 'in_box', range_m: 2.31, updated_at: 'now' },
    kiosk: { state: 'running', progress_message: null, url: 'http://localhost:8080/' },
    lens_id: 'stock-2.8mm',
  };
  await swingStep(
    page,
    () => ladder('full-150', { 'full-150': 'active' }, null),
    () => status
  );
  await page.goto('/tester.html');

  await expect(page.locator('#live-metrics')).toHaveText(
    'Live:ball 22 pxball peak 188 DNclipped 0.2%exposure 150 µs (blur limit 200 µs)120 fpsgain 4.00'
  );
  const address = page.locator('#address-status');
  // a camera estimate: the radar measures the distance on the swing (13 Oct)
  await expect(address).toHaveText('✓ Ready for a swing: the ball is in the box, about 2.31 m away (camera estimate).');
  await expect(address).toHaveAttribute('data-state', 'in_box');

  // the kiosk opens in a normal window or tab, at the address this page was reached by
  const view = page.locator('#kiosk-view');
  await expect(view).toBeEnabled();
  const popup = page.waitForEvent('popup');
  await view.tap();
  const kiosk = await popup;
  expect(kiosk.url()).toBe('http://127.0.0.1:8080/');
  await kiosk.close();

  // a mode switch shows its progress and the tester page stays where it is
  status = {
    ...status,
    ball_at_address: { state: 'outside_box', range_m: null, updated_at: 'now' },
    kiosk: {
      state: 'switching_mode',
      progress_message: 'Switching to 640×400: restarting the camera (12 s left)',
      url: 'http://localhost:8080/',
    },
  };
  const progress = page.locator('#kiosk-progress');
  await expect(progress).toHaveText('Switching to 640×400: restarting the camera (12 s left)');
  await expect(progress).toBeInViewport();
  await expect(address).toHaveText('Not ready: the ball is outside the box. Move it back into the box.');
  expect(page.url()).toContain('/tester.html');

  status = {
    ...status,
    ball_at_address: { state: 'not_seen', range_m: null, updated_at: 'now' },
    kiosk: { state: 'stopped', progress_message: null, url: null },
    live_metrics: null,
  };
  await expect(progress).toBeHidden();
  await expect(view).toBeDisabled();
  await expect(address).toHaveText('Not ready: the camera cannot see a ball in the box.');
  await expect(page.locator('#live-metrics')).toBeHidden();
});

test("the kiosk's numbers: signed left/right against their frame, values unbroken, not-built ones in one row", async ({
  page,
}) => {
  await page.setViewportSize({ width: 800, height: 480 });
  const catalog = JSON.parse(readFileSync(new URL('../../src/data/metricCatalog.json', import.meta.url), 'utf8')) as {
    metrics: Array<{ id: string; label: string; unit: string; pending: string | null }>;
  };
  const measured = (value: number, unit: string, extra: object = {}) => ({
    value,
    unit,
    status: 'measured',
    source: 'OPS243 radial speed over the 4-18 ms window',
    reason: null,
    frame: null,
    ...extra,
  });
  const notMeasured = (unit: string, reason: string) => ({
    value: null,
    unit,
    status: 'not_measured',
    source: null,
    reason,
  });
  const metrics: Record<string, object> = Object.fromEntries(
    catalog.metrics.map((spec) => [spec.id, notMeasured(spec.unit, 'no camera club delivery on this shot')])
  );
  Object.assign(metrics, {
    ball_speed: measured(170.3, 'mph'),
    launch_horizontal: measured(-3.9, 'deg', { frame: 'target_line' }),
    club_path: measured(2.4, 'deg', { frame: 'unit_boresight' }),
    face_angle: { ...measured(6.1, 'deg', { frame: 'target_line' }), status: 'calculated' },
    club_speed_full: notMeasured(
      'mph',
      'the OPS club speed is a percentile over 4-18 ms before impact, not the full speed'
    ),
    impact_offset: notMeasured('mm', 'pending E5: impact location is not built yet (needs the 6 mm / 8 mm lens)'),
  });
  await swingStep(page, () => ladder(null, {}, RED));
  await page.route('**/api/shots/latest', (route) =>
    json(route, { shot: { shot_number: 3, club: 'driver', metrics }, catalog })
  );
  await page.goto('/tester.html');

  const shot = page.locator('#kiosk-shot');
  // linked to its verdict: the numbers sit inside the verdict card
  await expect(page.locator('#verdict-card #kiosk-shot')).toHaveCount(1);
  await expect(shot).toContainText('Kiosk numbers for its latest shot #3 (driver)');
  const key = (id: string) => shot.locator(`.kiosk-shot__key [data-kiosk-metric="${id}"]`);
  await expect(key('ball_speed')).toContainText('Ball speed (radar line): 170.3 mph');
  await expect(key('launch_horizontal')).toContainText('-3.9° left');
  await expect(key('launch_horizontal').locator('.kiosk-frame')).toHaveText('vs target line');
  await expect(key('club_path')).toContainText('+2.4° right');
  await expect(key('club_path').locator('.kiosk-frame')).toHaveText('vs unit aim');
  expect(
    await key('face_angle')
      .locator('b')
      .evaluate((node) => getComputedStyle(node).whiteSpace)
  ).toBe('nowrap');
  // the darker calculated badge reads at 4.5:1 or better on the page
  const contrast = await key('face_angle')
    .locator('.kiosk-label')
    .evaluate((node) => {
      const parse = (value: string) => (value.match(/\d+(\.\d+)?/g) || []).slice(0, 3).map(Number);
      const luminance = ([r, g, b]: number[]) => {
        const linear = [r, g, b].map((c) => {
          const v = c / 255;
          return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
        });
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
      };
      let back = document.body as HTMLElement;
      for (let at: HTMLElement | null = node as HTMLElement; at; at = at.parentElement) {
        const bg = getComputedStyle(at).backgroundColor;
        if (bg && !bg.startsWith('rgba(0, 0, 0, 0)') && bg !== 'transparent') {
          back = at;
          break;
        }
      }
      const fg = luminance(parse(getComputedStyle(node).color));
      const bg = luminance(parse(getComputedStyle(back).backgroundColor));
      return (Math.max(fg, bg) + 0.05) / (Math.min(fg, bg) + 0.05);
    });
  expect(contrast).toBeGreaterThanOrEqual(4.5);

  // a short reason, the full one on tap
  await shot.getByText('All 35 metrics with labels').tap();
  const speedRow = shot.locator('tr[data-kiosk-metric="carry"]');
  await expect(speedRow.locator('summary')).toHaveText('the camera did not see the club ▸');
  // the not-built ones fold into one “Coming later” row
  const later = shot.locator('tr[data-kiosk-metric="coming-later"]');
  await expect(later).toHaveText(
    /^Coming later.*Gyro.*Club speed \(total\).*Impact offset.*Impact height.*Dynamic lie/
  );
  for (const id of ['gyro', 'club_speed_full', 'impact_offset', 'impact_height', 'dynamic_lie']) {
    await expect(shot.locator(`tr[data-kiosk-metric="${id}"]`)).toHaveCount(0);
  }
  // the source column goes on a narrow screen
  await expect(shot.locator('tr[data-kiosk-metric="ball_speed"] td.src')).toBeHidden();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
