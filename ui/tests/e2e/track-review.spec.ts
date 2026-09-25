import { expect, test, type Page, type Route } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { KIOSK_VIEWPORTS } from './helpers';

test.use({ hasTouch: true });

const testerId = 'reviewer-1';
const scope = {
  tester_id: testerId,
  arm_id: 'arm5',
  run: 'run-01',
  run_dir: '/sessions/tester/arm5/run-01',
};
const hashes = { capture_npz_sha256: 'a'.repeat(64), metadata_sha256: 'b'.repeat(64) };
const capture = {
  capture_id: 'camera_001',
  ...hashes,
  frame_count: 3,
  width: 200,
  height: 100,
  sensor_timestamp_ns: ['1000000', '2000000', '3000000'],
  host_timestamp_ns: ['1100000', '2100000', '3100000'],
};

async function json(route: Route, body: object, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
}

function frameSvg(index: string) {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100"><rect width="200" height="100" fill="rgb(${Number(index) * 40},20,30)"/></svg>`;
}

async function mockReview(page: Page, onCompare?: (body: Record<string, unknown>) => void) {
  await page.addInitScript(({ key, value }) => localStorage.setItem(key, value), {
    key: 'openflight-tester-id',
    value: testerId,
  });
  await page.route('**/api/tester/attempts?**', (route) => json(route, { schema_version: 1, scopes: [scope] }));
  await page.route('**/api/tester/review/captures?**', (route) =>
    json(route, {
      scope: { tester_id: testerId, arm_id: scope.arm_id, run: scope.run },
      captures: [
        { id: capture.capture_id, available: true },
        { id: 'camera_incomplete', available: false, error: 'frames missing' },
      ],
    })
  );
  await page.route('**/api/tester/review/capture?**', (route) => json(route, capture));
  await page.route('**/api/tester/review/frame?**', async (route) => {
    const url = new URL(route.request().url());
    expect(url.searchParams.get('capture_npz_sha256')).toBe(hashes.capture_npz_sha256);
    expect(url.searchParams.get('metadata_sha256')).toBe(hashes.metadata_sha256);
    await route.fulfill({ contentType: 'image/svg+xml', body: frameSvg(url.searchParams.get('frame_index') || '0') });
  });
  await page.route('**/api/tester/review/compare', async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    onCompare?.(body);
    const response = {
      version: 1,
      status: 'conditional',
      compatibility: { status: 'unverified', reasons: ['capture profile remains a declared hypothesis'] },
      counts: {
        tracks: 1,
        observations_attempted: 2,
        observations_compared: 1,
        observations_withheld: 1,
        intervals_attempted: 1,
        intervals_compared: 0,
        intervals_withheld: 1,
      },
      tracks: [
        {
          id: 'ball-main',
          point_kind: 'ball_center',
          observations: [
            { frame_index: 0, status: 'compared', angular_difference_deg: 0.125, position_difference_m: 0.01 },
            { frame_index: 1, status: 'withheld', error: 'pixel was explicitly marked missing' },
          ],
          intervals: [
            {
              start_frame_index: 0,
              end_frame_index: 1,
              start_timestamp_ns: 'EXACT_INTEGER',
              status: 'withheld',
              error: 'an endpoint observation was withheld',
            },
          ],
        },
      ],
    };
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(response).replace('"EXACT_INTEGER"', '1000000000000000001'),
    });
  });
}

async function openCapture(page: Page) {
  await page.goto('/track-review.html');
  await page.locator('#run').selectOption({ label: 'arm5 · run-01' });
  await page.locator('#capture').selectOption(capture.capture_id);
  await expect(page.locator('#frame')).toHaveAttribute('data-loaded', 'true');
}

async function loadInputFiles(page: Page) {
  for (const id of ['candidate-file', 'profile-file', 'setup-file']) {
    await page
      .locator(`#${id}`)
      .setInputFiles({ name: `${id}.json`, mimeType: 'application/json', buffer: Buffer.from('{}') });
  }
}

async function downloadedJson(download: import('@playwright/test').Download) {
  return JSON.parse(await downloadedText(download));
}

