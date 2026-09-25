import { expect, test, type Page, type Route } from '@playwright/test';
import { KIOSK_VIEWPORTS } from './helpers';

test.use({ hasTouch: true });

const defaultTester = '20260922-name';

function eligibility(testerId = defaultTester, eligible = false) {
  const checks = [
    {
      id: 'rig_config',
      label: 'Approved measured v3 rig config',
      status: 'pass',
      reason: null,
      remedy: null,
    },
    {
      id: 'lis3dh',
      label: 'LIS3DH identity and stable tilt',
      status: 'pass',
      reason: 'LIS3DH identity is current and tilt is stable.',
      remedy: 'Reconnect and level the LIS3DH.',
    },
    {
      id: 'operator_physical_setup',
      label: 'Operator physical rig confirmation',
      status: eligible ? 'pass' : 'block',
      reason: eligible ? 'Operator confirmation is bound to this config.' : 'Physical build is not confirmed.',
      remedy: 'Measure the feet, lens reference, radar mounts and LIS3DH orientation, then confirm.',
    },
  ];
  return {
    schema_version: 1,
    tester_id: testerId,
    config_hash: 'measured-v3-hash',
    eligible,
    stage: 'tester_admission',
    checks,
    blockers: checks
      .filter((check) => check.status === 'block')
      .map((check) => ({ id: check.id, reason: check.reason, remedy: check.remedy })),
    operator_confirmation: {
      confirmed: eligible,
      confirmed_at: eligible ? '2026-09-24T01:00:00Z' : null,
      config_hash: eligible ? 'measured-v3-hash' : null,
      authority: 'operator_physical_setup',
    },
    runtime_requirements: ['ops', 'camera', 'iwr6843', 'lis3dh'],
  };
}

function warnedEligibility() {
  const result = eligibility(defaultTester, true);
  const warning =
    'placement exceeds the 2.0 degree flag threshold: pitch +3.25 degrees (delta +3.25), roll -2.50 degrees (delta -2.50); correction accuracy is not qualified';
  result.checks[1] = {
    ...result.checks[1],
    status: 'warn',
    reason: warning,
    remedy: null,
    placement_guard: {
      ready: true,
      reason: null,
      pitch_deg: 3.25,
      roll_deg: -2.5,
      z_g: 0.99,
      upright: true,
      expected_pitch_deg: 0,
      expected_roll_deg: 0,
      tolerance_deg: 2,
      pitch_error_deg: 3.25,
      roll_error_deg: -2.5,
      within_threshold: false,
      warned: true,
      warning,
      accuracy_qualified: false,
    },
  } as (typeof result.checks)[number];
  return { ...result, warnings: [{ id: 'lis3dh', reason: warning }] };
}

async function json(route: Route, payload: object, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(payload) });
}

async function mockBase(page: Page) {
  await page.route('**/api/tester/status**', (route) => json(route, {}));
  await page.route('**/api/tester/attempts?**', (route) => json(route, { schema_version: 1, scopes: [] }));
  await page.route('**/api/tester/ladder?**', (route) =>
    json(route, { ladder: null, stopped: true, photo_target: null })
  );
  await page.route('**/api/camera/preview.jpg**', (route) => route.fulfill({ status: 204 }));
}

