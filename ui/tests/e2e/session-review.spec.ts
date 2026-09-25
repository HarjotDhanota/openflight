import { expect, test, type Page, type Route } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { KIOSK_VIEWPORTS } from './helpers';

const REPO = fileURLToPath(new URL('../../..', import.meta.url));
const testerId = 'pilot-1';

function pgm(width: number, height: number) {
  const header = Buffer.from(`P5\n${width} ${height}\n255\n`, 'ascii');
  const pixels = Buffer.alloc(width * height);
  for (let i = 0; i < pixels.length; i += 1) pixels[i] = (i % width) % 256;
  return Buffer.concat([header, pixels]);
}

const metric = (key: string, label: string, status: string, extra: Record<string, unknown> = {}) => ({
  key,
  label,
  unit: key.endsWith('rpm') ? 'rpm' : key.endsWith('mph') ? 'mph' : 'deg',
  status,
  value: null,
  source: 'replay',
  confidence: null,
  reason: null,
  recorded_status: null,
  validation: 'unvalidated',
  details: {},
  ...extra,
});

const review = {
  schema: 'openflight.session_review.v1',
  tester_id: testerId,
  analysis: {},
  status_vocabulary: ['accepted', 'experimental', 'rejected', 'not_requested', 'unavailable', 'processing_failed'],
  runs: [
    {
      arm_id: 'arm5',
      run: 'run-01',
      session_uuid: 'session-one',
      session_sha256: 'a'.repeat(64),
      errors: [],
      rejected_triggers: [{ ts: 't1', reason: 'no ball speed' }],
      rejected_trigger_count: 1,
      attempt_ledger: null,
    },
  ],
  attempts: [
    {
      attempt_id: 'session-one:1',
      kind: 'shot',
      arm_id: 'arm5',
      run: 'run-01',
      shot_number: 1,
      capture_id: 'camera_001',
      evidence: {
        preview_frames: {
          first: `${testerId}/arm5/paired/run-01/arm5/camera/camera_001/first.pgm`,
          trigger: `${testerId}/arm5/paired/run-01/arm5/camera/camera_001/trigger.pgm`,
          last: null,
        },
        impact_photo: `${testerId}/impact/camera_001.pgm`,
        camera_capture_error: null,
      },
      picture_verdict: {
        rung_id: 'full-300',
        color: 'green',
        reasons: [],
        meaning: 'picture quality only; not a fused-metric verdict',
      },
      live_processing: { outcome: 'complete', label: 'processing finished' },
      identity: { arm_id: 'arm5', capture_exposure_us: 300 },
      identity_evidence: { arm_id: 'session_start.config.camera_capture.output_dir location' },
      stages: {},
      overlay: {
        expected_region: { x_fraction: [0.1, 0.9], y_fraction: [0.4, 0.95], diameter_px: [9, 30] },
        candidates: [{ detector: 'scene', x: 714, y: 245.3, diameter_px: 23, status: 'rejected' }],
      },
      agreements: [
        {
          key: 'ops_ball_vs_iwr_track_speed',
          label: 'Ball speed: OPS radial vs IWR track',
          unit: 'mph',
          status: 'compared',
          values: [
            { source: 'ops_radial', value: 72.1 },
            { source: 'iwr_track', value: 81.3 },
          ],
          difference: 9.2,
          note: 'independent sensors; neither is a reference',
        },
      ],
      metrics: [
        metric('ball_speed_mph', 'Ball speed (OPS radial)', 'accepted', { value: 72.1 }),
        metric('spin_rpm', 'Spin (OPS)', 'experimental', {
          value: 3076.17,
          confidence: 0.249,
          reason: 'candidate only: quality experimental, confidence 0.249',
        }),
        metric('iwr_launch_horizontal_deg', 'Horizontal launch (IWR)', 'rejected', {
          confidence: 0.637,
          reason: 'withheld: hlcmf_v1_low_coherence',
        }),
        metric('camera_launch_horizontal_deg', 'Horizontal launch (camera)', 'rejected', {
          reason:
            'rejected_reference_ball_not_found (scene: candidate failed size or hitting-zone geometry at (714, 245), 23.0 px)',
        }),
        metric('ball_speed_total_mph', 'Total ball speed candidate', 'not_requested', {
          reason: 'the total-speed projection stage was not requested',
        }),
      ],
    },
    {
      attempt_id: 'run-01:camera_009',
      kind: 'camera_trigger_without_shot',
      arm_id: 'arm5',
      run: 'run-01',
      shot_number: null,
      capture_id: 'camera_009',
      evidence: { preview_frames: { first: null, trigger: null, last: null }, impact_photo: null },
      picture_verdict: null,
      live_processing: null,
      identity: {},
      identity_evidence: {},
      stages: {},
      overlay: {
        expected_region: { x_fraction: [0.1, 0.9], y_fraction: [0.4, 0.95], diameter_px: [9, 30] },
        candidates: [],
      },
      agreements: [],
      metrics: [],
      rejection: { reason: 'runtime setup readiness blocked at capture review' },
    },
  ],
  unattached_impact_photos: [],
  metric_status_counts: { spin_rpm: { experimental: 1 } },
};

