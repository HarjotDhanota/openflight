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

const TINY_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
  'base64'
);

type GuidedState =
  | 'warming'
  | 'exposure_searching'
  | 'ball_not_found'
  | 'optical_gates_failed'
  | 'lighting_required'
  | 'exposure_locked';

function guidedDisplay(state: GuidedState, overrides: Record<string, unknown> = {}) {
  const locked = state === 'exposure_locked';
  return {
    schema: 'openflight.tester_guided_camera_display.v1',
    state,
    reason: locked ? 'ready to save' : 'waiting for a stable ball at the current exposure',
    save_ready: locked,
    controls: {
      requested: { exposure_us: 1250, gain: 8 },
      applied: { exposure_us: 1248, gain: 8 },
      match: true,
    },
    exposure: {
      status: locked ? 'locked' : 'searching',
      stage: 'refine',
      current_step: locked ? null : { exposure_us: 1250, gain: 8 },
      attempts: 4,
      lock: locked ? { exposure_us: 1250, gain: 8, applied_exposure_us: 1248, applied_gain: 8 } : null,
    },
    ball_outline: locked ? { x_px: 641.2, y_px: 502.7, diameter_px: 24.4 } : null,
    ...overrides,
  };
}

function rangeDisplay(current: FlowState | null) {
  const evidence = current?.evidence ?? {};
  const resolved = current?.solution?.status === 'resolved';
  const item = (key: string) => {
    const value = (evidence[key] as { radar_slant_range_m?: number } | undefined)?.radar_slant_range_m;
    if (value == null) return { state: 'pending', range_m: null, diagnostic_range_m: null, reason: null };
    return resolved
      ? { state: 'accepted', range_m: value, diagnostic_range_m: null, reason: null }
      : {
          state: 'unqualified',
          range_m: null,
          diagnostic_range_m: value,
          reason: 'not qualified for promotion on this setup',
        };
  };
  return {
    schema: 'openflight.tester_tee_range_display.v1',
    iwr: { ...item('iwr_candidate'), label: 'bias-corrected IWR slant range' },
    camera: { arm5: item('camera_arm5_candidate'), arm6: item('camera_arm6_candidate') },
    canonical: resolved
      ? { state: 'resolved', range_m: current?.solution?.selected_range_m ?? null, reason: null }
      : { state: 'withheld', range_m: null, reason: current?.solution ? 'qualification_artifact_missing' : null },
  };
}

