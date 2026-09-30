import { expect, test, type Page, type Route } from '@playwright/test';
import { KIOSK_VIEWPORTS, mockConfirmedPlacementBox, placementBoxState } from './helpers';

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
  sequence?: number;
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
  | 'looking_for_ball'
  | 'optical_gates_failed'
  | 'lighting_required'
  | 'ball_not_identified'
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
  await mockConfirmedPlacementBox(page);
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
              size_radar_range_m: 1.527,
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

  await expect(action).toHaveText('Evidence complete — no ball found');
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
              size_radar_range_m: 1.527,
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
    'Camera search in the patch: ball selected · x 641.2 · y 502.7 · diameter 24.4 px · range 1.527 m · stable and ready to save'
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
    'Camera search in the patch: No ball found in the patch. The ball may be outside it: move the ball or the patch. Save remains disabled.'
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
    'Camera search in the patch: Several ball-like things are in the patch: keep only the ball in it. Save remains disabled.'
  );
  await expect(page.locator('#tee-range-action')).toBeDisabled();
  await expect(page.locator('#automatic-range-summary')).toContainText('The reference camera is live');
});

// P8-2: the camera never uses the radar while it searches, so nothing it shows is radar-guided.
test('the camera search is labelled as the patch search, never radar-guided', async ({ page }) => {
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
          size_radar_range_m: 1.527,
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
  await expect(detector).toContainText('Camera search in the patch: ball selected');
  await expect(detector).not.toContainText('Radar-guided');
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
    'camera and radar checks work from'
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

test('an earlier failure never resurfaces beside a newer one', async ({ page }) => {
  // audit T8: a camera failure was retried and fixed; later the radar failed
  const oldCamera = {
    arm_id: 'arm5',
    stage: 'live_view',
    message: 'camera cable disconnected',
    remedy: 'Check the camera connection.',
    sequence: 5,
  };
  const fixture = await base(page, {
    epoch_id: 'epoch-t8',
    sequence: 9,
    phase: 'retryable_failure',
    reason: 'ball_present_capture_unusable',
    retry_phase: 'needs_ball',
    evidence: {
      camera_capture_failure: oldCamera,
      capture_failure: { ...CONNECT_FAILURE, capture_kind: 'ball_present', sequence: 9 },
    },
    solution: null,
  });
  await page.goto('/tester.html');
  const summary = page.locator('#automatic-range-summary');
  await expect(summary).toContainText('Ball-present IWR capture failed at connect');
  await expect(summary).not.toContainText('camera cable disconnected');

  // a failure that set no message of its own shows neither old one
  fixture.setState({
    ...fixture.state()!,
    sequence: 12,
    reason: 'camera_arm6_evaluation_failed',
    retry_phase: 'needs_camera_arm6',
  });
  await expect(summary).not.toContainText('IWR capture failed');
  await expect(summary).not.toContainText('camera cable disconnected');
});

for (const key of ['size_radar_range_m', 'floor_radar_range_m']) {
  test(`camera range evidence recorded as ${key} is shown`, async ({ page }) => {
    // wiring audit S11: arm records written before the rename keep floor_radar_range_m.
    // The arm record's panel shows when the guided range endpoint does not answer.
    await base(page);
    await page.route('**/api/tester/tee-range**', (route) => json(route, { error: 'unavailable' }, 503));
    await page.route('**/api/tester/status**', (route) =>
      json(route, {
        study: {
          arms: [
            {
              arm_id: 'arm5',
              label: 'Arm 5',
              isolates: 'reference',
              exposure_us: 300,
              target: 10,
              tee_range_camera_evidence: {
                status: 'selected',
                confidence: 'experimental',
                candidates: [{}],
                selected: {
                  [key]: 1.234,
                  floor_range_uncertainty_m: 0.02,
                  size_camera_range_m: 1.2,
                  size_range_uncertainty_m: 0.02,
                  range_disagreement_m: null,
                  consistency_sigma: 0.4,
                  confidence: 'experimental',
                },
              },
            },
          ],
        },
      })
    );
    await page.goto('/tester.html');
    await expect(page.locator('#automatic-range-values')).toContainText('radar range from size 1.234 m');
  });
}

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
    name: 'a look without the ball',
    display: guidedDisplay('looking_for_ball', { reason: 'looking for the ball in the patch' }),
    text: 'Looking for the ball in the patch',
    saveEnabled: false,
    problem: false,
  },
  {
    name: 'no ball in the patch',
    display: guidedDisplay('ball_not_found', {
      reason: 'No ball found in the patch. The ball may be outside it: move the ball or the patch.',
    }),
    text: 'No ball found in the patch',
    saveEnabled: false,
    problem: true,
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
    name: 'several ball-like objects',
    display: guidedDisplay('ball_not_identified', {
      reason: 'several ball-like objects are visible and the ball could not be picked out',
    }),
    text: 'Several ball-like objects are in view; the ball cannot be picked out',
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

  test(`the summary says what swings get and flags a 640x400 disagreement at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const state = {
      epoch_id: 'epoch-pending-swings',
      phase: 'raw_only',
      reason: 'qualification_artifact_missing',
      evidence: {},
      solution: { status: 'unresolved', selected_range_m: null },
    };
    const pending = { state: 'pending', range_m: null, diagnostic_range_m: null, reason: null };
    await base(page, state);
    await page.route('**/api/tester/tee-range**', (route) =>
      json(route, {
        state,
        display: {
          iwr: { ...pending, label: 'bias-corrected IWR slant range' },
          camera: { arm5: pending, arm6: pending },
          canonical: { state: 'withheld', range_m: null, reason: 'qualification_artifact_missing' },
          swings: {
            state: 'pending',
            range_m: null,
            message:
              'Swings start with the tee range pending: no radar range was accepted, so launch and club metrics that need it are withheld.',
          },
          validation: {
            state: 'validation_disagrees',
            message: '640x400 1.500 m vs 1280x800 1.200 m (10.6 sigma); the setup is flagged, not blocked.',
          },
        },
      })
    );
    await page.goto('/tester.html');

    const swings = page.locator('#automatic-range-values [data-state="swings-pending"]');
    await swings.scrollIntoViewIfNeeded();
    await expect(swings).toContainText('swings get tee range pending');
    await expect(swings).toContainText('withheld');
    const validation = page.locator('#automatic-range-values [data-state="validation_disagrees"]');
    await expect(validation).toContainText('640×400 check disagrees');
    await expect(validation).toContainText('flagged, not blocked');
    await expect(validation).toHaveClass('problem');
    for (const line of [swings, validation]) {
      const box = await line.boundingBox();
      expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width);
    }
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

for (const viewport of KIOSK_VIEWPORTS) {
  test(`a lighting failure offers an unqualified raw-evidence save at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const state = {
      epoch_id: 'epoch-dark',
      phase: 'camera_arm5_capturing',
      reason: 'camera_arm5_warming',
      evidence: {},
      solution: null,
    };
    await base(page, state);
    let display = guidedDisplay('lighting_required', {
      reason: 'no visible setting passed the ball-pixel gates',
    });
    await page.route('**/api/tester/live', (route) =>
      json(route, {
        running: true,
        arm_id: 'arm5',
        owner: { kind: 'guided_tee_range', tester_id: '20260922-name', epoch_id: 'epoch-dark', arm_id: 'arm5' },
        stats: { mean: 21.3, p99: 30, max: 38, clipped_pct: 0 },
        association: { status: 'not_found', selected: null, save_eligible: false },
        guided_display: display,
      })
    );
    const posted: string[] = [];
    await page.route('**/api/tester/tee-range**', (route) => {
      if (route.request().method() === 'GET') return json(route, { state, display: rangeDisplay(state) });
      posted.push(route.request().postDataJSON().action);
      return json(route, { state, display: rangeDisplay(state) });
    });
    await page.goto('/tester.html');

    const diagnostic = page.locator('#tee-range-diagnostic');
    await diagnostic.scrollIntoViewIfNeeded();
    await expect(diagnostic).toBeVisible();
    await expect(page.locator('#tee-range-action')).toBeDisabled();
    const box = await diagnostic.boundingBox();
    expect(box!.height).toBeGreaterThanOrEqual(44);
    expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width);
    await diagnostic.tap();
    await expect.poll(() => posted).toEqual(['save_camera_arm5_diagnostic']);

    display = guidedDisplay('exposure_locked');
    await expect(diagnostic).toBeHidden();
  });
}

