import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { readFileSync } from 'node:fs';
import { test, type Page, type Route } from '@playwright/test';
import {
  expect,
  foundBall,
  gotoApp,
  lensState,
  placementBoxState,
  PATCH_PROJECTION,
  resetSession,
  simulateShot,
  withControlSocket,
} from './helpers';

// Screenshots of every tester step and the kiosk tabs at the two Pi screens, for review.
// Off unless UX_SHOTS_DIR names a folder: `UX_SHOTS_DIR=/tmp/ux npx playwright test ux-screenshots`.
const OUT = process.env.UX_SHOTS_DIR;
test.skip(!OUT, 'set UX_SHOTS_DIR to write the review screenshots');
test.use({ hasTouch: true });

const TESTER = 'harjot-indoor-test-2';
const SIZES = [
  { width: 1024, height: 600 },
  { width: 800, height: 480 },
] as const;

// a stand-in camera frame: a net, the floor, a white ball in the box and a small flag
const SCENE = `<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="800" viewBox="0 0 1280 800">
<rect width="1280" height="800" fill="#2b2f36"/>
<rect y="0" width="1280" height="300" fill="#3b4047"/>
<g stroke="#4a5059" stroke-width="2">${Array.from({ length: 33 }, (_, i) => `<line x1="${i * 40}" y1="0" x2="${i * 40}" y2="300"/>`).join('')}${Array.from({ length: 8 }, (_, i) => `<line x1="0" y1="${i * 40}" x2="1280" y2="${i * 40}"/>`).join('')}</g>
<polygon points="0,300 1280,300 1280,800 0,800" fill="#2f5a34"/>
<polygon points="430,560 850,560 1280,800 0,800" fill="#3a6b3f"/>
<circle cx="640" cy="478" r="11" fill="#f4f4f4"/>
<line x1="760" y1="236" x2="760" y2="262" stroke="#ddd" stroke-width="2"/>
<polygon points="760,236 772,240 760,244" fill="#e53935"/>
</svg>`;

async function json(route: Route, payload: object, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(payload) });
}

function eligibility(eligible: boolean) {
  return {
    schema_version: 1,
    tester_id: TESTER,
    config_hash: 'measured-v3-hash',
    eligible,
    stage: 'tester_admission',
    checks: [
      { id: 'rig_config', label: 'Approved measured v3 rig config', status: 'pass', reason: null, remedy: null },
      { id: 'lis3dh', label: 'LIS3DH identity and stable tilt', status: 'pass', reason: 'Tilt is stable.', remedy: null },
      {
        id: 'operator_physical_setup',
        label: 'Operator physical rig confirmation',
        status: eligible ? 'pass' : 'block',
        reason: eligible ? 'Confirmed for this config.' : 'Physical build is not confirmed.',
        remedy: 'Measure the feet, lens reference, radar mounts and LIS3DH orientation, then confirm.',
      },
    ],
    blockers: eligible ? [] : [{ id: 'operator_physical_setup', reason: 'not confirmed', remedy: 'confirm' }],
    operator_confirmation: {
      confirmed: eligible,
      confirmed_at: eligible ? '2026-10-13T08:00:00Z' : null,
      config_hash: 'measured-v3-hash',
      authority: 'operator_physical_setup',
    },
    runtime_requirements: ['ops', 'camera', 'iwr6843', 'lis3dh'],
  };
}

type Stage = {
  tester?: string | null;
  eligible?: boolean;
  box?: object;
  aim?: object;
  range?: object | null;
  rangeDisplay?: object | null;
  ladder?: object;
  status?: object;
  liveOwner?: string | null;
  lens?: string | null;
};

const RUNGS = ['full-300', 'full-200', 'full-150', 'full-100', 'full-75', 'full-50', 'full-30', 'full-20', 'full-10', 'half-300', 'half-150', 'half-75', 'half-30', 'half-15'];

