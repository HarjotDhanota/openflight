import { expect, test, type Page, type Route } from '@playwright/test';
import { DEFAULT_TESTER, lensState, mockConfirmedPlacementBox, useTester } from './helpers';

/**
 * The free-play step as a tester sees it (19 Oct): only free play and its exposure
 * study on the main path, the manual ladder folded away for maintainers, and one
 * current step behind both the sticky band and the “You are here” bar.
 */
const VIEWPORTS = [
  { width: 1024, height: 600 },
  { width: 800, height: 480 },
] as const;

/** Screenshots go to the test's output folder, or to FREE_PLAY_PAGE_SHOTS_DIR when set. */
function shotPath(name: string) {
  const dir = process.env.FREE_PLAY_PAGE_SHOTS_DIR;
  return dir ? `${dir}/${name}` : test.info().outputPath(name);
}

async function fulfillJson(route: Route, payload: object, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(payload) });
}

function eligibility(testerId: string | null, eligible: boolean) {
  return {
    schema_version: 1,
    tester_id: testerId,
    config_hash: 'measured-v3-hash',
    eligible,
    stage: 'tester_admission',
    checks: [],
    blockers: eligible ? [] : [{ id: 'operator_physical_setup', reason: 'not confirmed', remedy: 'confirm' }],
    operator_confirmation: {
      confirmed: eligible,
      confirmed_at: eligible ? '2026-10-19T08:00:00Z' : null,
      config_hash: 'measured-v3-hash',
      authority: 'operator_physical_setup',
    },
    runtime_requirements: ['ops', 'camera', 'iwr6843', 'lis3dh'],
  };
}

const STUDY = {
  phase: 'ladder',
  ladder_status: 'running',
  resolution: { arm_id: 'arm6', label: '640×400', width: 640, height: 400, fps: 288 },
  text: 'Exposure study: 300 µs · swing 3 of 5 · setting 1 of 2',
  changing: false,
  swings_per_rung: 5,
  settings_total: 2,
  rungs: [
    {
      rung_id: 'half-300',
      exposure_us: 300,
      gain: 1,
      status: 'active',
      skip_reason: null,
      swings: 2,
      counts: { green: 1, amber: 0, red: 1, no_capture: 0, error: 0 },
    },
  ],
};

type World = {
  eligible: boolean;
  aim: string;
  freePlay: object;
  /** the kiosk's light; null: it never answers */
  light: object | null;
};

const GREEN = {
  schema_version: 1,
  state: 'green',
  word: 'SWING',
  cause: 'Ready for a swing',
  time_left_s: null,
  causes: [],
  since: 100,
  checked_at: 110,
  hold: null,
  swing: null,
  source: 'kiosk',
};

/** Every tester endpoint the page polls, driven by `world` so a test can walk the steps. */
async function mockWorld(page: Page, world: World) {
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
    fulfillJson(route, eligibility(new URL(route.request().url()).searchParams.get('tester_id'), world.eligible))
  );
  await page.route('**/api/tester/aim-point**', (route) =>
    fulfillJson(route, {
      tester_id: new URL(route.request().url()).searchParams.get('tester_id'),
      state: world.aim,
      runs: {},
    })
  );
  await page.route('**/api/camera/preview.jpg**', (route) => route.fulfill({ status: 204 }));
  await page.route('**/api/tester/attempts?**', (route) => fulfillJson(route, { schema_version: 1, scopes: [] }));
  // the state that drew "Ladder finished" under Start free play: a ladder with no setting left
  await page.route('**/api/tester/ladder?**', (route) => fulfillJson(route, { ladder: { rungs: {} }, stopped: true }));
  await page.route('**/api/tester/status**', (route) => fulfillJson(route, { free_play: world.freePlay }));
  // with no light the kiosk never answers: the band must not wait on it to name the step
  await page.route('**/api/tester/ready-light**', (route) =>
    world.light ? fulfillJson(route, world.light) : new Promise<void>(() => {})
  );
}

/** Both step indicators, read in one go so neither can catch up between the reads. */
async function indicators(page: Page) {
  return page.evaluate(() => ({
    here: document.getElementById('stepbar-step')!.textContent!.trim(),
    word: document.getElementById('ready-light-word')!.textContent!.trim(),
    cause: document.getElementById('ready-light-cause')!.textContent!.trim(),
    state: document.getElementById('ready-light')!.dataset.state,
  }));
}

async function expectAgreeAt(page: Page, step: number, title: string) {
  const here = `Step ${step} · ${title}`;
  await expect(page.locator('#stepbar-step')).toHaveText(here);
  const now = await indicators(page);
  expect(now.here).toBe(here);
  expect(now.state).toBe('setup');
  expect(now.word).toBe('SETTING UP');
  expect(now.cause).toBe(`${here} · free play starts at step 7`);
}