test('fails closed with concrete physical and sensor remedies while recovery actions remain available', async ({
  page,
}) => {
  await mockBase(page);
  await page.route('**/api/tester/setup-eligibility?**', (route) => json(route, eligibility()));
  let acquisitionRequests = 0;
  await page.route('**/api/tester/run', (route) => {
    acquisitionRequests += 1;
    return json(route, {});
  });
  await page.goto('/tester.html');

  await expect(page.locator('#setup-summary')).toContainText('1 setup blocker');
  await expect(page.locator('#setup-checks')).toContainText('LIS3DH identity and stable tilt: pass');
  await expect(page.locator('#setup-checks')).not.toContainText('null');
  await expect(page.locator('main')).toContainText('physically verify and confirm the measured v3 rig setup');
  await expect(page.locator('#setup-checks')).toContainText('Remedy: Measure the feet');
  await expect(page.locator('#setup-gate')).toContainText('lens centre 95 mm');
  await expect(page.locator('#setup-gate')).toContainText('LIS3DH lies flat with +Y toward the back');
  await expect(page.locator('#setup-gate')).toContainText('cannot inspect the enclosure build');
  await expect(page.locator('#setup-gate')).toContainText('does not count as accepted data');
  await expect(page.locator('#setup-gate')).toContainText('record it in the independent swing tally');
  await expect(page.getByRole('button', { name: 'C. Start the exposure ladder' })).toBeDisabled();
  await expect(page.locator('#btn-gain')).toBeDisabled();
  await expect(page.locator('#btn-swings')).toBeDisabled();
  await expect(page.getByRole('button', { name: 'B. Measure the light (both modes)' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'A. Check the hardware' })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Stop all tester activity' })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'D. Analyse, review & package' })).toBeEnabled();
  await page.locator('#btn-swings').evaluate((button: HTMLButtonElement) => button.click());
  expect(acquisitionRequests).toBe(0);
});

test('posts exact config-bound attestation and a server restart revokes eligibility', async ({ page }) => {
  await mockBase(page);
  let confirmed = false;
  const confirmations: Record<string, unknown>[] = [];
  await page.route('**/api/tester/setup-eligibility?**', (route) => json(route, eligibility(defaultTester, confirmed)));
  await page.route('**/api/tester/setup-eligibility', async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    confirmations.push(body);
    confirmed = true;
    await json(route, eligibility(defaultTester, true));
  });
  await page.goto('/tester.html');
  await page.locator('#setup-physical-confirm').check();
  await page.getByRole('button', { name: 'Confirm physical setup' }).tap();

  expect(confirmations).toEqual([
    {
      tester_id: defaultTester,
      action: 'confirm',
      config_hash: 'measured-v3-hash',
      physical_rig_confirmed: true,
    },
  ]);
  await expect(page.locator('#setup-gate')).toHaveAttribute('data-ready', 'true');
  await expect(page.getByRole('button', { name: 'C. Start the exposure ladder' })).toBeEnabled();
  await expect(page.locator('#setup-authority')).toContainText('authority operator_physical_setup');
  await expect(page.locator('#setup-authority')).toContainText('runtime sensors: ops, camera, iwr6843, lis3dh');

  confirmed = false;
  await expect(page.locator('#setup-gate')).toHaveAttribute('data-ready', 'false', { timeout: 3000 });
  await expect(page.getByRole('button', { name: 'C. Start the exposure ladder' })).toBeDisabled();
});

test('request timeout remains active while the response body stalls', async ({ page }) => {
  await mockBase(page);
  await page.addInitScript(() => {
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (input, init) => {
      if (String(input).includes('/api/tester/setup-eligibility?')) {
        return Promise.resolve(new Response(new ReadableStream({ start() {} }), { status: 200 }));
      }
      return nativeFetch(input, init);
    };
  });
  await page.goto('/tester.html');

  await expect(page.locator('#setup-summary')).toContainText('timed out', { timeout: 6500 });
});