function ladderRunning() {
  const status: Record<string, string> = { 'full-300': 'done', 'full-200': 'done', 'full-150': 'active' };
  return {
    ladder: {
      current: 'full-150',
      pending_photo: null,
      rungs: Object.fromEntries(
        RUNGS.map((id) => [
          id,
          {
            status: status[id] ?? 'pending',
            swings: status[id] === 'done' ? Array(5).fill({ color: 'green' }) : status[id] === 'active' ? [{ color: 'red' }] : [],
            reason: null,
          },
        ])
      ),
    },
    last_verdict: {
      color: 'red',
      rung_id: 'full-150',
      capture: 'camera_20261013_101500',
      reasons: ['light: the ball zone is too dark (signal 6 DN above black)', 'resting ball not found in the pre-impact frames'],
      delivered_fps: 118.4,
      signal_dn: 6.2,
      clipped_pct: 0.4,
    },
    photo_target: null,
    stopped: false,
  };
}

async function stage(page: Page, options: Stage) {
  const tester = options.tester === undefined ? TESTER : options.tester;
  await page.addInitScript((id) => {
    if (id) localStorage.setItem('openflight-tester-id', id);
    else localStorage.removeItem('openflight-tester-id');
  }, tester);
  await page.route('**/api/tester/testers**', (route) =>
    json(route, { testers: [TESTER, '20260922-name', 'cormac-garage-1'], suggested_new: 'tester-2026-10-13-1' })
  );
  await page.route('**/api/tester/lens**', (route) => json(route, lensState(TESTER, options.lens === undefined ? 'stock-2.8mm' : options.lens)));
  await page.route('**/api/tester/setup-eligibility?**', (route) => json(route, eligibility(options.eligible ?? true)));
  await page.route('**/api/tester/placement-box**', (route) =>
    json(route, options.box ?? placementBoxState(TESTER, 'confirmed'))
  );
  await page.route('**/api/tester/aim-point**', (route) =>
    json(route, options.aim ?? { tester_id: TESTER, state: 'confirmed', target_line: { yaw_deg: 2.1, aim_px: [760, 250] }, total_uncertainty_deg: 0.8, confirmed_at_utc: '2026-10-13T08:05:00Z', projection: PATCH_PROJECTION, runs: {} })
  );
  await page.route('**/api/tester/tee-range**', (route) =>
    json(route, { state: options.range ?? null, display: options.rangeDisplay ?? null })
  );
  await page.route('**/api/tester/status**', (route) => json(route, options.status ?? {}));
  await page.route('**/api/tester/attempts?**', (route) => json(route, { schema_version: 1, scopes: [] }));
  await page.route('**/api/tester/ladder?**', (route) => json(route, options.ladder ?? { ladder: null, stopped: true }));
  await page.route('**/api/tester/ready-light**', (route) =>
    json(route, { state: 'green', word: 'SWING', cause: 'Ready for a swing', time_left_s: null, checked_at: 1, swing: null, source: 'kiosk' })
  );
  await page.route('**/api/tester/live', (route) => {
    const owner = options.liveOwner ?? null;
    return json(route, {
      running: Boolean(owner),
      arm_id: 'arm5',
      owner: owner ? { kind: owner, tester_id: TESTER, epoch_id: (options.range as { epoch_id?: string } | null)?.epoch_id ?? null, arm_id: 'arm5' } : null,
      stats: owner ? { mean: 90, p99: 200, max: 230, clipped_pct: 0 } : null,
      association: null,
      guided_display: null,
      placement_box: null,
      preview_exposure: owner ? { purpose: 'box_preview', display_only: true, state: 'settled' } : null,
    });
  });
  await page.route('**/api/tester/live.png?**', (route) => route.fulfill({ status: 200, contentType: 'image/svg+xml', body: SCENE }));
  await page.route('**/api/camera/preview.jpg**', (route) => route.fulfill({ status: 204 }));
}

async function shot(page: Page, name: string, size: { width: number; height: number }, target?: string) {
  const dir = OUT as string;
  mkdirSync(dir, { recursive: true });
  if (target) {
    await page.locator(target).scrollIntoViewIfNeeded();
    await page.evaluate((id) => document.querySelector(id)?.scrollIntoView({ block: 'start' }), target);
  }
  await page.waitForTimeout(400);
  await page.screenshot({ path: join(dir, `${name}-${size.width}x${size.height}.png`) });
}

