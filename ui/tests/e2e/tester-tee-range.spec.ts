import { expect, test, type Page, type Route } from '@playwright/test';
import { KIOSK_VIEWPORTS } from './helpers';

test.use({ hasTouch: true });

async function json(route: Route, body: object, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
}

function eligibility() {
  return {
    tester_id: '20260922-name',
    config_hash: 'fixture',
    eligible: true,
    checks: [],
    blockers: [],
    warnings: [],
    operator_confirmation: { confirmed: true, authority: 'operator', confirmed_at: 'now' },
    runtime_requirements: ['ops', 'camera', 'iwr6843', 'lis3dh'],
  };
}

type FlowState = {
  epoch_id: string;
  phase: string;
  reason: string;
  retry_phase?: string | null;
  evidence: Record<string, unknown>;
  solution: null | { status: string; selected_range_m: number | null };
};

async function base(page: Page, initial: FlowState | null = null) {
  let state = initial;
  const next: Record<string, string> = {
    start: 'needs_empty',
    start_over: 'needs_empty',
    capture_empty: 'needs_ball',
    capture_ball: 'needs_camera_arm5',
    start_camera_arm5: 'camera_arm5_capturing',
    evaluate_camera_arm5: 'needs_camera_arm6',
    start_camera_arm6: 'camera_arm6_capturing',
    evaluate_camera_arm6: 'raw_only',
    retry: 'needs_empty',
  };
  await page.route('**/api/tester/setup-eligibility?**', (route) => json(route, eligibility()));
  await page.route('**/api/tester/status**', (route) => json(route, {}));
  await page.route('**/api/tester/attempts?**', (route) => json(route, { scopes: [] }));
  await page.route('**/api/tester/ladder?**', (route) => json(route, { ladder: null, stopped: true }));
  await page.route('**/api/tester/tee-range**', (route) => {
    if (route.request().method() === 'GET') return json(route, { state });
    const action = route.request().postDataJSON().action as string;
    const phase = next[action];
    state = {
      epoch_id: action === 'start_over' || !state ? `epoch-${action}` : state.epoch_id,
      phase,
      reason: phase,
      evidence:
        phase === 'raw_only'
          ? {
              iwr_candidate: { radar_slant_range_m: 1.52 },
              camera_arm5_candidate: { radar_slant_range_m: 1.5 },
              camera_arm6_candidate: { radar_slant_range_m: 1.51 },
            }
          : {},
      solution:
        phase === 'raw_only' ? { status: 'unresolved', selected_range_m: null } : null,
    };
    return json(route, { state });
  });
  return {
    state: () => state,
    setState: (value: FlowState) => {
      state = value;
    },
  };
}

test('walks the main automatic-range prompts and reconstructs after reload', async ({ page }) => {
  await base(page);
  await page.goto('/tester.html');
  const action = page.locator('#tee-range-action');

  await expect(action).toHaveText('Start automatic range');
  await action.tap();
  await expect(action).toHaveText('Capture empty hitting area');
  await action.tap();
  await expect(action).toHaveText('Capture ball at address');
  await page.reload();
  await expect(action).toHaveText('Capture ball at address');
  await action.tap();
  await expect(action).toHaveText('Open 1280×800 camera');
  await action.tap();
  await expect(action).toHaveText('Save 1280×800 observation');
  await action.tap();
  await expect(action).toHaveText('Open 640×400 camera');
  await action.tap();
  await expect(action).toHaveText('Save 640×400 observation');
  await action.tap();

  await expect(action).toHaveText('Evidence complete — raw-only mode');
  await expect(page.locator('#automatic-range')).toContainText('IWR apparent range 1.520 m');
  await expect(page.locator('#automatic-range')).toContainText('canonical range withheld');
  await expect(page.getByRole('button', { name: 'C. Start the exposure ladder' })).toBeEnabled();
});

test('shows retry and a qualified resolved range without accepting tape input', async ({ page }) => {
  const fixture = await base(page, {
    epoch_id: 'epoch-retry',
    phase: 'retryable_failure',
    reason: 'empty_capture_unusable',
    retry_phase: 'needs_empty',
    evidence: {},
    solution: null,
  });
  await page.goto('/tester.html');
  await expect(page.locator('#tee-range-action')).toHaveText('Retry this step');
  await page.locator('#tee-range-action').tap();
  await expect(page.locator('#tee-range-action')).toHaveText('Capture empty hitting area');

  fixture.setState({
    epoch_id: 'epoch-resolved',
    phase: 'resolved',
    reason: 'qualified_static_iwr_supported_by_camera_arm5',
    evidence: {
      iwr_candidate: { radar_slant_range_m: 1.524 },
      camera_arm5_candidate: { radar_slant_range_m: 1.51 },
      camera_arm6_candidate: { radar_slant_range_m: 1.52 },
    },
    solution: { status: 'resolved', selected_range_m: 1.524 },
  });
  await page.reload();
  await expect(page.locator('#automatic-range')).toContainText('canonical range 1.524 m');
  await expect(page.locator('#tee-mm')).toBeHidden();
});

test('requires start over when setup admission changed', async ({ page }) => {
  await base(page, {
    epoch_id: 'epoch-invalid-setup',
    phase: 'retryable_failure',
    reason: 'setup_admission_changed_start_over_required',
    retry_phase: null,
    evidence: {},
    solution: null,
  });
  await page.goto('/tester.html');

  await expect(page.locator('#tee-range-action')).toHaveText('Start over required');
  await expect(page.locator('#tee-range-action')).toBeDisabled();
  await expect(page.locator('#tee-range-restart')).toBeVisible();
});

for (const viewport of KIOSK_VIEWPORTS) {
  test(`automatic-range touch step fits at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await base(page, {
      epoch_id: 'epoch-size',
      phase: 'needs_ball',
      reason: 'place_ball_at_address_without_moving_rig',
      evidence: {},
      solution: null,
    });
    await page.goto('/tester.html');
    const card = page.locator('#automatic-range');
    await card.scrollIntoViewIfNeeded();
    const button = page.locator('#tee-range-action');
    const box = await button.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.width).toBeGreaterThanOrEqual(44);
    expect(box!.height).toBeGreaterThanOrEqual(44);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}
