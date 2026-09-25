import { expect, test, type Page, type Route } from '@playwright/test';
import { KIOSK_VIEWPORTS } from './helpers';

test.use({ hasTouch: true });

const testerId = 'diagnostic-reviewer';
const runOne = { tester_id: testerId, arm_id: 'arm5', run: 'run-01', run_dir: '/runs/arm5/run-01' };
const runTwo = { tester_id: testerId, arm_id: 'arm6', run: 'run-02', run_dir: '/runs/arm6/run-02' };

async function json(route: Route, body: object, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
}

function metric(
  key: string,
  label: string,
  value: number | null,
  unit: string,
  source = 'existing_live_pipeline',
  status = value === null ? 'withheld' : 'available'
) {
  return {
    key,
    label,
    value,
    unit,
    source,
    status,
    reason: value === null ? 'source did not produce a value' : null,
    validation: key.startsWith('camera_') ? 'estimated' : 'unvalidated',
    definition: 'Recorded diagnostic value at the existing pipeline output.',
  };
}

function shot(
  revision: number,
  outcome: 'processing' | 'complete' | 'partial' | 'rejected',
  overrides: Record<string, unknown> = {}
) {
  return {
    schema_version: 1,
    session_uuid: 'session-a',
    shot_number: 1,
    revision,
    phase: outcome === 'processing' ? 'pending' : 'terminal',
    outcome,
    reason: outcome === 'processing' || outcome === 'complete' ? undefined : 'camera result timed out',
    recorded_at: `2026-09-24T00:00:0${revision}Z`,
    accuracy_qualified: false,
    metrics: [
      metric('ball_speed_mph', 'Ball speed', 0, 'mph', 'OPS243 radial speed'),
      metric('club_speed_mph', 'Club speed', null, 'mph'),
      metric('launch_vertical_deg', 'Vertical launch', 12.5, 'deg'),
      metric('launch_horizontal_deg', 'Horizontal launch', -1.25, 'deg'),
      metric('club_path_deg', 'Club path', 2.5, 'deg', 'camera/IWR estimate'),
      metric('attack_angle_deg', 'Attack angle', -3, 'deg', 'camera/IWR estimate'),
    ],
    comparisons: [
      {
        key: 'camera_iwr_horizontal_delta_deg',
        label: 'Camera / IWR horizontal delta',
        value: 0,
        unit: 'deg',
        status: 'available',
        reason: null,
        shared_inputs: ['camera_horizontal_deg', 'iwr_horizontal_deg'],
      },
    ],
    ...overrides,
  };
}

function response(shots: ReturnType<typeof shot>[], options: Record<string, unknown> = {}) {
  return {
    schema_version: 1,
    scope: { tester_id: testerId, arm_id: runOne.arm_id, run: runOne.run },
    status: 'available',
    reasons: [],
    read_complete: true,
    retention: { max_shots_per_session: 100, truncated: false },
    sessions: [
      {
        session_uuid: 'session-a',
        session_file: 'session_a.jsonl',
        ended: false,
        diagnostics_available: true,
        reasons: [],
        shots,
      },
    ],
    ...options,
  };
}

async function mockRuns(page: Page) {
  await page.addInitScript(({ key, value }) => localStorage.setItem(key, value), {
    key: 'openflight-tester-id',
    value: testerId,
  });
  await page.route('**/api/tester/attempts?**', (route) =>
    json(route, { schema_version: 1, scopes: [runOne, runTwo] })
  );
}

async function openRun(page: Page, label = 'arm5 · run-01') {
  await page.goto('/fusion-diagnostics.html');
  await page.locator('#run').selectOption({ label });
}

test('polls pending to terminal and renders zero separately from unavailable with provenance', async ({ page }) => {
  await mockRuns(page);
  let requests = 0;
  await page.route('**/api/tester/diagnostics?**', (route) => {
    requests += 1;
    return json(route, response([requests === 1 ? shot(1, 'processing') : shot(2, 'complete')]));
  });
  await openRun(page);
  await expect(page.locator('#snapshot')).toContainText('processing');
  await expect(page.locator('#snapshot')).toContainText('0 mph');
  await expect(page.locator('#snapshot')).toContainText('Club speed');
  await expect(page.locator('#snapshot')).toContainText('Unavailable');
  await expect(page.locator('#snapshot')).toContainText('OPS243 radial speed · available · unvalidated');
  await expect(page.locator('#snapshot')).toContainText('Disagreement, not reference accuracy');
  await expect(page.locator('#snapshot')).toContainText('shared inputs: camera_horizontal_deg, iwr_horizontal_deg');
  await expect(page.locator('#shot-list')).toContainText('revision 2', { timeout: 2500 });
  await expect(page.locator('#snapshot')).toContainText('processing finished');
  await expect(page.locator('#shot-list')).toContainText('revision 2 · processing finished');
});