async function downloadedText(download: import('@playwright/test').Download) {
  const path = await download.path();
  if (!path) throw new Error('download has no local path');
  return readFile(path, 'utf8');
}

test('maps a tap to the saved-image grid, refines it, and exports explicit missing evidence', async ({ page }) => {
  let posted: Record<string, unknown> | undefined;
  await mockReview(page, (body) => {
    posted = body;
  });
  await openCapture(page);

  const box = await page.locator('#frame').boundingBox();
  if (!box) throw new Error('frame has no box');
  await page.mouse.click(box.x + box.width / 4, box.y + box.height / 2);
  expect(Number(await page.locator('#pixel-x').inputValue())).toBeCloseTo(49.5, 0);
  expect(Number(await page.locator('#pixel-y').inputValue())).toBeCloseTo(49.5, 0);

  await page.locator('#pixel-x').fill('12.5');
  await page.locator('#pixel-y').fill('20.5');
  await page.getByRole('button', { name: 'Apply x/y' }).click();
  await page.getByRole('button', { name: 'Next' }).click();
  await expect(page.locator('#frame-time')).toContainText('Frame 2/3');
  await page.getByRole('button', { name: 'Mark missing' }).click();
  await loadInputFiles(page);
  await page.getByRole('button', { name: 'Compare saved tracks' }).click();

  await expect(page.locator('#report')).toContainText('Conditional model disagreement only');
  await expect(page.locator('#report')).toContainText('angular Δ 0.1250 deg');
  await expect(page.locator('#report')).toContainText('withheld — pixel was explicitly marked missing');
  expect(posted).toMatchObject({
    tester_id: testerId,
    arm_id: scope.arm_id,
    run_dir: scope.run_dir,
    capture_id: capture.capture_id,
    ...hashes,
  });
  const manifest = JSON.parse(String(posted?.tracks_json));
  expect(manifest.tracks).toHaveLength(1);
  expect(manifest.tracks[0]).toMatchObject({ id: 'ball-main', object: 'ball', point_kind: 'ball_center' });
  expect(manifest.tracks[0].observations).toEqual([
    { frame_index: 0, pixel_px: [12.5, 20.5], radar_range_m: null },
    { frame_index: 1, pixel_px: null, radar_range_m: null },
  ]);

  const manifestDownload = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download manifest' }).click();
  const savedManifest = await downloadedJson(await manifestDownload);
  expect(savedManifest).toEqual(manifest);
  const reportDownload = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download report' }).click();
  const savedReportDownload = await reportDownload;
  const savedReportText = await downloadedText(savedReportDownload);
  const savedReport = JSON.parse(savedReportText);
  expect(savedReport.status).toBe('conditional');
  expect(savedReportText).toContain('"start_timestamp_ns":1000000000000000001');

  await page.locator('#source-description').fill('Second manual review');
  await expect(page.locator('#report')).toContainText('No current report');
  await expect(page.getByRole('button', { name: 'Download report' })).toBeDisabled();
});

test('draft identity includes both hashes and survives reload without rebinding', async ({ page }) => {
  await mockReview(page);
  await openCapture(page);
  await page.locator('#pixel-x').fill('18.25');
  await page.locator('#pixel-y').fill('22.75');
  await page.getByRole('button', { name: 'Apply x/y' }).click();
  const keys = await page.evaluate(() =>
    Object.keys(localStorage).filter((key) => key.startsWith('openflight-track-review-v1:'))
  );
  expect(keys).toHaveLength(1);
  expect(keys[0]).toContain(encodeURIComponent(hashes.capture_npz_sha256));
  expect(keys[0]).toContain(encodeURIComponent(hashes.metadata_sha256));

  await page.reload();
  await page.locator('#run').selectOption({ label: 'arm5 · run-01' });
  await page.locator('#capture').selectOption(capture.capture_id);
  await expect(page.locator('#pixel-x')).toHaveValue('18.25');
  await expect(page.locator('#pixel-y')).toHaveValue('22.75');

  const changedCapture = { ...capture, capture_npz_sha256: 'c'.repeat(64) };
  await page.unroute('**/api/tester/review/capture?**');
  await page.route('**/api/tester/review/capture?**', (route) => json(route, changedCapture));
  await page.unroute('**/api/tester/review/frame?**');
  await page.route('**/api/tester/review/frame?**', (route) =>
    route.fulfill({ contentType: 'image/svg+xml', body: frameSvg('0') })
  );
  await page.locator('#capture').selectOption('');
  await page.locator('#capture').selectOption(capture.capture_id);
  await expect(page.locator('#frame')).toHaveAttribute('data-loaded', 'true');
  await expect(page.locator('#pixel-x')).toHaveValue('');
  await expect(page.locator('#point-status')).toContainText('no annotation');
});