for (const viewport of VIEWPORTS) {
  test.describe(`the free-play step at ${viewport.width}x${viewport.height}`, () => {
    test.use({ viewport, hasTouch: true });

    test('the main path shows free play only; the manual ladder is folded away for maintainers', async ({
      page,
    }) => {
      await useTester(page);
      const world: World = { eligible: true, aim: 'skipped', freePlay: { enabled: false }, light: GREEN };
      await mockWorld(page, world);
      await page.goto('/tester.html');

      const advanced = page.locator('#advanced-ladder');
      const summary = advanced.locator(':scope > summary');
      await expect(summary).toHaveText('Advanced: manual exposure ladder (maintainer)');
      await expect(page.locator('#ladder-panel')).toContainText('Ladder finished', { useInnerText: false });

      // before free play starts: free play's controls, no old verdict card or ladder status
      await expect(page.locator('#step-free-play')).toBeVisible();
      await expect(advanced).not.toHaveAttribute('open');
      await expect(page.locator('#verdict-card')).toBeHidden();
      await expect(page.getByText('No swing judged yet')).toBeHidden();
      await expect(page.getByText('No kiosk shot yet.')).toBeHidden();
      await expect(page.getByText('Ladder finished')).toBeHidden();
      await expect(page.locator('#step-ladder')).toBeHidden();
      await expect(page.locator('#step-7 #advanced-ladder')).toHaveCount(1);
      await page.locator('#free-play-resolution button[data-resolution="arm6"]').tap();
      await page.locator('#step-7').scrollIntoViewIfNeeded();
      await summary.scrollIntoViewIfNeeded();
      await page.screenshot({ path: shotPath(`free-play-idle-${viewport.width}x${viewport.height}.png`) });

      // the advanced section still opens, and its controls are still there
      await summary.tap();
      await expect(advanced).toHaveAttribute('open');
      await expect(page.locator('#verdict-card')).toBeVisible();
      await expect(page.locator('#step-ladder')).toBeVisible();
      await summary.tap();
      await expect(advanced).not.toHaveAttribute('open');

      // during free play: its exposure study's progress, the advanced section still closed
      world.freePlay = {
        enabled: true,
        tester_id: DEFAULT_TESTER,
        run_id: 'run-1',
        phase: 'recording',
        resolution: STUDY.resolution,
        exposure_study: STUDY,
      };
      await expect(page.locator('#free-play-study')).toHaveText(STUDY.text);
      await expect(page.locator('#free-play-rungs li')).toHaveCount(1);
      await expect(advanced).not.toHaveAttribute('open');
      await expect(summary).toBeVisible();
      await expect(page.locator('#verdict-card')).toBeHidden();
      await expect(page.getByText('No swing judged yet')).toBeHidden();
      await expect(page.getByText('Ladder finished')).toBeHidden();
      await expect(page.locator('#stepbar-step')).toHaveText('Step 7 · Free play');
      await expect(page.locator('#ready-light')).toHaveAttribute('data-state', 'green');
      await page.locator('#free-play-rungs').scrollIntoViewIfNeeded();
      await page.screenshot({ path: shotPath(`free-play-recording-${viewport.width}x${viewport.height}.png`) });
    });

    test('the sticky band and the “You are here” bar always name the same step', async ({ page }) => {
      await useTester(page);
      const world: World = { eligible: false, aim: 'unset', freePlay: { enabled: false }, light: null };
      await mockWorld(page, world);
      await page.goto('/tester.html');

      await expectAgreeAt(page, 2, 'Check the setup');
      await page.screenshot({ path: shotPath(`band-step2-${viewport.width}x${viewport.height}.png`) });

      world.eligible = true;
      await expectAgreeAt(page, 4, 'Aim at your target');
      await page.screenshot({ path: shotPath(`band-step4-${viewport.width}x${viewport.height}.png`) });

      world.aim = 'skipped';
      await expectAgreeAt(page, 5, 'Hardware and light');

      // free play recording is the swing step: the band stops saying it is setting up
      world.freePlay = {
        enabled: true,
        tester_id: DEFAULT_TESTER,
        run_id: 'run-1',
        phase: 'recording',
        resolution: STUDY.resolution,
        exposure_study: STUDY,
      };
      await expect(page.locator('#stepbar-step')).toHaveText('Step 7 · Free play');
      const swinging = await indicators(page);
      expect(swinging.state).not.toBe('setup');
      expect(swinging.word).not.toBe('SETTING UP');
      expect(swinging.cause).not.toContain('Step ');
    });
  });
}