test('ignores an older revision and keeps same shot numbers separate across sessions', async ({ page }) => {
  await mockRuns(page);
  let requests = 0;
  const sessionBShot = shot(1, 'partial', { session_uuid: 'session-b', reason: 'camera result timed out' });
  await page.route('**/api/tester/diagnostics?**', (route) => {
    requests += 1;
    const body = response([shot(requests === 1 ? 2 : 1, requests === 1 ? 'complete' : 'processing')]);
    if (requests === 1) {
      body.sessions.push({
        session_uuid: 'session-b',
        session_file: 'session_b.jsonl',
        ended: true,
        diagnostics_available: true,
        reasons: [],
        shots: [sessionBShot],
      });
    }
    return json(route, body);
  });
  await openRun(page);
  await expect(page.locator('#shot-list')).toContainText('session-a · shot 1 · revision 2');
  await expect(page.locator('#shot-list')).toContainText('session-b · shot 1 · revision 1');
  await page.getByRole('button', { name: /session-b · shot 1/ }).tap();
  await expect(page.locator('#snapshot')).toContainText('camera result timed out');
  await expect.poll(() => requests, { timeout: 2500 }).toBeGreaterThan(1);
  await expect(page.locator('#shot-list')).toContainText('revision 2');
  await expect(page.locator('#shot-list')).not.toContainText('session-a · shot 1 · revision 1');
  await expect(page.locator('#shot-list')).not.toContainText('session-b');
});

test('a late response from another run cannot populate the selected scope', async ({ page }) => {
  await mockRuns(page);
  let releaseOld!: () => void;
  const oldGate = new Promise<void>((resolve) => {
    releaseOld = resolve;
  });
  await page.route('**/api/tester/diagnostics?**', async (route) => {
    const runDir = new URL(route.request().url()).searchParams.get('run_dir');
    if (runDir === runOne.run_dir) {
      await oldGate;
      return json(route, response([shot(2, 'complete', { reason: 'old run result' })]));
    }
    return json(
      route,
      response([shot(1, 'rejected', { session_uuid: 'session-new', reason: 'new run rejection' })], {
        scope: { tester_id: testerId, arm_id: runTwo.arm_id, run: runTwo.run },
      })
    );
  });
  await openRun(page);
  await page.locator('#run').selectOption({ label: 'arm6 · run-02' });
  await expect(page.locator('#snapshot')).toContainText('new run rejection');
  releaseOld();
  await page.waitForTimeout(50);
  await expect(page.locator('#snapshot')).toContainText('new run rejection');
  await expect(page.locator('#snapshot')).not.toContainText('old run result');
});

test('hidden block suppresses all metric and discrepancy values through polling and reload until reveal', async ({
  page,
}) => {
  await mockRuns(page);
  let requests = 0;
  await page.route('**/api/tester/diagnostics?**', (route) => {
    requests += 1;
    return json(route, response([shot(requests === 1 ? 1 : 2, requests === 1 ? 'processing' : 'complete')]));
  });
  await page.goto('/fusion-diagnostics.html');
  await page.getByRole('button', { name: 'Start hidden-metric block' }).tap();
  await page.locator('#run').selectOption({ label: 'arm5 · run-01' });
  await expect(page.locator('#snapshot')).toContainText('processing');
  await expect(page.locator('#snapshot')).toContainText('Metrics and discrepancies are hidden');
  await expect(page.locator('#snapshot')).not.toContainText('0 mph');
  await expect(page.locator('#snapshot')).not.toContainText('Camera / IWR horizontal delta');
  await expect(page.locator('#shot-list')).toContainText('revision 2', { timeout: 2500 });
  await expect(page.locator('#snapshot')).not.toContainText('12.5');

  await page.reload();
  await expect(page.locator('#block-status')).toContainText('active');
  await page.locator('#run').selectOption({ label: 'arm5 · run-01' });
  await expect(page.locator('#snapshot')).toContainText('Metrics and discrepancies are hidden');
  await page.getByRole('button', { name: 'End block' }).tap();
  await expect(page.locator('#snapshot')).not.toContainText('Ball speed');
  await page.getByRole('button', { name: 'Reveal metrics' }).tap();
  await expect(page.locator('#snapshot')).toContainText('Ball speed');
  await expect(page.locator('#snapshot')).toContainText('0 mph');
});

test('reader errors clear stale values and polling retries without overlapping requests', async ({ page }) => {
  await mockRuns(page);
  let calls = 0;
  let active = 0;
  let maximumActive = 0;
  await page.route('**/api/tester/diagnostics?**', async (route) => {
    calls += 1;
    active += 1;
    maximumActive = Math.max(maximumActive, active);
    await new Promise((resolve) => setTimeout(resolve, 30));
    active -= 1;
    if (calls === 2) return json(route, { error: 'diagnostic file temporarily unreadable' }, 503);
    return json(route, response([shot(calls, calls > 2 ? 'complete' : 'processing')]));
  });
  await openRun(page);
  await expect(page.locator('#snapshot')).toContainText('0 mph');
  await expect(page.locator('#snapshot')).toContainText('No current diagnostic values', { timeout: 2500 });
  await expect(page.locator('#snapshot')).toContainText('processing finished', { timeout: 2500 });
  expect(maximumActive).toBe(1);
});

