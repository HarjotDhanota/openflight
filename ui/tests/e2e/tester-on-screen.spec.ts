import { expect, test, type Page } from '@playwright/test';
import { DEFAULT_TESTER, fulfillJson, mockSetupDone, RECORDING, useTester } from './testerHelpers';

/**
 * The tester page on the unit's own screen (25 Oct): `start-tester.sh --on-screen` opens it
 * with ?screen=unit in the kiosk's Electron shell, and that one window switches between
 * the page and the kiosk. A page on a phone keeps the kiosk in its own window.
 */
const KIOSK = 'http://127.0.0.1:8080/';
const KIOSK_HERE = `${KIOSK}?from=tester`;

type World = { freePlay: object; kiosk: object | null };

async function mockOnScreenWorld(page: Page, world: World) {
  await mockSetupDone(page, world);
  await page.unroute('**/api/tester/status**');
  await page.route('**/api/tester/status**', (route) =>
    fulfillJson(route, { free_play: world.freePlay, ...(world.kiosk ? { kiosk: world.kiosk } : {}) })
  );
  await page.route(`${KIOSK}**`, (route) => route.fulfill({ contentType: 'text/html', body: 'scoreboard' }));
}

async function startFreePlay(page: Page, world: World) {
  const starts: Record<string, unknown>[] = [];
  await page.route('**/api/tester/run', async (route) => {
    starts.push(route.request().postDataJSON());
    world.freePlay = RECORDING;
    world.kiosk = { state: 'starting', progress_message: 'Starting the kiosk…', url: 'http://localhost:8080/' };
    await fulfillJson(route, { job: { state: 'running', action: 'free_play' }, free_play: RECORDING }, 202);
  });
  await page.locator('#free-play-resolution button[data-resolution="arm5"]').tap();
  await page.locator('#first-club button[data-club="7-iron"]').tap();
  await page.locator('#step-free-play').tap();
  await expect.poll(() => starts.length).toBe(1);
  return starts[0];
}

test.use({ viewport: { width: 1024, height: 600 }, hasTouch: true });

test('free play from the unit screen switches this window to the kiosk once it answers', async ({ page }) => {
  const world: World = { freePlay: { enabled: false }, kiosk: null };
  await useTester(page);
  await mockOnScreenWorld(page, world);
  await page.goto('/tester.html?screen=unit');

  const start = await startFreePlay(page, world);
  // no second window: the kiosk starts without one and shows in this one
  expect(start).toMatchObject({ action: 'free_play', club: '7-iron', kiosk_window: 'shared' });
  await expect(page.locator('#kiosk-progress')).toHaveText(
    'Starting the kiosk (about 20 s). The scoreboard opens on this screen when it is ready.'
  );
  expect(page.url()).toContain('/tester.html');

  world.kiosk = { state: 'running', progress_message: null, url: 'http://localhost:8080/' };
  await expect(page).toHaveURL(KIOSK_HERE);
});

test('back from the kiosk, the page stays put and Kiosk view goes there again in one tap', async ({ page }) => {
  const world: World = {
    freePlay: RECORDING,
    kiosk: { state: 'running', progress_message: null, url: 'http://localhost:8080/' },
  };
  await useTester(page);
  await mockOnScreenWorld(page, world);
  // the unit screen was marked once; the kiosk's Tester button comes back without the mark
  await page.goto('/tester.html?screen=unit');
  await page.goto('/tester.html');
  await expect(page.locator('#free-play-status')).toHaveText('Free play is recording.');
  await expect(page.locator('#step-7')).toHaveAttribute('data-state', 'current');
  // nothing switches by itself: the switch belongs to the start it was asked for
  await page.waitForTimeout(2500);
  expect(page.url()).toContain('/tester.html');

  let popups = 0;
  page.on('popup', () => (popups += 1));
  await page.locator('#kiosk-view').tap();
  await expect(page).toHaveURL(KIOSK_HERE);
  expect(popups).toBe(0);
});

test('a phone starts free play with the kiosk in its own window, and stays on the page', async ({ page }) => {
  const world: World = { freePlay: { enabled: false }, kiosk: null };
  await useTester(page);
  await mockOnScreenWorld(page, world);
  await page.goto('/tester.html');

  const start = await startFreePlay(page, world);
  expect(start).toMatchObject({ action: 'free_play', kiosk_window: 'own' });
  world.kiosk = { state: 'running', progress_message: null, url: 'http://localhost:8080/' };
  await expect(page.locator('#kiosk-view')).toBeEnabled();
  await page.waitForTimeout(2500);
  expect(page.url()).toContain('/tester.html');
});

test('a reload keeps the tester and the switch it is waiting for', async ({ page }) => {
  const world: World = { freePlay: { enabled: false }, kiosk: null };
  await useTester(page);
  await mockOnScreenWorld(page, world);
  await page.goto('/tester.html?screen=unit');
  await startFreePlay(page, world);
  await expect(page.locator('#kiosk-progress')).toContainText('The scoreboard opens on this screen');
  await page.reload();
  await expect(page.locator('#kiosk-progress')).toContainText('The scoreboard opens on this screen');
  await expect(page.locator('#tester-id')).toHaveValue(DEFAULT_TESTER);
  await expect(page.locator('#free-play-status')).toHaveText('Free play is recording.');
  world.kiosk = { state: 'running', progress_message: null, url: 'http://localhost:8080/' };
  await expect(page).toHaveURL(KIOSK_HERE);
});