test('shows the 1280x800 exposure search while the radar records the ball', async ({ page }) => {
  const state = {
    epoch_id: 'epoch-parallel',
    phase: 'ball_capturing',
    reason: 'capturing_ball_present',
    evidence: {
      ball_present_capture_id: 'ball_present-000004',
      camera_arm5_capture_setup: { started_during_radar_capture_id: 'ball_present-000004' },
    },
    solution: null,
  };
  const control = await base(page, state);
  let display = guidedDisplay('lighting_required', {
    reason: 'no visible setting passed the ball-pixel gates',
  });
  await page.route('**/api/tester/live', (route) =>
    json(route, {
      running: true,
      arm_id: 'arm5',
      owner: { kind: 'guided_tee_range', tester_id: '20260922-name', epoch_id: 'epoch-parallel', arm_id: 'arm5' },
      stats: { mean: 70, p99: 140, max: 180, clipped_pct: 0 },
      association: { status: 'selected', selected: null, save_eligible: display.save_ready },
      guided_display: display,
    })
  );
  await page.goto('/tester.html');

  const action = page.locator('#tee-range-action');
  const exposure = page.locator('#tee-range-camera-exposure');
  await expect(action).toHaveText('Capturing ball…');
  await expect(action).toBeDisabled();
  await expect(page.locator('#tee-range-camera-preview')).toBeVisible();
  await expect(exposure).toContainText('More light is needed on the ball');
  await expect(exposure).toContainText('Save opens when the radar capture finishes.');
  await expect(page.locator('#tee-range-diagnostic')).toBeHidden();

  display = guidedDisplay('exposure_locked');
  await expect(exposure).toContainText('Exposure locked at 1250 µs × 8.00');
  control.setState({ ...state, phase: 'camera_arm5_capturing', reason: 'camera_arm5_searched_during_radar_capture' });
  await expect(action).toHaveText('Save 1280×800 observation');
  await expect(action).toBeEnabled();
  await expect(exposure).toContainText('Ready to save.');
});