for (const size of SIZES) {
  test.describe(`tester steps at ${size.width}x${size.height}`, () => {
    test.use({ viewport: size });

    test('step 1: tester picker and lens', async ({ page }) => {
      await stage(page, { tester: null });
      await page.goto('/tester.html');
      await expect(page.locator('#tester-list button')).toHaveCount(3);
      await shot(page, '01-step1-tester-picker', size);
      await page.locator('#tester-list button').first().tap();
      await expect(page.locator('#lens-list .lens-card')).toHaveCount(3);
      await shot(page, '02-step1-lens', size, '#lens');
      await page.locator('#lens-list .lens-card').nth(1).tap();
      await shot(page, '03-step1-lens-change-warning', size, '#lens');
      await page.getByRole('button', { name: 'Keep the current lens' }).tap();
      await page.getByRole('button', { name: "Change this lens's settings" }).tap();
      await shot(page, '04-step1-lens-settings', size, '#lens-editor');
    });

    test('step 2: setup check', async ({ page }) => {
      await stage(page, { eligible: false, box: placementBoxState(TESTER, 'setup_not_confirmed') });
      await page.goto('/tester.html');
      await shot(page, '05-step2-setup-not-confirmed', size, '#step-2');
    });

    test('step 3: ball box', async ({ page }) => {
      await stage(page, {
        box: { ...placementBoxState(TESTER, 'needs_confirmation'), previewing: true },
        aim: { tester_id: TESTER, state: 'needs_patch' },
        liveOwner: 'placement_box',
      });
      await page.goto('/tester.html');
      await expect(page.locator('#placement-check')).toHaveAttribute('display', 'inline');
      await shot(page, '06-step3-ball-found', size);
    });

    test('step 3: ball outside the box', async ({ page }) => {
      await stage(page, {
        box: {
          ...placementBoxState(TESTER, 'needs_confirmation', {
            ball: foundBall({ state: 'outside_box', center_px: [880, 500], message: null, range_m: null, range_status: null }),
            net_line: { row_px: null, status: 'not_set', message: null },
          }),
          previewing: true,
        },
        aim: { tester_id: TESTER, state: 'needs_patch' },
        liveOwner: 'placement_box',
      });
      await page.goto('/tester.html');
      await expect(page.locator('#placement-ball-status')).toHaveAttribute('data-state', 'outside_box');
      await shot(page, '07-step3-ball-outside', size);
    });

    test('step 4: aim and the full-screen magnifier', async ({ page }) => {
      await stage(page, {
        aim: { tester_id: TESTER, state: 'needs_confirmation', previewing: true, reason: 'not_set', aim: { aim_px: [758, 252] }, projection: PATCH_PROJECTION, runs: {} },
        liveOwner: 'aim_point',
      });
      await page.goto('/tester.html');
      await expect(page.locator('#aim-overlay')).toHaveAttribute('data-aim', '758.0,252.0');
      await shot(page, '08-step4-aim', size);
      await page.locator('#aim-fullscreen').tap();
      await expect(page.locator('#aim-full')).toBeVisible();
      await shot(page, '09-step4-aim-fullscreen-loupe', size);
    });

    test('step 4: skipped', async ({ page }) => {
      await stage(page, { aim: { tester_id: TESTER, state: 'skipped', runs: {} } });
      await page.goto('/tester.html');
      await shot(page, '10-step4-aim-skipped', size, '#step-4');
    });

    test('steps 5 and 6: hardware, light and ball range', async ({ page }) => {
      await stage(page, {
        range: { epoch_id: 'e1', phase: 'needs_ball', reason: 'needs_ball', evidence: {}, solution: null },
        rangeDisplay: { iwr: { state: 'pending' }, canonical: { state: 'withheld', reason: 'pending' } },
      });
      await page.goto('/tester.html');
      await shot(page, '11-step5-hardware-light', size, '#step-5');
      await shot(page, '12-step6-ball-range', size, '#step-6');
    });

    test('step 7: swing verdict, live numbers and kiosk progress', async ({ page }) => {
      const catalog = JSON.parse(readFileSync(new URL('../../src/data/metricCatalog.json', import.meta.url), 'utf8')) as {
        metrics: Array<{ id: string; unit: string }>;
      };
      const metrics = Object.fromEntries(
        catalog.metrics.map((spec) => [spec.id, { value: null, unit: spec.unit, status: 'not_measured', source: null, reason: 'no camera club delivery on this shot' }])
      );
      Object.assign(metrics, {
        ball_speed: { value: 170.3, unit: 'mph', status: 'measured', source: 'OPS243 radial', frame: null },
        launch_vertical: { value: 13.4, unit: 'deg', status: 'measured', source: 'IWR', frame: null },
        launch_horizontal: { value: -3.9, unit: 'deg', status: 'measured', source: 'IWR', frame: 'target_line' },
        spin_rate: { value: 3169, unit: 'rpm', status: 'measured', source: 'radar', frame: null },
        carry: { value: 308.7, unit: 'yd', status: 'estimated', source: 'flight model', frame: null },
        club_speed: { value: 116.6, unit: 'mph', status: 'measured', source: 'OPS243 radial', frame: null },
        club_path: { value: -3.1, unit: 'deg', status: 'measured', source: 'camera', frame: 'target_line' },
        face_angle: { value: 6.1, unit: 'deg', status: 'calculated', source: 'D-plane', frame: 'target_line' },
      });
      await page.route('**/api/shots/latest', (route) => json(route, { shot: { shot_number: 1, club: 'driver', metrics }, catalog }));
      await stage(page, {
        range: { epoch_id: 'e1', phase: 'experimental', reason: 'experimental', evidence: {}, solution: null },
        rangeDisplay: { canonical: { state: 'experimental', range_m: 2.31 } },
        ladder: ladderRunning(),
        status: {
          live_metrics: { ball_px: 21.6, ball_peak_dn: 188, clipped_pct: 0.2, exposure_us: 150, blur_ceiling_us: 200, fps: 120, gain: 4 },
          ball_at_address: { state: 'in_box', range_m: 2.31, updated_at: 'now' },
          kiosk: { state: 'switching_mode', progress_message: 'Switching to 640×400: restarting the camera (12 s left)', url: 'http://localhost:8080/' },
        },
      });
      await page.goto('/tester.html');
      await expect(page.locator('#verdict-card')).toHaveAttribute('data-color', 'red');
      await shot(page, '13-step7-verdict-card', size, '#step-7');
      await page.locator('#kiosk-shot details > summary').first().tap();
      await shot(page, '14-step7-kiosk-numbers', size, '#kiosk-shot');
      await shot(page, '15-step7-ladder-rows', size, '#ladder-panel');
      await shot(page, '16-step7-settings', size, '#ladder-choice');
    });

    test('step 8: review and package', async ({ page }) => {
      await stage(page, {});
      await page.goto('/tester.html');
      await shot(page, '17-step8-package', size, '#step-8');
    });
  });

  test.describe(`kiosk tabs at ${size.width}x${size.height}`, () => {
    test.use({ viewport: size });

    test.beforeEach(async () => {
      await withControlSocket(async (socket) => {
        await resetSession(socket);
        await simulateShot(socket);
      });
    });

    test('live, club and flight', async ({ page }) => {
      await page.route('**/api/target-line', (route) =>
        json(route, { status: 'set', yaw_deg: 2.1, total_uncertainty_deg: 0.8, source: 'aim_point' })
      );
      await gotoApp(page);
      await page.getByRole('button', { name: 'Close Select club' }).click();
      await expect(page.locator('.live-panel__target')).toBeVisible();
      await shot(page, '20-kiosk-live', size);
      await page.getByRole('button', { name: 'Club', exact: true }).click();
      await shot(page, '21-kiosk-club', size);
      await page.locator('[data-metric="coming-later"]').scrollIntoViewIfNeeded();
      await page.locator('[data-metric="coming-later"]').tap();
      await shot(page, '22-kiosk-club-coming-later', size);
      await page.getByRole('button', { name: 'Flight', exact: true }).click();
      await shot(page, '23-kiosk-flight', size);
    });
  });
}
