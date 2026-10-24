import { expect, test, type Page, type Route } from '@playwright/test';
import { DEFAULT_TESTER, mockLens, placementBoxState } from './testerHelpers';

// 19 Oct: the tester flow for outside testers. Step 2 checks the unit (all four
// sensors, then the tilt sensor's direction by one lift of the front, then the setup);
// the bar and the band advance step by step; the ball range opens its own camera; step 8
// downloads the package and says how to send it.
test.use({ hasTouch: true });

const VIEWPORTS = [
  { width: 1024, height: 600 },
  { width: 800, height: 480 },
];

async function json(route: Route, payload: object, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(payload) });
}

type Row = { id: string; label: string; status: string; reason: string | null; remedy: string | null };

const SENSORS: [string, string][] = [
  ['ops243', 'Speed radar (OPS243)'],
  ['camera', 'Camera (OV9281)'],
  ['iwr6843_cli', 'Ranging radar (IWR6843)'],
];

/** The setup gate as the tester server reports it, with the 19 Oct unit rows. */
function eligibility(
  o: { checked?: boolean; missing?: string[]; tilt?: boolean; confirmed?: boolean; lis3dh?: string } = {}
) {
  const checked = o.checked ?? false;
  const missing = o.missing ?? [];
  const rows: Row[] = [
    { id: 'geometry', label: 'Approved rig geometry', status: 'pass', reason: null, remedy: null },
    {
      id: 'lis3dh',
      label: 'Stable current LIS3DH reading',
      status: o.lis3dh ? 'block' : 'pass',
      reason: o.lis3dh ?? null,
      remedy: o.lis3dh ? 'Connect the LIS3DH.' : null,
    },
    {
      id: 'operator_confirmation',
      label: 'Operator physical setup confirmation',
      status: o.confirmed ? 'pass' : 'block',
      reason: o.confirmed ? null : 'confirm the physical v3 rig and setup for this tester and configuration',
      remedy: o.confirmed ? null : 'Confirm the setup.',
    },
    ...SENSORS.map(([id, label]): Row => {
      const bad = !checked || missing.includes(label);
      return {
        id,
        label,
        status: bad ? 'block' : 'pass',
        reason: !checked ? 'not checked yet' : bad ? `${label} is missing or not answering: no answer` : null,
        remedy: !checked ? 'Tap Check the unit (step 2).' : bad ? 'Check its cable.' : null,
      };
    }),
    {
      id: 'lis3dh_orientation',
      label: 'Tilt sensor orientation (LIS3DH)',
      status: o.tilt ? 'pass' : 'block',
      reason: o.tilt ? null : "the tilt sensor's orientation has not been checked",
      remedy: o.tilt ? null : 'Run the tilt sensor check in step 2.',
    },
  ];
  const blockers = rows.filter((row) => row.status === 'block').map(({ id, reason, remedy }) => ({ id, reason, remedy }));
  return {
    schema_version: 1,
    tester_id: DEFAULT_TESTER,
    config_hash: 'measured-v3-hash',
    eligible: !blockers.length,
    stage: 'tester_admission',
    checks: rows,
    blockers,
    warnings: [],
    operator_confirmation: {
      confirmed: Boolean(o.confirmed),
      confirmed_at: o.confirmed ? '2026-10-19T18:00:00Z' : null,
      config_hash: 'measured-v3-hash',
      authority: 'operator_physical_setup',
    },
    runtime_requirements: ['ops', 'camera', 'iwr6843', 'lis3dh'],
    hardware: { checked, missing: [...(o.lis3dh ? ['Tilt sensor (LIS3DH)'] : []), ...(checked ? missing : [])] },
  };
}

const READY = eligibility({ checked: true, tilt: true, confirmed: true });

function tilt(state: string, extra: object = {}) {
  return {
    state,
    passed: state === 'passed',
    mount: { yaw_deg: 180, upside_down: false },
    mount_source: 'rig_file',
    corrections: ['turned_around', 'quarter_left', 'quarter_right', 'upside_down'],
    thresholds: { lift_min_deg: 2 },
    ...extra,
  };
}

const STUDY_ARMS = ['arm5', 'arm6'].map((arm_id) => ({
  arm_id,
  light_index: 4.2e-3,
  lighting_required: false,
  too_bright: false,
  mixed_light: false,
  gain_screen: { stale: false, age_s: 40, prompt: null },
}));