test('an old confirmation cannot clear the pending state of a newer confirmation', async ({ page }) => {
  await mockBase(page);
  await page.addInitScript(() => {
    const nativeFetch = window.fetch.bind(window);
    let releaseOld!: () => void;
    const oldGate = new Promise<void>((resolve) => {
      releaseOld = resolve;
    });
    (window as Window & { releaseOldSetup?: () => void }).releaseOldSetup = releaseOld;
    window.fetch = async (input, init) => {
      if (
        String(input).endsWith('/api/tester/setup-eligibility') &&
        init?.method === 'POST' &&
        String(init.body).includes('20260922-name')
      ) {
        await oldGate;
        return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return nativeFetch(input, init);
    };
  });
  let releaseNew!: () => void;
  const newGate = new Promise<void>((resolve) => {
    releaseNew = resolve;
  });
  let eligibilityReads = 0;
  await page.route('**/api/tester/setup-eligibility?**', (route) => {
    eligibilityReads += 1;
    const id = new URL(route.request().url()).searchParams.get('tester_id') || '';
    return json(route, eligibility(id, false));
  });
  await page.route('**/api/tester/setup-eligibility', async (route) => {
    await newGate;
    const id = String(route.request().postDataJSON().tester_id);
    await json(route, eligibility(id, true));
  });
  await page.goto('/tester.html');
  await page.locator('#setup-physical-confirm').check();
  await page.getByRole('button', { name: 'Confirm physical setup' }).tap();
  await page.locator('#tester-id').fill('new-tester');
  await expect(page.locator('#setup-summary')).toContainText('setup blocker');
  await page.locator('#setup-physical-confirm').check();
  await page.getByRole('button', { name: 'Confirm physical setup' }).tap();

  await page.evaluate(() => (window as Window & { releaseOldSetup?: () => void }).releaseOldSetup?.());
  await page.waitForTimeout(50);
  const readsBeforeManualRefresh = eligibilityReads;
  await page.evaluate(() => (window as Window & { refreshSetupEligibility?: () => Promise<boolean> }).refreshSetupEligibility?.());
  expect(eligibilityReads).toBe(readsBeforeManualRefresh);
  releaseNew();
  await expect(page.locator('#setup-gate')).toHaveAttribute('data-ready', 'true');
});

test('late eligibility for the prior tester cannot authorize the new tester', async ({ page }) => {
  await mockBase(page);
  let releaseOld!: () => void;
  const oldGate = new Promise<void>((resolve) => {
    releaseOld = resolve;
  });
  await page.route('**/api/tester/setup-eligibility?**', async (route) => {
    const testerId = new URL(route.request().url()).searchParams.get('tester_id') || '';
    if (testerId === defaultTester) {
      await oldGate;
      return json(route, eligibility(defaultTester, true));
    }
    return json(route, eligibility(testerId, false));
  });
  await page.goto('/tester.html');
  await page.locator('#tester-id').fill('different-tester');
  await expect(page.locator('#setup-authority')).toContainText('not confirmed');
  releaseOld();
  await page.waitForTimeout(50);
  await expect(page.locator('#setup-gate')).toHaveAttribute('data-ready', 'false');
  await expect(page.locator('#setup-authority')).toContainText('not confirmed');
});

test('a 409 action response immediately replaces stale local readiness with server blockers', async ({ page }) => {
  await mockBase(page);
  await page.route('**/api/tester/setup-eligibility?**', (route) => json(route, eligibility(defaultTester, true)));
  await page.route('**/api/tester/ladder/start', (route) =>
    json(route, { error: 'setup eligibility was lost', setup_eligibility: eligibility(defaultTester, false) }, 409)
  );
  await page.goto('/tester.html');
  await expect(page.getByRole('button', { name: 'C. Start the exposure ladder' })).toBeEnabled();
  await page.getByRole('button', { name: 'C. Start the exposure ladder' }).tap();
  await expect(page.locator('#setup-gate')).toHaveAttribute('data-ready', 'false');
  await expect(page.locator('#ladder-panel')).toContainText('setup eligibility was lost');
});

test('placement warning stays visible after reload without blocking acquisition', async ({ page }) => {
  await mockBase(page);
  await page.route('**/api/tester/setup-eligibility?**', (route) => json(route, warnedEligibility()));
  await page.goto('/tester.html');

  for (let load = 0; load < 2; load += 1) {
    await expect(page.locator('#setup-gate')).toHaveAttribute('data-ready', 'true');
    await expect(page.locator('#setup-gate')).toHaveAttribute('data-warning', 'true');
    await expect(page.locator('#setup-summary')).toContainText('Eligible with 1 retained placement warning');
    await expect(page.locator('#setup-summary')).toContainText('Correction accuracy is not validated');
    await expect(page.locator('#setup-checks')).toContainText('LIS3DH identity and stable tilt: warn');
    await expect(page.locator('#setup-checks')).toContainText('Measured pitch 3.25 degrees, roll -2.50 degrees');
    await expect(page.locator('#setup-checks')).toContainText(
      'signed deviations 3.25 degrees pitch and -2.50 degrees roll'
    );
    await expect(page.locator('#setup-checks')).toContainText('flag threshold +/-2.0 degrees');
    await expect(page.getByRole('button', { name: 'C. Start the exposure ladder' })).toBeEnabled();
    await expect(page.locator('#btn-gain')).toBeEnabled();
    await expect(page.locator('#btn-swings')).toBeEnabled();
    if (load === 0) await page.reload();
  }
});

for (const viewport of KIOSK_VIEWPORTS) {
  test(`setup gate remains touchable without horizontal clipping at ${viewport.width}x${viewport.height}`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    await mockBase(page);
    await page.route('**/api/tester/setup-eligibility?**', (route) => json(route, eligibility()));
    await page.route('**/api/tester/setup-eligibility', (route) => json(route, eligibility(defaultTester, true)));
    await page.goto('/tester.html');
    const gate = page.locator('#setup-gate');
    await gate.scrollIntoViewIfNeeded();
    await page.locator('#setup-physical-confirm').tap();
    await page.getByRole('button', { name: 'Confirm physical setup' }).tap();
    await expect(page.getByRole('button', { name: 'Stop all tester activity' })).toBeVisible();
    await page.getByRole('button', { name: 'Stop all tester activity' }).scrollIntoViewIfNeeded();
    const stopBox = await page.getByRole('button', { name: 'Stop all tester activity' }).boundingBox();
    expect(stopBox).not.toBeNull();
    expect(stopBox!.width).toBeGreaterThanOrEqual(44);
    expect(stopBox!.height).toBeGreaterThanOrEqual(44);
    await expect(page.getByText('Advanced: manual single-arm tools')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Manual single-arm controls' })).toBeHidden();
    const clipped = await gate.evaluate((node) => {
      const rect = node.getBoundingClientRect();
      return rect.left < 0 || rect.right > window.innerWidth;
    });
    expect(clipped).toBe(false);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}