// P8-1 (D14): the placement box became a 2 ft × 2 ft ground patch, drawn in true
// perspective on the live 1280×800 picture. The tester drags it over the hitting
// spot, near or far, sees its distance and side offset while dragging, and confirms
// it; the hardware check, the light and the ball range follow.
const WIDE_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAABAAAAAKCAAAAACY1YFAAAAAD0lEQVR4nGPQQAMMg1UAAE4iGQEMndODAAAAAElFTkSuQmCC',
  'base64'
);
const TESTER = '20260922-name';

type BoxOptions = {
  confirmed?: boolean;
  previewRunning?: boolean;
  showFails?: string;
  prefill?: number[];
};

async function boxStep(page: Page, options: BoxOptions = {}) {
  let range: FlowState | null = null;
  const initial = placementBoxState(TESTER);
  let box: Record<string, unknown> = placementBoxState(
    TESTER,
    options.confirmed ? 'confirmed' : 'needs_confirmation',
    options.prefill
      ? {
          box: {
            ...initial.box,
            patch: { centre_lfu_m: options.prefill, size_m: 0.61 },
            source: 'last_confirmed',
          },
        }
      : {}
  );
  let previewRunning = options.previewRunning ?? false;
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
                  prompt: 'Measure the light again (B): this screen was measured in a box you have since moved.',
                },
              },
            ]
          : [],
      },
    })
  );
  await page.route('**/api/tester/attempts?**', (route) => json(route, { scopes: [] }));
  await page.route('**/api/tester/ladder?**', (route) => json(route, { ladder: null, stopped: true }));
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
    if (route.request().method() === 'GET') return json(route, { state: range, display: rangeDisplay(range) });
    const action = route.request().postDataJSON().action as string;
    rangePosts.push(action);
    // the ball range begins straight at the radar captures, in the confirmed patch
    range = {
      epoch_id: `epoch-${rangePosts.length}`,
      phase: 'needs_empty',
      reason: 'needs_empty',
      evidence: {},
      solution: null,
    };
    return json(route, { state: range, display: rangeDisplay(range) });
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
    const shown = box.box as { patch: { centre_lfu_m: number[] } };
    const before = shown.patch.centre_lfu_m;
    const after = body.centre_m as number[];
    const shift = Math.hypot(after[0] - before[0], after[1] - before[1]);
    const moved = box.state === 'confirmed' && shift > 0.02;
    const same = box.state === 'confirmed' && !moved;
    box = placementBoxState(TESTER, 'confirmed', {
      box: {
        ...(box.box as object),
        patch: { centre_lfu_m: same ? before : after, size_m: 0.61 },
        source: 'tester_dragged',
      },
      change: moved ? 'moved' : same ? 'unchanged' : 'first',
    });
    previewRunning = false;
    if (moved) {
      lightStale = true;
      range = { epoch_id: 'epoch-moved', phase: 'needs_empty', reason: 'needs_empty', evidence: {}, solution: null };
    }
    return json(route, { ...box, previewing: false, change: box.change, range_restarted: moved });
  });
  return {
    posts,
    rangePosts,
    runs,
    setShowFails: (value: string | null) => {
      showFails = value;
    },
    setRange: (value: FlowState) => {
      range = value;
    },
    setBrightness: (value: string) => {
      brightness = value;
    },
  };
}