interface Stage {
  setup?: () => object;
  tilt?: () => object;
  box?: string;
  aim?: string;
  range?: () => object | null;
  status?: () => object;
}

async function stage(page: Page, o: Stage) {
  // the tester already chosen, as a returning tester's page has it (helpers' useTester)
  await page.addInitScript((id) => localStorage.setItem('openflight-tester-id', id), DEFAULT_TESTER);
  await mockLens(page);
  await page.route('**/api/tester/setup-eligibility?**', (route) => json(route, (o.setup ?? (() => READY))()));
  await page.route('**/api/tester/lis3dh-check', (route) =>
    route.request().method() === 'GET' ? json(route, (o.tilt ?? (() => tilt('passed')))()) : route.fallback()
  );
  await page.route('**/api/tester/placement-box**', (route) =>
    json(route, placementBoxState(DEFAULT_TESTER, o.box ?? 'confirmed'))
  );
  await page.route('**/api/tester/aim-point**', (route) =>
    json(route, { tester_id: DEFAULT_TESTER, state: o.aim ?? 'skipped', runs: {} })
  );
  await page.route('**/api/tester/tee-range?**', (route) => json(route, { state: o.range ? o.range() : null }));
  await page.route('**/api/tester/status**', (route) => json(route, o.status ? o.status() : { study: { arms: [] } }));
  await page.route('**/api/tester/attempts?**', (route) => json(route, { schema_version: 1, scopes: [] }));
  await page.route('**/api/tester/ladder?**', (route) => json(route, { ladder: null, stopped: true }));
  await page.route('**/api/tester/ready-light**', (route) =>
    json(route, { state: 'red', cause: 'kiosk not running', source: 'tester', swing: null, time_left_s: null })
  );
  await page.route('**/api/tester/live', (route) => json(route, { running: false, owner: null }));
}

async function inView(page: Page, selector: string) {
  const box = await page.locator(selector).boundingBox();
  const height = page.viewportSize()!.height;
  expect(box, selector).not.toBeNull();
  expect(box!.y, `${selector} below the sticky bars`).toBeGreaterThanOrEqual(0);
  expect(box!.y + box!.height, `${selector} on screen`).toBeLessThanOrEqual(height);
}