test('late frame response cannot replace the newly selected frame', async ({ page }) => {
  await mockReview(page);
  let releaseFrameOne!: () => void;
  const frameOne = new Promise<void>((resolve) => {
    releaseFrameOne = resolve;
  });
  await page.route('**/api/tester/review/frame?**', async (route) => {
    const index = new URL(route.request().url()).searchParams.get('frame_index') || '0';
    if (index === '1') await frameOne;
    await route.fulfill({ contentType: 'image/svg+xml', body: frameSvg(index) });
  });
  await openCapture(page);
  await page.getByRole('button', { name: 'Next' }).click();
  await page.locator('#frame-index').fill('0');
  await page.locator('#frame-index').press('Enter');
  await expect(page.locator('#frame-time')).toContainText('Frame 1/3');
  releaseFrameOne();
  await page.waitForTimeout(50);
  await expect(page.locator('#frame-time')).toContainText('Frame 1/3');
});

test('late run discovery cannot restore a previous tester scope', async ({ page }) => {
  await page.addInitScript(({ key, value }) => localStorage.setItem(key, value), {
    key: 'openflight-tester-id',
    value: testerId,
  });
  let releaseOld!: () => void;
  const oldRequest = new Promise<void>((resolve) => {
    releaseOld = resolve;
  });
  await page.route('**/api/tester/attempts?**', async (route) => {
    const requested = new URL(route.request().url()).searchParams.get('tester_id');
    if (requested === testerId) await oldRequest;
    const selected = requested === 'reviewer-2' ? { ...scope, tester_id: 'reviewer-2', run: 'run-new' } : scope;
    await json(route, { schema_version: 1, scopes: [selected] });
  });
  await page.goto('/track-review.html');
  await page.locator('#tester-id').fill('reviewer-2');
  await expect(page.locator('#run')).toContainText('run-new');
  releaseOld();
  await page.waitForTimeout(50);
  await expect(page.locator('#run')).toContainText('run-new');
  await expect(page.locator('#run')).not.toContainText('run-01');
});