test('a stalled diagnostic read times out and polling can retry', async ({ page }) => {
  await mockRuns(page);
  await page.addInitScript(() => {
    const originalFetch = window.fetch.bind(window);
    let diagnosticReads = 0;
    window.fetch = async (input, init) => {
      if (!String(input).includes('/api/tester/diagnostics?')) return originalFetch(input, init);
      diagnosticReads += 1;
      if (diagnosticReads > 1) return originalFetch(input, init);
      let streamController!: ReadableStreamDefaultController<Uint8Array>;
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          streamController = controller;
          controller.enqueue(new TextEncoder().encode('{'));
        },
      });
      init?.signal?.addEventListener('abort', () =>
        streamController.error(new DOMException('The operation was aborted.', 'AbortError'))
      );
      return new Response(stream, { status: 200, headers: { 'Content-Type': 'application/json' } });
    };
  });
  await page.route('**/api/tester/diagnostics?**', (route) => json(route, response([shot(2, 'complete')])));
  await openRun(page);
  await expect(page.locator('#diagnostic-error')).toContainText('timed out', { timeout: 6500 });
  await expect(page.locator('#snapshot')).toContainText('No current diagnostic values');
  await expect(page.locator('#snapshot')).toContainText('processing finished', { timeout: 2500 });
  await expect(page.locator('#diagnostic-error')).toBeEmpty();
});

test('corrupt block storage fails closed and a failed save does not start a block', async ({ page }) => {
  await mockRuns(page);
  await page.addInitScript(() => localStorage.setItem('openflight-fusion-hidden-block-v1', '{'));
  await page.route('**/api/tester/diagnostics?**', (route) => json(route, response([shot(1, 'complete')])));
  await page.goto('/fusion-diagnostics.html');
  await expect(page.locator('#block-status')).toContainText('ended');
  await expect(page.locator('#block-error')).toContainText('Metrics remain hidden');
  await page.locator('#run').selectOption({ label: 'arm5 · run-01' });
  await expect(page.locator('#snapshot')).toContainText('Metrics and discrepancies are hidden');
  await expect(page.locator('#block-error')).toContainText('Metrics remain hidden');
  await page.getByRole('button', { name: 'Reveal metrics' }).tap();
  await page.locator('#run').selectOption('');
  await page.evaluate(() => {
    Storage.prototype.setItem = () => {
      throw new DOMException('quota exhausted', 'QuotaExceededError');
    };
  });
  await page.getByRole('button', { name: 'Start hidden-metric block' }).tap();
  await expect(page.locator('#block-error')).toContainText('Hidden block was not started');
  await expect(page.locator('#block-status')).toContainText('Metrics are visible');
});

test('legacy, ended-pending, and partial reader context remain explicit', async ({ page }) => {
  await mockRuns(page);
  const body = response([shot(1, 'processing')], {
    status: 'partial',
    reasons: ['last JSONL line was incomplete'],
    read_complete: false,
  });
  body.sessions[0].ended = true;
  body.sessions.push({
    session_uuid: null,
    session_file: 'legacy.jsonl',
    ended: true,
    diagnostics_available: false,
    reasons: ['legacy session has no diagnostic snapshots'],
    shots: [],
  });
  await page.route('**/api/tester/diagnostics?**', (route) => json(route, body));
  await openRun(page);
  await expect(page.locator('#reader-status')).toContainText('partial · partial read · last JSONL line was incomplete');
  await expect(page.locator('#shot-list')).toContainText('Session ended while a diagnostic remained pending');
  await expect(page.locator('#shot-list')).toContainText('diagnostics unavailable');
  await expect(page.locator('#snapshot')).toContainText('Processing has not reached a terminal result');
});

for (const viewport of KIOSK_VIEWPORTS) {
  test(`shot list supports drag without selection and tap at ${viewport.width}x${viewport.height}`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    await mockRuns(page);
    const shots = Array.from({ length: 14 }, (_, index) =>
      shot(1, index === 13 ? 'complete' : 'partial', {
        shot_number: index + 1,
        reason: `reason ${index + 1}`,
      })
    );
    await page.route('**/api/tester/diagnostics?**', (route) => json(route, response(shots)));
    await openRun(page);
    await expect(page.locator('#snapshot')).toContainText('reason 14');
    const list = page.locator('#shot-list');
    const sizes = await list.evaluate((node) => ({ client: node.clientHeight, scroll: node.scrollHeight }));
    expect(sizes.scroll).toBeGreaterThan(sizes.client);
    const box = await list.boundingBox();
    if (!box) throw new Error('shot list has no box');
    await page.mouse.move(box.x + box.width / 2, box.y + box.height - 20);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2, box.y + 20, { steps: 5 });
    await page.mouse.up();
    await expect(page.locator('#snapshot')).toContainText('reason 14');
    await page.getByRole('button', { name: /shot 1 · revision/ }).tap();
    await expect(page.locator('#snapshot')).toContainText('reason 1');
    const overflow = await page
      .locator('main')
      .evaluate((main) => main.getBoundingClientRect().right - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
  });
}