for (const viewport of VIEWPORTS) {
  test.describe(`${viewport.width}×${viewport.height}`, () => {
    test.use({ viewport });

    test('step 2 checks the unit in plain words, with its Check the unit button on screen', async ({ page }) => {
      let setup = eligibility();
      const runs: object[] = [];
      await stage(page, { setup: () => setup, tilt: () => tilt('not_run') });
      await page.route('**/api/tester/run', (route) => {
        runs.push(route.request().postDataJSON());
        setup = eligibility({ checked: true, missing: ['Camera (OV9281)'] });
        return json(route, { job: { state: 'running', action: 'preflight' } }, 202);
      });
      await page.goto('/tester.html');

      await expect(page.locator('#stepbar-step')).toHaveText('Step 2 · Check the unit');
      await expect(page.locator('#ready-light-word')).toHaveText('SETTING UP');
      await expect(page.locator('#setup-summary')).toHaveText('Next: A. tap Check the unit.');
      // the engineering detail is folded away; the button is on the first screen of the step
      await expect(page.locator('#setup-notes')).not.toHaveAttribute('open', '');
      // what a tester reads (innerText leaves out the closed Details)
      const shown = await page.locator('#step-2').evaluate((node) => (node as HTMLElement).innerText);
      for (const term of ['LIS3DH yaw', 'config hash', 'Config ', 'operator_physical_setup', 'swing tally', 'tilted-placement']) {
        expect(shown).not.toContain(term);
      }
      await page.waitForTimeout(400);
      await inView(page, '#step-check');
      await expect(page.locator('#unit-sensors li')).toHaveText([
        /Speed radar \(OPS243\)\s*not checked yet/,
        /Camera \(OV9281\)\s*not checked yet/,
        /Ranging radar \(IWR6843\)\s*not checked yet/,
        /Tilt sensor \(LIS3DH\)\s*ready/,
      ]);
      // the RESET box says when it is for
      await expect(page.locator('#iwr-reset-hint')).toContainText('Only when the ranging radar fails its check');
      await expect(page.locator('#setup-confirm')).toBeDisabled();
      await expect(page.locator('#setup-confirm-hint')).toHaveText('Finish A and B first.');

      await page.locator('#step-check').tap();
      // a missing sensor is refused by name, in the summary and the verdict
      await expect(page.locator('#setup-summary')).toContainText('Missing or not answering: Camera (OV9281)');
      await expect(page.locator('#check-verdict')).toHaveText('Missing or not answering: Camera (OV9281)');
      await expect(page.locator('#unit-sensors li').nth(1)).toContainText('not answering: no answer');
      expect(runs).toEqual([
        { tester_id: DEFAULT_TESTER, arm_id: 'arm5', environment: 'indoors', action: 'preflight', operator_reset: false },
      ]);
      await expect(page.locator('#stepbar-step')).toHaveText('Step 2 · Check the unit');
    });

    test('the tilt check: lift the front, and a wrong reading offers its fix by tap', async ({ page }) => {
      let state = tilt('not_run');
      const posts: object[] = [];
      await stage(page, { setup: () => eligibility({ checked: true }), tilt: () => state });
      await page.route('**/api/tester/lis3dh-check', (route) => {
        if (route.request().method() !== 'POST') return route.fallback();
        const body = route.request().postDataJSON();
        posts.push(body);
        state = body.action === 'start' ? tilt('lift') : tilt('lift', { mount: { yaw_deg: 90, upside_down: false } });
        return json(route, state);
      });
      await page.goto('/tester.html');

      await expect(page.locator('#setup-summary')).toHaveText('Next: B. the tilt sensor check.');
      await page.locator('#lis3dh-start').tap();
      await expect(page.locator('#lis3dh-status')).toHaveText('Lift the FRONT of the unit about 2 cm and hold it still');
      await expect(page.locator('#lis3dh-start')).toBeHidden();

      state = tilt('needs_correction', { suggestion: 'quarter_right', front_up_deg: 5.8 });
      await expect(page.locator('#lis3dh-fixes')).toBeVisible();
      await expect(page.locator('#lis3dh-status')).toContainText('fitted another way round');
      const fixes = page.locator('#lis3dh-fixes button[data-correction]');
      await expect(fixes).toHaveText([
        'Turned around (180°)',
        'Quarter turn left (90°)',
        'Quarter turn right (90°)',
        'Upside down',
      ]);
      await expect(page.locator('#lis3dh-fixes button[data-suggested="true"]')).toHaveText('Quarter turn right (90°)');
      for (const fix of await fixes.all()) expect((await fix.boundingBox())!.height).toBeGreaterThanOrEqual(44);
      await page.locator('#lis3dh-fixes button[data-correction="quarter_right"]').tap();
      await expect.poll(() => posts).toEqual([{ action: 'start' }, { action: 'correct', correction: 'quarter_right' }]);
      // the check runs again with the fix
      await expect(page.locator('#lis3dh-status')).toHaveText('Lift the FRONT of the unit about 2 cm and hold it still');

      state = tilt('passed', { front_up_deg: 5.9 });
      await expect(page.locator('#lis3dh-status')).toHaveText('Tilt sensor checked: the front reads up');
      await expect(page.locator('#lis3dh-prompt')).toHaveText('Put the unit back down on its feet.');
      await expect(page.locator('#lis3dh-start')).toHaveText('Check it again');
    });

    test('an upside-down board is caught by gravity alone', async ({ page }) => {
      await stage(page, {
        setup: () => eligibility({ checked: true, lis3dh: 'LIS3DH reports the enclosure is upside down' }),
        tilt: () => tilt('upside_down', { suggestion: 'upside_down' }),
      });
      await page.goto('/tester.html');
      await expect(page.locator('#lis3dh-status')).toHaveText('The tilt sensor reads upside down');
      await expect(page.locator('#lis3dh-fixes button[data-suggested="true"]')).toHaveText('Upside down');
      await expect(page.locator('#unit-sensors li').nth(3)).toContainText('reads upside down');
    });

    test('the bar and the band advance step by step, and stay neutral until free play', async ({ page }) => {
      let setup = eligibility({ checked: true, tilt: true });
      let box = 'needs_confirmation';
      let aim = 'needs_patch';
      let light = false;
      await stage(page, {
        setup: () => setup,
        status: () => ({ study: { arms: light ? STUDY_ARMS : [] } }),
      });
      await page.unroute('**/api/tester/placement-box**');
      await page.route('**/api/tester/placement-box**', (route) => json(route, placementBoxState(DEFAULT_TESTER, box)));
      await page.unroute('**/api/tester/aim-point**');
      await page.route('**/api/tester/aim-point**', (route) => json(route, { tester_id: DEFAULT_TESTER, state: aim, runs: {} }));
      await page.route('**/api/tester/tee-range', (route) =>
        json(route, { state: { epoch_id: 'e1', phase: 'experimental', reason: 'camera_prior_saved' }, display: null })
      );
      await page.goto('/tester.html');
      const bar = page.locator('#stepbar-step');
      const band = page.locator('#ready-light-cause');

      await expect(bar).toHaveText('Step 2 · Check the unit');
      setup = READY;
      box = 'needs_confirmation';
      await expect(bar).toHaveText('Step 3 · Ball in the box');
      await expect(band).toHaveText('Step 3 · Ball in the box · free play starts at step 7');
      box = 'confirmed';
      await expect(bar).toHaveText('Step 4 · Aim at your target');
      aim = 'skipped';
      await expect(bar).toHaveText('Step 5 · Check the light');
      await expect(page.locator('#status')).not.toContainText('(complete)');
      light = true;
      // step 6 opens its own camera: the page starts the range by itself
      await expect(bar).toHaveText('Step 6 · Ball range');
      await expect(page.locator('#ready-light-word')).toHaveText('SETTING UP');
    });

    test('free play not started: the band is neutral, never a red NOT READY', async ({ page }) => {
      await stage(page, {
        range: () => ({ epoch_id: 'e1', phase: 'experimental', reason: 'camera_prior_saved' }),
        status: () => ({ study: { arms: STUDY_ARMS }, free_play: { enabled: false } }),
      });
      await page.goto('/tester.html');
      await expect(page.locator('#stepbar-step')).toHaveText('Step 7 · Free play');
      await expect(page.locator('#ready-light-word')).toHaveText('NOT STARTED');
      await expect(page.locator('#ready-light-cause')).toHaveText('Free play starts at step 7: tap Start free play');
      await expect(page.locator('#ready-light')).toHaveAttribute('data-state', 'setup');
    });

    test('step 6 opens the camera by itself: one tap, Save the ball', async ({ page }) => {
      let range: object | null = null;
      const actions: string[] = [];
      await stage(page, { range: () => range, status: () => ({ study: { arms: STUDY_ARMS } }) });
      await page.route('**/api/tester/tee-range', (route) => {
        const { action } = route.request().postDataJSON();
        actions.push(action);
        range =
          action === 'start'
            ? { epoch_id: 'e1', phase: 'needs_camera_arm5', reason: 'place_the_ball_in_the_box' }
            : { epoch_id: 'e1', phase: 'camera_arm5_capturing', reason: 'camera_live' };
        return json(route, { state: range, display: null });
      });
      await page.goto('/tester.html');
      await expect(page.locator('#stepbar-step')).toHaveText('Step 6 · Ball range');
      await expect.poll(() => actions).toEqual(['start', 'start_camera_arm5']);
      await expect(page.locator('#tee-range-action')).toHaveText('Save the ball');
      await page.waitForTimeout(1500);
      expect(actions).toEqual(['start', 'start_camera_arm5']);
    });

    test('step 6 says free play is next, and how its light note differs from step 5', async ({ page }) => {
      await stage(page, {
        range: () => ({ epoch_id: 'e1', phase: 'experimental', reason: 'camera_prior_saved' }),
        status: () => ({ study: { arms: STUDY_ARMS } }),
      });
      await page.route('**/api/tester/tee-range?**', (route) =>
        json(route, {
          state: { epoch_id: 'e1', phase: 'experimental', reason: 'camera_prior_saved' },
          display: {
            light_warning: {
              status: 'measured',
              message: 'Your light supports 1280x800 300 us.',
              supported: ['full-300', 'full-200'],
              unsupported: ['full-100', 'full-75'],
              rungs: [
                { rung_id: 'full-300', arm_id: 'arm5', exposure_us: 300, supported: true, light_needed_factor: 1 },
                { rung_id: 'full-200', arm_id: 'arm5', exposure_us: 200, supported: true, light_needed_factor: 1 },
                { rung_id: 'full-100', arm_id: 'arm5', exposure_us: 100, supported: false, light_needed_factor: 1.13 },
                { rung_id: 'full-75', arm_id: 'arm5', exposure_us: 75, supported: false, light_needed_factor: 1.5 },
              ],
            },
          },
        })
      );
      await page.goto('/tester.html');
      await expect(page.locator('#automatic-range-summary')).toContainText('Free play (step 7) is next.');
      await expect(page.locator('#step-6')).not.toContainText('ladder can start');
      await expect(page.locator('#tee-range-light-warning')).toHaveText(
        'Light for the exposure study: enough for 1280×800 at 300, 200 µs. Its shortest settings (1280×800 at 100, 75 µs) need about 1.13× more light: they are still recorded, but will read dark. Step 5’s “light sufficient” is for each mode’s standard setting.'
      );
    });

    test('step 8: a big Download, the send folder and size, and three plain steps to send it', async ({ page }) => {
      const bundle = { name: 'demo-tester-session-bundle-20261019T183012Z.zip', size_bytes: 1_234_567_890, sha256: 'a'.repeat(64) };
      await stage(page, {
        range: () => ({ epoch_id: 'e1', phase: 'experimental', reason: 'camera_prior_saved' }),
        status: () => ({
          study: { arms: STUDY_ARMS },
          free_play: { enabled: true, tester_id: DEFAULT_TESTER, run_id: 'r1', phase: 'closed', bundle },
          analysis: {
            state: 'complete',
            reason: null,
            job: { state: 'complete' },
            review_ready: true,
            bundles: [bundle],
            latest_bundle: bundle,
            send_folder: '~/OpenFlight-to-send',
            send_copy: { name: bundle.name, folder: '~/OpenFlight-to-send', size_bytes: bundle.size_bytes, method: 'link', error: null },
          },
        }),
      });
      await page.goto('/tester.html');
      await expect(page.locator('#stepbar-step')).toHaveText('Step 8 · Send the package');
      await expect(page.locator('#ready-light-word')).toHaveText('FINISHED');
      const download = page.locator('#bundle-link');
      await expect(download).toHaveText('Download');
      await expect(download).toHaveAttribute('download', '');
      await expect(download).toHaveAttribute('href', new RegExp(`/api/tester/bundle\\?tester_id=${DEFAULT_TESTER}&name=${bundle.name}`));
      expect((await download.boundingBox())!.height).toBeGreaterThanOrEqual(60);
      await expect(page.locator('#bundle-size')).toHaveText(`${bundle.name} · 1.23 GB`);
      await expect(page.locator('#send-folder')).toHaveText('Also saved on the unit in ~/OpenFlight-to-send/');
      await expect(page.locator('#send-steps li')).toHaveText([
        'Upload the zip to your Google Drive (drive.google.com → New → File upload).',
        'Share it: “Anyone with the link”.',
        'Send the link to whoever asked you to test.',
      ]);
      await expect(page.locator('#step-8')).not.toContainText('upload page');
      // the manual ladder's packaging is the maintainer's, folded away
      await expect(page.locator('#step-package')).toBeHidden();
      await expect(page.locator('#step-8')).not.toContainText('Stop the ladder first');
      await expect(page.locator('#free-play-status')).toHaveText(`Package ready: ${bundle.name}. Send it from step 8 below.`);
      await download.scrollIntoViewIfNeeded();
      await inView(page, '#bundle-link');
    });
  });
}

test('step 1 asks for no photo spray, and a first lens pick names nothing to set again', async ({ page }) => {
  await stage(page, { setup: () => eligibility(), tilt: () => tilt('not_run'), box: 'setup_not_confirmed', aim: 'needs_patch' });
  await page.unroute('**/api/tester/lens?**');
  await mockLens(page, null);
  await page.route('**/api/tester/lens', (route) =>
    json(route, {
      tester_id: DEFAULT_TESTER,
      lens_id: 'stock-2.8mm',
      changed: true,
      reset: ['placement_box', 'aim_point', 'camera_tilt'],
    })
  );
  await page.goto('/tester.html');
  await expect(page.locator('#before-start')).not.toContainText('spray');
  await page.locator('#lens-list .lens-card').first().tap();
  await expect(page.locator('#lens-status')).toHaveText('Lens set to Stock 2.8 mm.');
});
