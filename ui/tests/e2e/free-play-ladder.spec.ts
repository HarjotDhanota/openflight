import { expect, test, type Page, type Route } from '@playwright/test';
import { DEFAULT_TESTER, lensState, mockConfirmedPlacementBox, useTester } from './testerHelpers';

/**
 * Free play's exposure study on the tester page (19 Oct): one resolution per
 * session, tapped by the tester, then progress only. The tester never acts on it.
 */
const VIEWPORTS = [
  { width: 1024, height: 600 },
  { width: 800, height: 480 },
] as const;

async function fulfillJson(route: Route, payload: object, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(payload) });
}

function study(overrides: object = {}) {
  return {
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
      {
        rung_id: 'half-150',
        exposure_us: 150,
        gain: 2,
        status: 'pending',
        skip_reason: null,
        swings: 0,
        counts: { green: 0, amber: 0, red: 0, no_capture: 0, error: 0 },
      },
      {
        rung_id: 'half-15',
        exposure_us: 15,
        gain: null,
        status: 'skipped',
        skip_reason: 'it needs 1.8x more light',
        swings: 0,
        counts: { green: 0, amber: 0, red: 0, no_capture: 0, error: 0 },
      },
    ],
    ...overrides,
  };
}

async function mockSetupDone(page: Page, lens = lensState()) {
  await mockConfirmedPlacementBox(page);
  await page.route('**/api/tester/lens?**', (route) => fulfillJson(route, lens));
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
  await page.route('**/api/camera/preview.jpg**', (route) => route.fulfill({ status: 204 }));
  await page.route('**/api/tester/attempts?**', (route) => fulfillJson(route, { schema_version: 1, scopes: [] }));
  await page.route('**/api/tester/ladder?**', (route) => fulfillJson(route, { ladder: { rungs: {} }, stopped: true }));
}

for (const viewport of VIEWPORTS) {
  test.describe(`free play's exposure study at ${viewport.width}x${viewport.height}`, () => {
    test.use({ viewport, hasTouch: true });

    test('the tester taps one resolution, then sees progress without being asked anything', async ({ page }) => {
      await useTester(page);
      await mockSetupDone(page);
      let freePlay: object = { enabled: false };
      const starts: Record<string, unknown>[] = [];
      await page.route('**/api/tester/status**', (route) => fulfillJson(route, { free_play: freePlay }));
      await page.route('**/api/tester/run', async (route) => {
        const body = route.request().postDataJSON();
        starts.push(body);
        freePlay = {
          enabled: true,
          tester_id: DEFAULT_TESTER,
          run_id: 'run-1',
          phase: 'recording',
          resolution: study().resolution,
          exposure_study: study(),
        };
        await fulfillJson(route, { job: { state: 'running', action: 'free_play' }, free_play: freePlay }, 202);
      });
      await page.goto('/tester.html');

      const start = page.locator('#step-free-play');
      const full = page.locator('#free-play-resolution button[data-resolution="arm5"]');
      const half = page.locator('#free-play-resolution button[data-resolution="arm6"]');
      await expect(full).toHaveText('1280×800 (lens default)');
      await expect(half).toHaveText('640×400');
      // the lens default is offered, never assumed: nothing starts until a tap
      await expect(full).toHaveAttribute('aria-pressed', 'false');
      await expect(start).toBeDisabled();
      await half.scrollIntoViewIfNeeded();
      for (const button of [full, half]) {
        expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(44);
      }
      await half.tap();
      await expect(half).toHaveAttribute('aria-pressed', 'true');
      await expect(page.locator('#free-play-resolution-hint')).toHaveText('This session records at 640×400.');
      await expect(start).toBeDisabled();
      await page.locator('#first-club button[data-club="7-iron"]').tap();
      await expect(start).toBeEnabled();
      await page.screenshot({
        path: test.info().outputPath(`tester-resolution-${viewport.width}x${viewport.height}.png`),
      });

      await start.tap();
      await expect.poll(() => starts.length).toBe(1);
      expect(starts[0]).toMatchObject({
        action: 'free_play',
        resolution: 'arm6',
        club: '7-iron',
        tester_id: DEFAULT_TESTER,
      });

      const line = page.locator('#free-play-study');
      await expect(line).toHaveText('Exposure study: 300 µs · swing 3 of 5 · setting 1 of 2');
      const rungs = page.locator('#free-play-rungs li');
      await expect(rungs).toHaveCount(3);
      await expect(rungs.nth(0)).toHaveText('300 µs × gain 1.00: active, 2 of 5 swings · 1 green, 1 red');
      await expect(rungs.nth(2)).toHaveText('15 µs: skipped (it needs 1.8x more light)');
      // progress only: no verdict card, no photo, no per-swing prompt, no resolution change
      await expect(page.locator('#verdict-card')).toBeHidden();
      await expect(page.locator('#ladder-controls')).toBeHidden();
      await expect(page.locator('#free-play-resolution-row')).toBeHidden();
      await expect(page.locator('#photo-handoff')).toBeHidden();
      const lineBox = (await line.boundingBox())!;
      expect(lineBox.x + lineBox.width).toBeLessThanOrEqual(viewport.width);
      await line.scrollIntoViewIfNeeded();
      await page.screenshot({
        path: test.info().outputPath(`tester-progress-${viewport.width}x${viewport.height}.png`),
      });

      freePlay = { ...freePlay, exposure_study: study({ phase: 'automatic', text: 'Automatic exposure' }) };
      await expect(line).toHaveText('Automatic exposure');
    });

    test('a resolution the lens disables is not offered', async ({ page }) => {
      await useTester(page);
      const lens = lensState();
      lens.lenses[0].settings = { modes: ['arm5'], default_arm: 'arm5', ladder: { arm5: [300, 150], arm6: [] } };
      await mockSetupDone(page, lens);
      await page.route('**/api/tester/status**', (route) => fulfillJson(route, { free_play: { enabled: false } }));
      await page.goto('/tester.html');

      await expect(page.locator('#free-play-resolution button[data-resolution="arm5"]')).toBeVisible();
      await expect(page.locator('#free-play-resolution button[data-resolution="arm6"]')).toBeHidden();
      await expect(page.locator('#step-free-play')).toBeDisabled();
    });
  });
}