const bundleEntry = {
  name: `${testerId}-session-bundle-20260925T010203Z.zip`,
  size_bytes: 2097152,
  sha256: 'b'.repeat(64),
};

async function json(route: Route, body: object, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
}

async function mockLive(page: Page) {
  const state = { phase: 'idle' as 'idle' | 'running' | 'done', posts: [] as unknown[] };
  await page.addInitScript((id) => localStorage.setItem('openflight-tester-id', id), testerId);
  await page.route('**/api/tester/analysis**', async (route) => {
    if (route.request().method() === 'POST') {
      state.posts.push(route.request().postDataJSON());
      state.phase = 'running';
      await json(route, { job: { action: 'analyze' }, analysis: { state: 'running', job: null, bundles: [] } }, 202);
      return;
    }
    if (state.phase === 'idle') return json(route, { state: 'never_run', job: null, review_ready: false, bundles: [] });
    if (state.phase === 'running')
      return json(route, {
        state: 'running',
        job: { phase: 'replay', progress: { done: 1, total: 5, current: 'arm5/run-01 shot 2' }, errors: [] },
        review_ready: false,
        bundles: [],
      });
    return json(route, {
      state: 'complete',
      job: { phase: 'finished', finished_at: '2026-09-25T01:02:03Z', replayed: 5, reused: 0, errors: [] },
      review_ready: true,
      bundles: [bundleEntry],
      latest_bundle: bundleEntry,
    });
  });
  await page.route('**/api/tester/session-review?**', (route) => json(route, review));
  await page.route('**/api/tester/session-review/file?**', (route) =>
    route.fulfill({ status: 200, contentType: 'image/x-portable-graymap', body: pgm(1280, 800) })
  );
  return state;
}

test('live review starts the analysis, survives a reload and shows every status', async ({ page }) => {
  const state = await mockLive(page);
  await page.goto('/session-review.html');
  await expect(page.locator('#analysis-state')).toContainText('Not analysed yet');

  await page.getByRole('button', { name: 'Analyse, review & package' }).click();
  await expect.poll(() => state.posts).toEqual([{ tester_id: testerId }]);
  await expect(page.locator('#analysis-state')).toContainText('Analysing (replay) 1 of 5');

  await page.reload();
  await expect(page.locator('#analysis-state')).toContainText('Analysing (replay) 1 of 5');
  await expect(page.getByRole('button', { name: 'Analyse, review & package' })).toBeDisabled();

  state.phase = 'done';
  await expect(page.locator('#analysis-state')).toContainText('Analysed', { timeout: 6000 });
  const card = page.locator('[data-attempt="session-one:1"]');
  const spin = card.locator('tr[data-metric="spin_rpm"]');
  await expect(spin).toContainText('3076');
  await expect(spin).toContainText('experimental');
  await expect(spin).toContainText('0.25');
  await expect(card.locator('tr[data-metric="iwr_launch_horizontal_deg"]')).toContainText('hlcmf_v1_low_coherence');
  await expect(card.locator('tr[data-metric="camera_launch_horizontal_deg"]')).toContainText('(714, 245)');
  await expect(card.locator('tr[data-metric="ball_speed_total_mph"]')).toContainText('not requested');
  await expect(card).toContainText('picture green · full-300');
  await expect(card).toContainText('live: processing finished');
  await expect(card.getByRole('img', { name: 'impact photo (club face)' })).toBeVisible();
  const trigger = card.getByRole('img', { name: 'trigger frame' });
  await trigger.scrollIntoViewIfNeeded();
  await expect(trigger).toHaveAttribute('data-state', 'loaded');
  expect(await trigger.evaluate((canvas: HTMLCanvasElement) => canvas.width)).toBe(640);

  const stray = page.locator('[data-attempt="run-01:camera_009"]');
  await expect(stray).toContainText('Not a shot: runtime setup readiness blocked');
  await page.getByLabel('Show').selectOption('experimental');
  await expect(stray).toHaveCount(0);
  await expect(page.locator('#runs')).toContainText('1 trigger(s) rejected');
  await expect(page.getByRole('link', { name: /Download the latest bundle/ })).toHaveAttribute(
    'href',
    `/api/tester/bundle?tester_id=${testerId}&name=${encodeURIComponent(bundleEntry.name)}`
  );
});