const centreOf = async (page: Page) =>
  ((await page.locator('#placement-box').getAttribute('data-centre')) ?? '').split(',').map(Number);

// P7-15b: the patch preview sets its own brightness for viewing, and says so meanwhile.
test('the patch preview says it is adjusting its brightness until it settles', async ({ page }) => {
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

test('the patch is step 1: its camera opens by itself and every check waits for it', async ({ page }) => {
  const mocks = await boxStep(page);
  await page.goto('/tester.html');

  await expect(page.getByRole('heading', { level: 2, name: '1. Set up the rig and place the patch' })).toBeVisible();
  // the patch step sits after the setup checklist and before A, the hardware check
  const order = await page.evaluate(() => {
    const at = (id: string) => document.getElementById(id) as HTMLElement;
    const follows = (a: string, b: string) =>
      Boolean(at(a).compareDocumentPosition(at(b)) & Node.DOCUMENT_POSITION_FOLLOWING);
    return [follows('setup-confirm', 'placement'), follows('placement', 'step-check')];
  });
  expect(order).toEqual([true, true]);

  const patch = page.locator('#placement-box');
  await expect(patch).toBeVisible();
  await expect(patch).toHaveAttribute('data-draggable', 'true');
  // drawn in perspective: the near edge (y 494) is wider than the far edge (y 457)
  await expect(patch).toHaveAttribute('data-outline', '338,494 942,494 823,457 457,457');
  await expect(page.locator('#placement-distance')).toHaveText('Patch centre 1.25 m from the radar, straight ahead.');
  expect(mocks.posts.map((item) => item.action)).toEqual(['show']);
  await expect(page.locator('#placement-summary')).toContainText('Drag the yellow patch');
  // A, B and the ball range are locked until the patch is confirmed
  await expect(page.locator('#step-check')).toBeDisabled();
  await expect(page.locator('#step-light')).toBeDisabled();
  await expect(page.locator('#tee-range-action')).toBeDisabled();
  await expect(page.locator('#suite-box-hint')).toBeVisible();
  await expect(page.locator('#automatic-range-summary')).toContainText('Confirm the patch in step 1 first');

  await page.locator('#placement-confirm').click();

  await expect(page.locator('#placement-summary')).toContainText('Patch confirmed');
  await expect(page.locator('#placement-summary')).toContainText('1.25 m from the radar');
  expect(mocks.posts.find((item) => item.action === 'confirm')?.centre_m).toEqual([0, 1.2486]);
  await expect(patch).toBeHidden();
  await expect(page.locator('#placement-move')).toBeVisible();
  await expect(page.locator('#suite-box-hint')).toBeHidden();
  await expect(page.locator('#step-check')).toBeEnabled();
  await expect(page.locator('#step-light')).toBeEnabled();
  const action = page.locator('#tee-range-action');
  await expect(action).toHaveText('Start automatic range');
  await expect(action).toBeEnabled();
  // step 3 begins directly with the camera and radar checks
  await action.click();
  await expect(action).toHaveText('Capture empty hitting area');
  expect(mocks.rangePosts).toEqual(['start']);
  await page.locator('#step-check').click();
  await expect.poll(() => mocks.runs).toEqual(['preflight']);
});

test('the tester drags the patch in perspective, near or far, and confirms it', async ({ page }) => {
  const mocks = await boxStep(page);
  await page.goto('/tester.html');

  const ground = page.locator('#placement-ground');
  const distance = page.locator('#placement-distance');
  await expect(distance).toHaveText('Patch centre 1.25 m from the radar, straight ahead.');

  // a mouse drag right and down: the patch comes nearer and moves right
  await ground.scrollIntoViewIfNeeded();
  const start = await ground.boundingBox();
  if (!start) throw new Error('the patch has no size');
  const x = start.x + start.width / 2;
  const y = start.y + start.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + 40, y + 6, { steps: 5 });
  await page.mouse.up();
  const [lateral, forward] = await centreOf(page);
  expect(lateral).toBeGreaterThan(0.02);
  expect(forward).toBeLessThan(1.2);
  await expect(distance).toContainText('right');

  // dragged up past the horizon, it stops at the farthest supported distance
  const moved = await ground.boundingBox();
  if (!moved) throw new Error('the patch has no size');
  await page.mouse.move(moved.x + moved.width / 2, moved.y + moved.height / 2);
  await page.mouse.down();
  await page.mouse.move(moved.x + moved.width / 2, moved.y - 400, { steps: 4 });
  await page.mouse.up();
  await expect(distance).toContainText('3.00 m from the radar');
  // far away the patch is drawn small: its near edge is much narrower than at 1.25 m
  const outline = ((await page.locator('#placement-box').getAttribute('data-outline')) ?? '')
    .split(' ')
    .map((point) => point.split(',').map(Number));
  expect(outline[1][0] - outline[0][0]).toBeLessThan(260);

  await page.locator('#placement-confirm').click();

  await expect(page.locator('#placement-summary')).toContainText('Patch confirmed');
  const confirm = mocks.posts.find((item) => item.action === 'confirm');
  const sent = confirm?.centre_m as number[];
  expect(Math.hypot(sent[0], sent[1] + 0.0014)).toBeCloseTo(3.0, 2);
  expect(typeof confirm?.request_id).toBe('string');
  await expect(page.locator('#placement-box')).toBeHidden();
});

test('the patch can be dragged with a finger', async ({ page }) => {
  const mocks = await boxStep(page);
  await page.goto('/tester.html');
  const ground = page.locator('#placement-ground');
  await expect(ground).toBeVisible();
  const start = await ground.boundingBox();
  if (!start) throw new Error('the patch has no size');
  const at = (dx: number) => ({
    pointerId: 7,
    pointerType: 'touch',
    isPrimary: true,
    clientX: start.x + start.width / 2 + dx,
    clientY: start.y + start.height / 2,
    bubbles: true,
  });

  await ground.dispatchEvent('pointerdown', at(0));
  await ground.dispatchEvent('pointermove', at(-40));
  await ground.dispatchEvent('pointerup', at(-40));

  const [lateral] = await centreOf(page);
  expect(lateral).toBeLessThan(-0.02);
  await expect(page.locator('#placement-distance')).toContainText('left');
  await page.locator('#placement-confirm').click();
  await expect(page.locator('#placement-summary')).toContainText('Patch confirmed');
  const sent = mocks.posts.find((item) => item.action === 'confirm')?.centre_m as number[];
  expect(sent[0]).toBeCloseTo(lateral, 3);
});

test('the patch starts where it was last confirmed', async ({ page }) => {
  await boxStep(page, { prefill: [0.3, 1.8] });
  await page.goto('/tester.html');

  await expect(page.locator('#placement-box')).toHaveAttribute('data-centre', '0.300,1.800');
  await expect(page.locator('#placement-distance')).toHaveText('Patch centre 1.83 m from the radar, 0.30 m right.');
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
  await expect(summary).toContainText('Drag the yellow patch');
});

test('moving the patch later makes the light stale and starts the ball range over', async ({ page }) => {
  const mocks = await boxStep(page, { confirmed: true });
  mocks.setRange({
    epoch_id: 'epoch-done',
    phase: 'raw_only',
    reason: 'raw_only',
    evidence: {},
    solution: { status: 'unresolved', selected_range_m: null },
  });
  await page.goto('/tester.html');
  await expect(page.locator('#tee-range-action')).toHaveText('Evidence complete — no ball found');
  // a confirmed patch opens no camera by itself
  await expect(page.locator('#placement-box')).toBeHidden();
  expect(mocks.posts).toEqual([]);

  await page.locator('#placement-move').click();
  const ground = page.locator('#placement-ground');
  await expect(page.locator('#placement-box')).toBeVisible();
  const start = await ground.boundingBox();
  if (!start) throw new Error('the patch has no size');
  await page.mouse.move(start.x + start.width / 2, start.y + start.height / 2);
  await page.mouse.down();
  await page.mouse.move(start.x + start.width / 2 + 60, start.y + start.height / 2, { steps: 5 });
  await page.mouse.up();
  await page.locator('#placement-confirm').click();

  await expect(page.locator('#placement-summary')).toContainText('The patch moved');
  await expect(page.locator('#placement-summary')).toContainText('the ball range has started over');
  await expect(page.locator('#tee-range-action')).toHaveText('Capture empty hitting area');
  await expect(page.locator('#light-age')).toContainText('Measure the light again (B)');
  expect(mocks.posts.map((item) => item.action)).toEqual(['show', 'confirm']);
});

test('the camera step draws the patch it searches', async ({ page }) => {
  await base(page, {
    epoch_id: 'epoch-boxed',
    phase: 'camera_arm5_capturing',
    reason: 'camera_arm5_warming',
    evidence: {},
    solution: null,
  });
  await page.route('**/api/tester/live', (route) =>
    json(route, {
      running: true,
      arm_id: 'arm5',
      owner: { kind: 'guided_tee_range', tester_id: '20260922-name', epoch_id: 'epoch-boxed', arm_id: 'arm5' },
      stats: { mean: 71, p99: 139, max: 178, clipped_pct: 0 },
      association: {
        status: 'not_found',
        selected: null,
        candidates: [],
        stable_count: 0,
        stable_span_s: 0,
        save_eligible: false,
        placement_box_px: [338, 372, 943, 550],
        readiness_reason: 'No ball found in the patch. The ball may be outside it: move the ball or the patch.',
      },
      guided_display: guidedDisplay('exposure_searching'),
      placement_box: {
        box_px: [338, 372, 943, 550],
        frame_size_px: [1280, 800],
        arm_id: 'arm5',
        outline_px: [
          [338.3, 494.0],
          [941.7, 494.0],
          [823.2, 457.1],
          [456.8, 457.1],
        ],
        search_outline_px: [
          [338.3, 548.6],
          [941.7, 548.6],
          [823.2, 372.6],
          [456.8, 372.6],
        ],
      },
    })
  );
  await page.route('**/api/tester/live.png?**', (route) =>
    route.fulfill({ status: 200, contentType: 'image/png', body: WIDE_PNG })
  );
  await page.goto('/tester.html');

  const patch = page.locator('#tee-range-placement-box');
  await expect(patch).toBeVisible();
  await expect(patch).toHaveAttribute('data-outline', '338,494 942,494 823,457 457,457');
  await expect(patch).toHaveAttribute('data-draggable', 'false');
  await expect(page.locator('#tee-range-patch-search')).toHaveAttribute(
    'points',
    '338.3,548.6 941.7,548.6 823.2,372.6 456.8,372.6'
  );
  await expect(page.locator('#tee-range-camera-detector')).toContainText('No ball found in the patch');
});

// P8-4: the camera's ball and the radar's candidates validate each other; the summary
// shows both distances and what they agreed on, with a warning when they did not.
const PAIR_STATE = {
  epoch_id: 'epoch-pair',
  phase: 'experimental',
  reason: 'patch_ball_saved',
  evidence: {},
  solution: { status: 'unresolved', selected_range_m: null },
};

async function pairStep(page: Page, patchBall: Record<string, unknown>) {
  const mocks = await base(page, PAIR_STATE);
  await page.route('**/api/tester/tee-range**', (route) =>
    json(route, {
      state: mocks.state(),
      display: {
        ...rangeDisplay(mocks.state()),
        canonical: { state: 'experimental', range_m: patchBall.range_m, reason: null },
        swings: {
          state: 'experimental',
          range_m: patchBall.range_m,
          message: 'EXPERIMENTAL: swings use the setup’s distance.',
        },
        patch_ball: patchBall,
      },
    })
  );
  await page.goto('/tester.html');
}

test('an agreeing pair shows both distances and where the ball sits aside', async ({ page }) => {
  await pairStep(page, {
    status: 'validated',
    warning: null,
    range_m: 1.176,
    side_offset_m: 0.1,
    source: 'static_iwr_magnitude',
    camera_range_m: 1.25,
    camera_uncertainty_m: 0.27,
    radar_range_m: 1.176,
    radar_method: 'magnitude',
    radar_candidates: [
      { method: 'magnitude', range_m: 1.176, score: 0.8, elevation_deg: -16 },
      { method: 'coherent', range_m: 1.575, score: 49, elevation_deg: -9.5 },
    ],
    radar_warnings: [],
  });

  const ball = page.locator('[data-state="ball-validated"]');
  await expect(ball).toContainText('ball 1.176 m — camera and radar agree');
  await expect(ball).toContainText('camera 1.250 m');
  await expect(ball).toContainText('radar 1.176 m (magnitude)');
  await expect(ball).toContainText('0.10 m right');
  await expect(ball).not.toHaveClass('problem');
  await expect(page.locator('[data-state="radar-candidates"]')).toContainText(
    '1.176 m magnitude, 1.575 m coherent'
  );
  await expect(page.getByRole('button', { name: 'C. Start the exposure ladder' })).toBeEnabled();
});

test('disagreeing sensors are saved with a warning that names both distances', async ({ page }) => {
  const warning =
    'The camera puts the ball at 1.50 m and the radar at 1.20 m: they disagree. Check for other objects in the patch. The camera’s distance is used.';
  await pairStep(page, {
    status: 'disagree',
    warning,
    range_m: 1.5,
    side_offset_m: 0,
    source: 'camera_size_range',
    camera_range_m: 1.5,
    camera_uncertainty_m: 0.02,
    radar_range_m: 1.2,
    radar_method: 'coherent',
    radar_candidates: [{ method: 'coherent', range_m: 1.2, score: 30, elevation_deg: -11 }],
    radar_warnings: [],
  });

  const ball = page.locator('[data-state="ball-disagree"]');
  await expect(ball).toHaveClass('problem');
  await expect(ball).toContainText('camera and radar disagree');
  await expect(ball).toContainText(warning);
  // measure, label, don't block (D15): the ladder can still start
  await expect(page.getByRole('button', { name: 'C. Start the exposure ladder' })).toBeEnabled();
});

// P7-7 (D11): without a qualified range the setup saves as experimental.
test('a setup saved as experimental says so and lets the ladder start', async ({ page }) => {
  const mocks = await base(page, {
    epoch_id: 'epoch-experimental',
    phase: 'experimental',
    reason: 'experimental_range_saved',
    evidence: {
      iwr_candidate: { radar_slant_range_m: 1.581 },
      camera_arm5_candidate: { radar_slant_range_m: 1.34 },
    },
    solution: { status: 'unresolved', selected_range_m: null },
  });
  await page.route('**/api/tester/tee-range**', (route) =>
    json(route, {
      state: mocks.state(),
      display: {
        ...rangeDisplay(mocks.state()),
        canonical: { state: 'experimental', range_m: 1.581, reason: null },
        swings: {
          state: 'experimental',
          range_m: 1.581,
          message: 'EXPERIMENTAL: swings use the radar’s range, which agrees with the camera’s.',
        },
      },
    })
  );
  await page.goto('/tester.html');

  await expect(page.locator('#tee-range-action')).toHaveText('Setup saved — experimental range');
  await expect(page.locator('#automatic-range-summary')).toContainText('Saved as EXPERIMENTAL');
  await expect(page.locator('#automatic-range')).toContainText('experimental range 1.581 m — not qualified');
  await expect(page.locator('#automatic-range')).toContainText('swings get 1.581 m — EXPERIMENTAL');
  await expect(page.getByRole('button', { name: 'C. Start the exposure ladder' })).toBeEnabled();
});

test('the advisory 640×400 check can be skipped', async ({ page }) => {
  const posts: string[] = [];
  await base(page, {
    epoch_id: 'epoch-skip',
    phase: 'needs_camera_arm6',
    reason: 'validate_shared_range_in_camera_arm6',
    evidence: {},
    solution: null,
  });
  await page.route('**/api/tester/tee-range**', (route) => {
    if (route.request().method() === 'GET') {
      const state = {
        epoch_id: 'epoch-skip',
        phase: posts.includes('skip_camera_arm6') ? 'experimental' : 'needs_camera_arm6',
        reason: 'x',
        evidence: {},
        solution: null,
      };
      return json(route, { state, display: rangeDisplay(state) });
    }
    const action = route.request().postDataJSON().action as string;
    posts.push(action);
    const state = { epoch_id: 'epoch-skip', phase: 'experimental', reason: 'x', evidence: {}, solution: null };
    return json(route, { state, display: rangeDisplay(state) });
  });
  await page.goto('/tester.html');

  const skip = page.locator('#tee-range-skip-arm6');
  await expect(page.locator('#automatic-range-summary')).toContainText('The check is advisory');
  await expect(skip).toBeVisible();
  await skip.click();

  await expect(page.locator('#tee-range-action')).toHaveText('Setup saved — experimental range');
  expect(posts).toEqual(['skip_camera_arm6']);
  await expect(skip).toBeHidden();
});