test('the package is ready: Done on the page closes the kiosk', async ({ page }) => {
  const ready = { ...RECORDING, phase: 'ready', bundle: { name: 'session.zip' } };
  const world: World = { freePlay: ready, kiosk: { state: 'running', progress_message: null, url: KIOSK } };
  await useTester(page);
  await mockOnScreenWorld(page, world);
  const closes: object[] = [];
  await page.route('**/api/tester/free-play/close', async (route) => {
    closes.push(route.request().postDataJSON());
    world.freePlay = { ...ready, phase: 'closed' };
    await fulfillJson(route, { closing: true });
  });
  await page.goto('/tester.html?screen=unit');
  await expect(page.locator('#free-play-status')).toHaveText(
    'Package ready: session.zip. Send it from step 8 below. Then tap Done.'
  );
  const done = page.locator('#free-play-done');
  await expect(done).toBeVisible();
  expect((await done.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await done.tap();
  await expect.poll(() => closes).toEqual([{ tester_id: DEFAULT_TESTER, run_id: 'run-1' }]);
  await expect(done).toBeHidden();
});

test.describe('a touchscreen that reports a finger as a mouse', () => {
  test.use({ hasTouch: false });

  async function mouseDrag(page: Page, from: { x: number; y: number }, dy: number) {
    await page.mouse.move(from.x, from.y);
    await page.mouse.down();
    for (let step = 1; step <= 8; step += 1) await page.mouse.move(from.x, from.y + (dy * step) / 8);
    await page.mouse.up();
  }

  test('drags the page on the unit screen, and a drag never taps what it started on', async ({ page }) => {
    const world: World = { freePlay: { enabled: false }, kiosk: null };
    await useTester(page);
    await mockOnScreenWorld(page, world);
    await page.goto('/tester.html?screen=unit');
    await expect(page.locator('#step-1')).toBeVisible();
    await page.evaluate(() => window.scrollTo(0, 0));
    const indoors = page.getByRole('button', { name: 'Outdoors', exact: true });
    const box = (await indoors.boundingBox())!;
    const before = await page.evaluate(() => window.scrollY);
    await mouseDrag(page, { x: box.x + box.width / 2, y: box.y + box.height / 2 }, -200);
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(before + 150);
    await expect(indoors).toHaveAttribute('aria-pressed', 'false');
    // a tap still taps
    await indoors.click();
    await expect(indoors).toHaveAttribute('aria-pressed', 'true');
  });

  test('a desktop browser keeps its own mouse: no drag scrolling off the unit screen', async ({ page }) => {
    const world: World = { freePlay: { enabled: false }, kiosk: null };
    await useTester(page);
    await mockOnScreenWorld(page, world);
    await page.goto('/tester.html');
    await expect(page.locator('#step-1')).toBeVisible();
    await page.evaluate(() => window.scrollTo(0, 0));
    await mouseDrag(page, { x: 400, y: 400 }, -200);
    expect(await page.evaluate(() => window.scrollY)).toBe(0);
  });
});

test('step 8 on the unit screen says where the package already is, with no Download', async ({ page }) => {
  const bundle = { name: 'demo-session-bundle.zip', size_bytes: 1_234_567_890, sha256: 'a'.repeat(64) };
  const world: World = {
    freePlay: { ...RECORDING, phase: 'closed', bundle },
    kiosk: { state: 'stopped', progress_message: null, url: 'http://localhost:8080/' },
  };
  await useTester(page);
  await mockOnScreenWorld(page, world);
  await page.unroute('**/api/tester/status**');
  await page.route('**/api/tester/status**', (route) =>
    fulfillJson(route, {
      free_play: world.freePlay,
      kiosk: world.kiosk,
      analysis: {
        state: 'complete',
        reason: null,
        job: { state: 'complete' },
        review_ready: true,
        bundles: [bundle],
        latest_bundle: bundle,
        send_folder: '~/OpenFlight-to-send',
        send_copy: {
          name: bundle.name,
          folder: '~/OpenFlight-to-send',
          size_bytes: bundle.size_bytes,
          method: 'link',
          error: null,
        },
      },
    })
  );
  await page.goto('/tester.html?screen=unit');
  await expect(page.locator('#bundle-size')).toHaveText(`${bundle.name} · 1.23 GB`);
  // a download here would only copy it to the same unit, through a save dialog a finger can't use
  await expect(page.locator('#bundle-link')).toBeHidden();
  await expect(page.locator('#send-folder')).toHaveText(
    'Saved on this unit in ~/OpenFlight-to-send/. To send it, open this page on a phone or laptop and tap Download there, or copy it to a USB stick.'
  );
});