for (const viewport of KIOSK_VIEWPORTS) {
  test(`review cards fit a ${viewport.width}x${viewport.height} kiosk without sideways page scroll`, async ({
    page,
  }) => {
    const state = await mockLive(page);
    state.phase = 'done';
    await page.setViewportSize(viewport);
    await page.goto('/session-review.html');
    await expect(page.locator('[data-attempt="session-one:1"]')).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });
}

test.describe('bundle import', () => {
  let bundlePath = '';

  test.beforeAll(() => {
    const folder = mkdtempSync(join(tmpdir(), 'openflight-review-'));
    const output = execFileSync('uv', ['run', 'python', 'ui/tests/e2e/fixtures/make_session_bundle.py', folder], {
      cwd: REPO,
      encoding: 'utf-8',
      timeout: 120000,
    });
    bundlePath = JSON.parse(output.trim().split('\n').pop() as string).bundle;
  });

  test.beforeEach(async ({ page }) => {
    await page.route('**/api/tester/**', (route) => route.abort());
  });

  test('a bundle made on the Pi opens anywhere with every hash verified', async ({ page }) => {
    await page.goto('/session-review.html');
    await page.getByLabel('Open a session bundle (.zip) from any machine').setInputFiles(bundlePath);
    await expect(page.locator('#source')).toContainText(`Imported bundle`);
    await expect(page.locator('#source')).toContainText(`tester ${testerId}`);
    await expect(page.locator('#verification')).toContainText('files match their manifest SHA-256 hashes', {
      timeout: 20000,
    });
    const card = page.locator(`[data-attempt="session-bundle:1"]`);
    await expect(card.locator('tr[data-metric="iwr_launch_vertical_deg"]')).toContainText('input absent');
    await expect(card.locator('tr[data-metric="spin_rpm"]')).toBeVisible();
    const photo = card.getByRole('img', { name: 'impact photo (club face)' });
    await photo.scrollIntoViewIfNeeded();
    await expect(photo).toHaveAttribute('data-state', 'loaded');
    await expect(page.locator('#live-controls')).toBeHidden();
  });

  test('a changed byte in a bundle fails the hash check visibly', async ({ page }) => {
    const bytes = readFileSync(bundlePath);
    const marker = bytes.indexOf(Buffer.from('P5\n8 4\n255\n', 'ascii'));
    expect(marker).toBeGreaterThan(0);
    bytes[marker + 12] ^= 0xff;
    const tampered = join(mkdtempSync(join(tmpdir(), 'openflight-tampered-')), 'tampered.zip');
    writeFileSync(tampered, bytes);
    await page.goto('/session-review.html');
    await page.getByLabel('Open a session bundle (.zip) from any machine').setInputFiles(tampered);
    await expect(page.locator('#verification')).toContainText('Hash check FAILED', { timeout: 20000 });
    await expect(page.locator('#verification')).toContainText('does not match its manifest hash');
  });
});