test('a stalled saved-run read times out and leaves recovery controls available', async ({ page }) => {
  await page.addInitScript(({ key, value }) => localStorage.setItem(key, value), {
    key: 'openflight-tester-id',
    value: testerId,
  });
  await page.addInitScript(() => {
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (input, init) => {
      if (!String(input).includes('/api/tester/attempts?')) return originalFetch(input, init);
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
  await page.goto('/track-review.html');
  await expect(page.locator('#request-error')).toContainText('timed out', { timeout: 6500 });
  await expect(page.getByRole('button', { name: 'Refresh' })).toBeEnabled();
});

test('a stalled frame body times out without enabling annotation', async ({ page }) => {
  await mockReview(page);
  await page.unroute('**/api/tester/review/frame?**');
  await page.route('**/api/tester/review/frame?**', async () => {
    await new Promise(() => {});
  });
  await page.goto('/track-review.html');
  await page.locator('#run').selectOption({ label: 'arm5 · run-01' });
  await page.locator('#capture').selectOption(capture.capture_id);
  await expect(page.locator('#request-error')).toContainText('timed out', { timeout: 6500 });
  await expect(page.locator('#frame')).toHaveAttribute('data-loaded', 'false');
  await expect(page.getByRole('button', { name: 'Mark missing' })).toBeDisabled();
});

test('a comparison response arriving after an edit cannot restore a stale report', async ({ page }) => {
  await mockReview(page);
  let releaseCompare!: () => void;
  const compareGate = new Promise<void>((resolve) => {
    releaseCompare = resolve;
  });
  await page.route('**/api/tester/review/compare', async (route) => {
    await compareGate;
    await json(route, {
      status: 'conditional',
      compatibility: { status: 'unverified', reasons: [] },
      counts: { observations_compared: 1, observations_withheld: 0, intervals_compared: 0 },
      tracks: [],
    });
  });
  await openCapture(page);
  await page.getByRole('button', { name: 'Mark missing' }).click();
  await loadInputFiles(page);
  await page.getByRole('button', { name: 'Compare saved tracks' }).click();
  await page.locator('#source-description').fill('Edited while comparison was pending');
  releaseCompare();
  await page.waitForTimeout(50);
  await expect(page.locator('#report')).toContainText('No current report');
  await expect(page.getByRole('button', { name: 'Download report' })).toBeDisabled();
});

test('409 requires an explicit capture reload and never silently rebinds the draft', async ({ page }) => {
  await mockReview(page);
  let stale = false;
  await page.route('**/api/tester/review/frame?**', async (route) => {
    if (stale) return json(route, { error: 'capture files changed; reload the capture' }, 409);
    const index = new URL(route.request().url()).searchParams.get('frame_index') || '0';
    await route.fulfill({ contentType: 'image/svg+xml', body: frameSvg(index) });
  });
  await openCapture(page);
  await page.getByRole('button', { name: 'Mark missing' }).click();
  stale = true;
  await page.getByRole('button', { name: 'Next' }).click();
  await expect(page.locator('#frame-time')).toContainText('Reload the capture');
  await expect(page.getByRole('button', { name: 'Mark missing' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Compare saved tracks' })).toBeDisabled();
});

test('browser draft storage failures remain visible to the reviewer', async ({ page }) => {
  await mockReview(page);
  await openCapture(page);
  await page.evaluate(() => {
    Storage.prototype.setItem = () => {
      throw new DOMException('quota exhausted', 'QuotaExceededError');
    };
  });
  await page.getByRole('button', { name: 'Mark missing' }).click();
  await expect(page.locator('#storage-error')).toContainText('Draft could not be saved: quota exhausted');
  await expect(page.locator('#point-status')).toContainText('explicitly marked missing');
});

for (const viewport of KIOSK_VIEWPORTS) {
  test(`touch controls fit and feature-track scroller preserves taps at ${viewport.width}x${viewport.height}`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    await mockReview(page);
    await openCapture(page);
    for (let index = 0; index < 8; index += 1) {
      await page.getByRole('button', { name: 'New club feature' }).tap();
    }
    await page.getByRole('button', { name: 'Ball · ball-main' }).tap();
    await expect(page.locator('#track-name')).toHaveValue('ball-main');
    const scroller = page.locator('#track-tabs');
    const scrollSize = await scroller.evaluate((node) => ({ width: node.clientWidth, scrollWidth: node.scrollWidth }));
    expect(scrollSize.scrollWidth).toBeGreaterThan(scrollSize.width);
    const box = await scroller.boundingBox();
    if (!box) throw new Error('track scroller has no box');
    await page.mouse.move(box.x + box.width - 30, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + 30, box.y + box.height / 2, { steps: 5 });
    await page.mouse.up();
    await expect(page.locator('#track-name')).toHaveValue('ball-main');
    await page.getByRole('button', { name: 'Club · club-feature-8' }).tap();
    await expect(page.locator('#track-name')).toHaveValue('club-feature-8');
    const overflow = await page.locator('main').evaluate((main) => ({
      right: main.getBoundingClientRect().right - document.documentElement.clientWidth,
      bottom: main.getBoundingClientRect().bottom - document.documentElement.scrollHeight,
    }));
    expect(overflow.right).toBeLessThanOrEqual(1);
    expect(overflow.bottom).toBeLessThanOrEqual(1);
  });
}