async function base(page: Page, initial: FlowState | null = null) {
  let state = initial;
  let postFailure: { body: object; status: number } | null = null;
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
  await page.route('**/api/tester/live', (route) => {
    const armId = state?.phase === 'camera_arm5_capturing'
      ? 'arm5'
      : state?.phase === 'camera_arm6_capturing'
        ? 'arm6'
        : null;
    return json(route, {
      running: Boolean(armId),
      arm_id: armId,
      owner: armId && state
        ? {
            kind: 'guided_tee_range',
            tester_id: '20260922-name',
            epoch_id: state.epoch_id,
            arm_id: armId,
          }
        : null,
      stats: armId ? { mean: 70, p99: 140, max: 180, clipped_pct: 0 } : null,
      association: armId
        ? {
            status: 'selected',
            selected: {
              x_px: 641.2,
              y_px: 502.7,
              diameter_px: 24.4,
              floor_radar_range_m: 1.527,
            },
            candidates: [],
            stable_count: 3,
            stable_span_s: 1,
            save_eligible: true,
            frame_sequence: 12,
          }
        : null,
      guided_display: armId ? guidedDisplay('exposure_locked') : null,
    });
  });
  await page.route('**/api/tester/live.png?**', (route) =>
    route.fulfill({ status: 200, contentType: 'image/png', body: TINY_PNG })
  );
  await page.route('**/api/tester/tee-range**', (route) => {
    if (route.request().method() === 'GET') return json(route, { state, display: rangeDisplay(state) });
    if (postFailure) return json(route, postFailure.body, postFailure.status);
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
    return json(route, { state, display: rangeDisplay(state) });
  });
  return {
    state: () => state,
    setState: (value: FlowState) => {
      state = value;
    },
    setPostFailure: (value: { body: object; status: number } | null) => {
      postFailure = value;
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
  await expect(page.locator('#automatic-range')).toContainText(
    'bias-corrected IWR slant range unqualified — not qualified for promotion on this setup · diagnostic 1.520 m, not used'
  );
  await expect(page.locator('#automatic-range')).toContainText('canonical range withheld');
  await expect(page.getByRole('button', { name: 'C. Start the exposure ladder' })).toBeEnabled();
});

test('shows the server-owned guided camera frame without starting another live view', async ({ page }) => {
  await base(page, {
    epoch_id: 'epoch-camera',
    phase: 'needs_camera_arm5',
    reason: 'needs_camera_arm5',
    evidence: {},
    solution: null,
  });
  let statusPolls = 0;
  let framePolls = 0;
  let livePosts = 0;
  let frameUrl = '';
  await page.route('**/api/tester/live', (route) => {
    if (route.request().method() !== 'GET') {
      livePosts += 1;
      return json(route, { error: 'guided preview must not start live view' }, 409);
    }
    statusPolls += 1;
    return json(route, {
      running: true,
      arm_id: 'arm5',
      owner: {
        kind: 'guided_tee_range',
        tester_id: '20260922-name',
        epoch_id: 'epoch-camera',
        arm_id: 'arm5',
      },
      stats: statusPolls > 1 ? { mean: 72.1, p99: 140, max: 181, clipped_pct: 0 } : null,
      association: statusPolls > 1
        ? {
            status: 'selected',
            selected: {
              x_px: 641.2,
              y_px: 502.7,
              diameter_px: 24.4,
              floor_radar_range_m: 1.527,
            },
            candidates: [],
            stable_count: 3,
            stable_span_s: 1,
            save_eligible: true,
            frame_sequence: 12,
          }
        : null,
      guided_display: statusPolls > 1 ? guidedDisplay('exposure_locked') : null,
    });
  });
  await page.route('**/api/tester/live.png?**', async (route) => {
    framePolls += 1;
    frameUrl = route.request().url();
    if (statusPolls < 2) return json(route, { error: 'no live frame yet' }, 503);
    await route.fulfill({ status: 200, contentType: 'image/png', body: TINY_PNG });
  });
  await page.goto('/tester.html');

  await page.locator('#tee-range-action').tap();

  const preview = page.locator('#tee-range-camera-preview');
  await expect(preview).toBeVisible();
  await expect(page.locator('#tee-range-camera-status')).toContainText('Waiting for the first frame');
  await expect(page.locator('#tee-range-camera-detector')).toContainText('Ball detector is warming up');
  await expect(page.locator('#automatic-range-summary')).toContainText('The reference camera is live');
  await expect(page.locator('#tee-range-camera-status')).toContainText('Live 1280×800 frame ready');
  await expect(page.locator('#tee-range-camera-detector')).toContainText(
    'Camera-only search: ball selected · x 641.2 · y 502.7 · diameter 24.4 px · range 1.527 m · stable and ready to save'
  );
  await expect(page.locator('#tee-range-camera-frame')).toHaveAttribute('src', /view=overlay/);
  expect(statusPolls).toBeGreaterThan(1);
  expect(framePolls).toBeGreaterThan(0);
  expect(frameUrl).toContain('view=overlay');
  expect(livePosts).toBe(0);

  await page.locator('#tee-range-action').tap();
  await expect(preview).toBeHidden();
});

test('reports the live detector reason without treating it as a camera failure', async ({ page }) => {
  await base(page, {
    epoch_id: 'epoch-no-ball',
    phase: 'camera_arm5_capturing',
    reason: 'camera_arm5_warming',
    evidence: {},
    solution: null,
  });
  await page.route('**/api/tester/live', (route) =>
    json(route, {
      running: true,
      arm_id: 'arm5',
      owner: {
        kind: 'guided_tee_range',
        tester_id: '20260922-name',
        epoch_id: 'epoch-no-ball',
        arm_id: 'arm5',
      },
      stats: { mean: 71, p99: 139, max: 178, clipped_pct: 0 },
      association: {
        status: 'not_found',
        selected: null,
        candidates: [],
        stable_count: 0,
        stable_span_s: 0,
        save_eligible: false,
        readiness_reason: 'no reference ball was found by the camera-only estimator',
        fallback: {
          used: true,
          reason: 'the static IWR candidate was rejected',
        },
      },
    })
  );
  await page.goto('/tester.html');

  const detector = page.locator('#tee-range-camera-detector');
  await expect(detector).toContainText(
    'Camera-only broad fallback: No reference ball was found. Save remains disabled · radar hint fallback: the static IWR candidate was rejected.'
  );
  await expect(detector).toHaveClass(/note/);
  await expect(detector).not.toHaveClass(/problem/);
  await expect(page.locator('#automatic-range-summary')).toContainText('The reference camera is live');
  await expect(page.locator('#tee-range-action')).toHaveText('Save 1280×800 observation');
  await expect(page.locator('#tee-range-action')).toBeDisabled();
});

test('withholds Save and a confident verdict when camera-only association is ambiguous', async ({ page }) => {
  await base(page, {
    epoch_id: 'epoch-ambiguous',
    phase: 'camera_arm5_capturing',
    reason: 'camera_arm5_warming',
    evidence: {},
    solution: null,
  });
  await page.route('**/api/tester/live', (route) =>
    json(route, {
      running: true,
      arm_id: 'arm5',
      owner: {
        kind: 'guided_tee_range',
        tester_id: '20260922-name',
        epoch_id: 'epoch-ambiguous',
        arm_id: 'arm5',
      },
      stats: { mean: 71, p99: 139, max: 178, clipped_pct: 0 },
      association: {
        status: 'ambiguous',
        selected: null,
        candidates: [{ x_px: 600 }, { x_px: 700 }],
        stable_count: 0,
        stable_span_s: 0,
        save_eligible: false,
        readiness_reason: 'multiple camera-only candidates remain plausible',
      },
    })
  );
  await page.goto('/tester.html');

  await expect(page.locator('#tee-range-camera-detector')).toContainText(
    'Camera-only search: Multiple candidates remain plausible. Save remains disabled.'
  );
  await expect(page.locator('#tee-range-action')).toBeDisabled();
  await expect(page.locator('#automatic-range-summary')).toContainText('The reference camera is live');
});

test('labels radar-conditioned readiness as provisional until independent Save', async ({ page }) => {
  await base(page, {
    epoch_id: 'epoch-radar-guided',
    phase: 'camera_arm5_capturing',
    reason: 'camera_arm5_warming',
    evidence: {},
    solution: null,
  });
  await page.route('**/api/tester/live', (route) =>
    json(route, {
      running: true,
      arm_id: 'arm5',
      owner: {
        kind: 'guided_tee_range',
        tester_id: '20260922-name',
        epoch_id: 'epoch-radar-guided',
        arm_id: 'arm5',
      },
      stats: { mean: 71, p99: 139, max: 178, clipped_pct: 0 },
      association: {
        status: 'selected',
        selected: {
          x_px: 641.2,
          y_px: 502.7,
          diameter_px: 24.4,
          floor_radar_range_m: 1.527,
        },
        stable_count: 3,
        stable_span_s: 1,
        save_eligible: true,
        independent: false,
        promotion_eligible: false,
        dependency_facts: { iwr_range_used: true },
      },
      guided_display: guidedDisplay('exposure_locked'),
    })
  );
  await page.goto('/tester.html');

  const detector = page.locator('#tee-range-camera-detector');
  await expect(detector).toContainText('Radar-guided provisional search: ball selected');
  await expect(detector).toContainText('independent full-frame check pending on Save');
  await expect(page.locator('#tee-range-action')).toBeEnabled();
});

test('guided camera errors stay beside the preview and stale tester polls are ignored', async ({ page }) => {
  await base(page, {
    epoch_id: 'epoch-camera-error',
    phase: 'camera_arm6_capturing',
    reason: 'camera_arm6_warming',
    evidence: {},
    solution: null,
  });
  let releaseStatus: (() => void) | null = null;
  let statusRequests = 0;
  await page.route('**/api/tester/live', async (route) => {
    statusRequests += 1;
    if (statusRequests === 1) {
      return json(route, {
        running: false,
        arm_id: 'arm6',
        error: 'camera cable disconnected',
        owner: {
          kind: 'guided_tee_range',
          tester_id: '20260922-name',
          epoch_id: 'epoch-camera-error',
          arm_id: 'arm6',
        },
      });
    }
    await new Promise<void>((resolve) => {
      releaseStatus = resolve;
    });
    return json(route, {
      running: false,
      arm_id: 'arm6',
      error: 'camera cable disconnected',
      owner: {
        kind: 'guided_tee_range',
        tester_id: '20260922-name',
        epoch_id: 'epoch-camera-error',
        arm_id: 'arm6',
      },
    });
  });
  await page.goto('/tester.html');
  await expect(page.locator('#tee-range-camera-preview')).toBeVisible();
  await expect(page.locator('#tee-range-camera-status')).toContainText('Camera error: camera cable disconnected');
  await expect(page.locator('#tee-range-action')).toBeDisabled();
  await expect(page.locator('#automatic-range-summary')).toContainText('The second camera mode is live');
  await expect(page.locator('#automatic-range-summary')).not.toContainText('camera cable disconnected');
  await expect.poll(() => releaseStatus !== null).toBe(true);

  await page.locator('#tester-id').fill('20260922-other');
  releaseStatus?.();

  await expect(page.locator('#tee-range-camera-preview')).toBeHidden();
  await expect(page.locator('#automatic-range-summary')).toContainText(
    'Each camera step finds its own exposure'
  );
  await expect(page.locator('#automatic-range-summary')).not.toContainText('camera cable disconnected');
});

for (const viewport of KIOSK_VIEWPORTS) {
  test(`guided camera preview fits at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await base(page, {
      epoch_id: 'epoch-preview-size',
      phase: 'camera_arm5_capturing',
      reason: 'camera_arm5_warming',
      evidence: {},
      solution: null,
    });
    await page.route('**/api/tester/live', (route) =>
      json(route, {
        running: true,
        arm_id: 'arm5',
        owner: {
          kind: 'guided_tee_range',
          tester_id: '20260922-name',
          epoch_id: 'epoch-preview-size',
          arm_id: 'arm5',
        },
        stats: { mean: 70, p99: 140, max: 180, clipped_pct: 0 },
      })
    );
    await page.route('**/api/tester/live.png?**', (route) =>
      route.fulfill({ status: 200, contentType: 'image/png', body: TINY_PNG })
    );
    await page.goto('/tester.html');

    const image = page.locator('#tee-range-camera-frame');
    await expect(image).toBeVisible();
    const box = await image.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.height).toBeLessThanOrEqual(viewport.height * 0.45 + 1);
    expect(box!.width).toBeLessThanOrEqual(viewport.width);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}

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
  fixture.setState({
    ...fixture.state()!,
    evidence: {
      empty_capture: { usable: false },
      capture_failure: {
        capture_kind: 'empty',
        stage: 'connect',
        type: 'RuntimeError',
        message: 'no IWR6843 CLI found — board on, flashed, single-port fw?',
        remedy: 'Use the CP2105 Enhanced/UARTA interface (if00), press RESET, and retry.',
      },
    },
  });
  await page.reload();
  await expect(page.locator('#automatic-range-summary')).toContainText(
    'Empty IWR capture failed at connect (RuntimeError): no IWR6843 CLI found'
  );
  await expect(page.locator('#automatic-range-summary')).toContainText(
    'Remedy: Use the CP2105 Enhanced/UARTA interface (if00), press RESET, and retry.'
  );
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

test('guides reconfirmation after restart and retains the start-over response', async ({ page }) => {
  const retryable: FlowState = {
    epoch_id: 'epoch-before-restart',
    phase: 'retryable_failure',
    reason: 'empty_capture_unusable',
    retry_phase: 'needs_empty',
    evidence: {},
    solution: null,
  };
  const fixture = await base(page, retryable);
  await page.unroute('**/api/tester/setup-eligibility?**');
  let confirmed = false;
  await page.route('**/api/tester/setup-eligibility?**', (route) =>
    json(route, {
      ...eligibility(),
      eligible: confirmed,
      blockers: confirmed ? [] : [{ id: 'iwr6843_cli' }],
      operator_confirmation: { confirmed },
    })
  );
  await page.route('**/api/tester/setup-eligibility', (route) => {
    confirmed = true;
    return json(route, eligibility());
  });
  await page.goto('/tester.html');

  await page.locator('#tee-range-action').tap();
  await expect(page.locator('#automatic-range-summary')).toContainText(
    'The tester restarted. Run Check the hardware and reconfirm the physical setup, then start this automatic range over.'
  );

  await page.locator('#setup-physical-confirm').check();
  await page.locator('#setup-confirm').tap();
  fixture.setPostFailure({
    status: 409,
    body: {
      error: 'automatic tee-range setup admission changed; start over',
      start_over_required: true,
      state: { ...retryable, retry_phase: null, reason: 'setup_admission_changed_start_over_required' },
    },
  });
  await page.locator('#tee-range-action').tap();

  await expect(page.locator('#tee-range-action')).toHaveText('Start over required');
  await expect(page.locator('#tee-range-action')).toBeDisabled();
  await expect(page.locator('#automatic-range-summary')).toContainText(
    'automatic tee-range setup admission changed; start over'
  );
  await expect(page.locator('#automatic-range-summary')).toContainText(
    'Use “Ball or rig moved: start over” to begin a new setup epoch.'
  );
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

const CONNECT_FAILURE = {
  capture_kind: 'empty',
  stage: 'connect',
  type: 'RuntimeError',
  message: 'no IWR6843 CLI found — board on, flashed, single-port fw? Probes: /dev/ttyUSB0 (CP2105 Enhanced if00): no reply to help within 1.5 s',
  remedy: 'Press RESET and retry.',
};

test('a newer server failure replaces an earlier rejected-action message', async ({ page }) => {
  const fixture = await base(page, {
    epoch_id: 'epoch-live',
    phase: 'needs_empty',
    reason: 'needs_empty',
    evidence: {},
    solution: null,
  });
  await page.goto('/tester.html');
  fixture.setPostFailure({ status: 409, body: { error: 'the tee_range job owns the hardware' } });
  await page.locator('#tee-range-action').tap();
  await expect(page.locator('#automatic-range-summary')).toHaveText('the tee_range job owns the hardware');

  fixture.setState({
    epoch_id: 'epoch-live',
    phase: 'retryable_failure',
    reason: 'empty_capture_unusable',
    retry_phase: 'needs_empty',
    evidence: { empty_capture: { usable: false }, capture_failure: CONNECT_FAILURE },
    solution: null,
  });

  await expect(page.locator('#automatic-range-summary')).toContainText(
    'Empty IWR capture failed at connect (RuntimeError): no IWR6843 CLI found'
  );
});

test('legacy camera evidence polling never overwrites the guided failure', async ({ page }) => {
  await base(page, {
    epoch_id: 'epoch-failed',
    phase: 'retryable_failure',
    reason: 'empty_capture_unusable',
    retry_phase: 'needs_empty',
    evidence: { empty_capture: { usable: false }, capture_failure: CONNECT_FAILURE },
    solution: null,
  });
  let statusPolls = 0;
  await page.route('**/api/tester/status**', (route) => {
    statusPolls += 1;
    return json(route, {
      study: {
        arms: [
          {
            arm_id: 'arm5',
            label: 'Arm 5',
            isolates: 'reference',
            exposure_us: 300,
            target: 10,
            tee_range_camera_evidence: { status: 'pending', candidates: [] },
          },
        ],
      },
    });
  });
  await page.goto('/tester.html');
  await expect(page.locator('#automatic-range-summary')).toContainText('no IWR6843 CLI found');

  await expect.poll(() => statusPolls).toBeGreaterThan(0);

  // Read in the same task as the status render, before the 1 s range poll could repaint.
  const summary = await page.evaluate(async () => {
    await (window as unknown as { refresh: () => Promise<void> }).refresh();
    return document.getElementById('automatic-range-summary')?.textContent ?? '';
  });

  expect(summary).toContain('no IWR6843 CLI found');
  expect(summary).not.toContain('Camera range');
});

test('a failed hardware check shows the IWR6843 reason, not only the exit code', async ({ page }) => {
  await base(page);
  await page.route('**/api/tester/run', (route) => json(route, { job: { state: 'running' } }, 202));
  await page.route('**/api/tester/status**', (route) =>
    json(route, {
      job: {
        state: 'error',
        action: 'preflight',
        message: 'Failed with exit code 1',
        output: [
          '$ python scripts/iwr6843/check_cli.py',
          'IWR6843 CLI check failed: no IWR6843 CLI found — board on, flashed, single-port fw? Probes: /dev/ttyUSB0 (CP2105 Enhanced if00): no reply to help within 1.5 s',
          '',
        ],
      },
    })
  );
  await page.goto('/tester.html');

  await page.locator('#step-check').tap();

  await expect(page.locator('#check-verdict')).toContainText('failed: Failed with exit code 1');
  await expect(page.locator('#check-verdict')).toContainText(
    '/dev/ttyUSB0 (CP2105 Enhanced if00): no reply to help within 1.5 s'
  );
});

const GUIDED_CAMERA_CASES = [
  {
    name: 'a held exposure lock',
    display: guidedDisplay('exposure_locked'),
    text: 'Exposure locked at 1250 µs × 8.00',
    saveEnabled: true,
    problem: false,
  },
  {
    name: 'a missing ball',
    display: guidedDisplay('ball_not_found', { reason: 'no reference ball at the current exposure' }),
    text: 'No reference ball at this exposure',
    saveEnabled: false,
    problem: false,
  },
  {
    name: 'insufficient light',
    display: guidedDisplay('lighting_required', {
      reason: 'no visible setting passed the ball-pixel gates',
      exposure: { status: 'lighting_required', stage: 'refine', current_step: null, attempts: 30, lock: null },
    }),
    text: 'More light is needed on the ball: no visible setting passed the ball-pixel gates',
    saveEnabled: false,
    problem: true,
  },
  {
    name: 'a clipped ball',
    display: guidedDisplay('optical_gates_failed', { reason: 'ball pixels failed: clipped' }),
    text: 'ball pixels failed: clipped',
    saveEnabled: false,
    problem: false,
  },
  {
    name: 'controls the camera has not applied',
    display: guidedDisplay('exposure_searching', {
      reason: 'waiting for the camera to apply the requested controls',
      controls: {
        requested: { exposure_us: 1250, gain: 8 },
        applied: { exposure_us: 298, gain: 12 },
        match: false,
      },
    }),
    text: 'Requested 1250 µs × 8.00; camera applied 298 µs × 12.00',
    saveEnabled: false,
    problem: false,
  },
];

for (const viewport of KIOSK_VIEWPORTS) {
  for (const scenario of GUIDED_CAMERA_CASES) {
    test(`guided camera reports ${scenario.name} at ${viewport.width}x${viewport.height}`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await base(page, {
        epoch_id: 'epoch-exposure',
        phase: 'camera_arm5_capturing',
        reason: 'camera_arm5_warming',
        evidence: {},
        solution: null,
      });
      await page.route('**/api/tester/live', (route) =>
        json(route, {
          running: true,
          arm_id: 'arm5',
          owner: {
            kind: 'guided_tee_range',
            tester_id: '20260922-name',
            epoch_id: 'epoch-exposure',
            arm_id: 'arm5',
          },
          stats: { mean: 21.3, p99: 30, max: 38, clipped_pct: 0 },
          association: { status: 'not_found', selected: null, save_eligible: scenario.saveEnabled },
          guided_display: scenario.display,
        })
      );
      await page.goto('/tester.html');

      const line = page.locator('#tee-range-camera-exposure');
      await line.scrollIntoViewIfNeeded();
      await expect(line).toContainText(scenario.text);
      await expect(line).toHaveAttribute('data-state', scenario.display.state);
      await expect(line).toHaveClass(scenario.problem ? 'problem' : 'note');
      const action = page.locator('#tee-range-action');
      if (scenario.saveEnabled) await expect(action).toBeEnabled();
      else await expect(action).toBeDisabled();
      const box = await line.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    });
  }

  test(`a rejected radar number is shown only as a diagnostic at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const state = {
      epoch_id: 'epoch-rejected-radar',
      phase: 'needs_camera_arm5',
      reason: 'needs_camera_arm5',
      evidence: { iwr_candidate: { radar_slant_range_m: 1.621 } },
      solution: null,
    };
    await base(page, state);
    await page.route('**/api/tester/tee-range**', (route) =>
      json(route, {
        state,
        display: {
          iwr: {
            state: 'rejected',
            range_m: null,
            diagnostic_range_m: 1.621,
            reason: 'rejected_scene_changed: a static reflector disappeared between captures',
            label: 'bias-corrected IWR slant range',
          },
          camera: {
            arm5: { state: 'pending', range_m: null, diagnostic_range_m: null, reason: null },
            arm6: { state: 'pending', range_m: null, diagnostic_range_m: null, reason: null },
          },
          canonical: { state: 'withheld', range_m: null, reason: 'needs_camera_arm5' },
        },
      })
    );
    await page.goto('/tester.html');

    const radar = page.locator('#automatic-range-values [data-state="rejected"]');
    await radar.scrollIntoViewIfNeeded();
    await expect(radar).toContainText('bias-corrected IWR slant range rejected — rejected_scene_changed');
    await expect(radar).toContainText('diagnostic 1.621 m, not used');
    await expect(radar).toHaveClass('problem');
    await expect(page.locator('#automatic-range-values')).not.toContainText(
      'bias-corrected IWR slant range 1.621 m'
    );
    const box = await radar.boundingBox();
    expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });

  test(`withheld fusion states its reason at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await base(page, {
      epoch_id: 'epoch-withheld',
      phase: 'raw_only',
      reason: 'qualification_artifact_missing',
      evidence: {
        iwr_candidate: { radar_slant_range_m: 1.52 },
        camera_arm5_candidate: { radar_slant_range_m: 1.5 },
        camera_arm6_candidate: { radar_slant_range_m: 1.51 },
      },
      solution: { status: 'unresolved', selected_range_m: null },
    });
    await page.goto('/tester.html');

    const canonical = page.locator('#automatic-range-values [data-state="withheld"]');
    await canonical.scrollIntoViewIfNeeded();
    await expect(canonical).toContainText('canonical range withheld — qualification artifact missing');
    const box = await canonical.boundingBox();
    expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}
