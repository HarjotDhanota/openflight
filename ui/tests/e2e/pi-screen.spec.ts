import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { extname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';
import { gotoApp, resetSession, simulateShot, withControlSocket } from './helpers';
import { DEFAULT_TESTER, fulfillJson, mockSetupDone, RECORDING, useTester } from './testerHelpers';

/**
 * The tester page and the kiosk in one window on the unit's own screen (25 Oct), at
 * both Pi screen sizes: the whole switch (tester page → free play → the kiosk → a shot →
 * Tester → back → Stop & package → Done), every kiosk tab, the club picker, the replay
 * player and every tester step, each checked for what a finger cannot reach.
 *
 * The tester page is served from its own origin, as the tester server serves it beside
 * the kiosk; its API is mocked. Screenshots go to PI_SCREEN_SHOTS_DIR when set.
 */
const SIZES = [
  { width: 1024, height: 600 },
  { width: 800, height: 480 },
] as const;
const PUBLIC_DIR = fileURLToPath(new URL('../../public/', import.meta.url));
const TESTER_ORIGIN = 'http://127.0.0.1:18765';
const TESTER_URL = `${TESTER_ORIGIN}/`;
const UI_PORT = process.env.OPENFLIGHT_E2E_UI_PORT ?? '5173';
const KIOSK_ORIGIN = `http://127.0.0.1:${UI_PORT}`;
const KIOSK_STATUS = { state: 'running', progress_message: null, url: `http://localhost:${UI_PORT}/` };
const TYPES: Record<string, string> = {
  '.html': 'text/html',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.js': 'text/javascript',
  '.css': 'text/css',
  '.json': 'application/json',
};

test.use({ hasTouch: true });

function shotPath(name: string, size: { width: number; height: number }) {
  const dir = process.env.PI_SCREEN_SHOTS_DIR;
  const file = `${name}-${size.width}x${size.height}.png`;
  if (!dir) return test.info().outputPath(file);
  mkdirSync(dir, { recursive: true });
  return join(dir, file);
}

/** What a finger cannot reach: a sideways page, controls off screen or too small to hit. */
async function reachProblems(page: Page, { fitsHeight }: { fitsHeight: boolean }) {
  return page.evaluate((fits) => {
    const width = window.innerWidth;
    const height = window.innerHeight;
    const problems: string[] = [];
    const root = document.documentElement;
    if (root.scrollWidth > width + 1) problems.push(`the page scrolls sideways (${root.scrollWidth} > ${width})`);
    if (fits && root.scrollHeight > height + 1) problems.push(`the page scrolls (${root.scrollHeight} > ${height})`);
    const inScroller = (node: Element) => {
      for (let parent = node.parentElement; parent; parent = parent.parentElement) {
        const style = getComputedStyle(parent);
        if (/(auto|scroll)/.test(style.overflowX) && parent.scrollWidth > parent.clientWidth + 1) return true;
      }
      return false;
    };
    const controls = document.querySelectorAll(
      'button, a[href], select, input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=range]), [role="button"]'
    );
    for (const node of controls) {
      const box = node.getBoundingClientRect();
      if (!box.width || !box.height || getComputedStyle(node).visibility === 'hidden') continue;
      if (node.closest('[aria-hidden="true"], [inert]')) continue;
      // inside a closed <details> it is not on the screen, though Chromium still lays it out
      const details = node.closest('details:not([open])');
      if (details && !node.closest('summary')) continue;
      const name = (node.getAttribute('aria-label') || node.textContent || node.id || node.tagName)
        .replace(/\s+/g, ' ')
        .trim()
        .slice(0, 48);
      if (!inScroller(node) && (box.left < -1 || box.right > width + 1)) {
        problems.push(`off the screen sideways: "${name}" (${Math.round(box.left)}–${Math.round(box.right)})`);
      }
      if (box.height < 36 || box.width < 36) {
        problems.push(`too small to tap: "${name}" (${Math.round(box.width)}×${Math.round(box.height)})`);
      }
    }
    return problems;
  }, fitsHeight);
}

/**
 * Every region of the kiosk that overflows must scroll when a finger drags it, even on a
 * screen that reports the finger as a mouse (ui/AGENTS.md): the regions that did not move.
 */
async function undraggableScrollers(page: Page) {
  const scrollers = await page.evaluate(() => {
    const found: { x: number; y: number; name: string; index: number }[] = [];
    const nodes = [...document.querySelectorAll('.panel-app__main *')];
    nodes.forEach((node, index) => {
      const style = getComputedStyle(node);
      if (!/(auto|scroll)/.test(style.overflowY) || node.scrollHeight <= node.clientHeight + 4) return;
      const box = node.getBoundingClientRect();
      if (box.height < 60) return;
      node.setAttribute('data-pi-scroller', String(index));
      found.push({
        x: box.x + box.width / 2,
        y: box.y + box.height / 2,
        name: `${node.tagName.toLowerCase()}.${[...node.classList].join('.')}`,
        index,
      });
    });
    return found;
  });
  const stuck: string[] = [];
  for (const scroller of scrollers) {
    const node = page.locator(`[data-pi-scroller="${scroller.index}"]`);
    const before = await node.evaluate((element) => element.scrollTop);
    await page.mouse.move(scroller.x, scroller.y);
    await page.mouse.down();
    for (let step = 1; step <= 8; step += 1) await page.mouse.move(scroller.x, scroller.y - step * 15);
    await page.mouse.up();
    const after = await node.evaluate((element) => element.scrollTop);
    if (after <= before) stuck.push(`does not scroll by dragging: ${scroller.name}`);
    await node.evaluate((element) => (element.scrollTop = 0));
  }
  return stuck;
}

/** Screenshot a screen and check it; PI_SCREEN_REPORT collects the problems in a file instead. */
async function check(
  page: Page,
  name: string,
  size: { width: number; height: number },
  fitsHeight = false,
  dragScroll = false
) {
  await page.waitForTimeout(300);
  await page.screenshot({ path: shotPath(name, size) });
  const problems = await reachProblems(page, { fitsHeight });
  if (dragScroll) problems.push(...(await undraggableScrollers(page)));
  const report = process.env.PI_SCREEN_REPORT;
  if (!report) {
    expect(problems, `${name} at ${size.width}×${size.height}`).toEqual([]);
    return;
  }
  const found = existsSync(report) ? JSON.parse(readFileSync(report, 'utf8')) : {};
  found[`${name}-${size.width}x${size.height}`] = problems;
  writeFileSync(report, JSON.stringify(found, null, 2));
}

/** The tester page on its own origin: the files of ui/public, and a 404 for any API not mocked. */
async function serveTesterOrigin(page: Page) {
  await page.route(`${TESTER_ORIGIN}/**`, async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.startsWith('/api/')) return fulfillJson(route, { error: 'not mocked' }, 404);
    const file = join(PUBLIC_DIR, path === '/' ? 'tester.html' : path.slice(1));
    if (!existsSync(file)) return route.fulfill({ status: 404, body: 'not found' });
    return route.fulfill({ contentType: TYPES[extname(file)] ?? 'application/octet-stream', body: readFileSync(file) });
  });
}

type World = { freePlay: Record<string, unknown>; kiosk: object | null };
// step 5 done: both modes' light measured
const STUDY_ARMS = ['arm5', 'arm6'].map((arm_id) => ({
  arm_id,
  label: arm_id === 'arm5' ? '1280×800' : '640×400',
  isolates: 'blur',
  exposure_us: 300,
  gain: 2.0,
  gain_screen: { age_s: 60, stale: false, prompt: null },
  attempted: 0,
  accepted: 0,
  target: 30,
  complete: false,
}));

/** The tester's world, every setup step done; free play and the kiosk move as `world` says. */
async function testerWorld(page: Page, world: World) {
  await serveTesterOrigin(page);
  await mockSetupDone(page, world);
  await page.unroute('**/api/tester/status**');
  await page.route('**/api/tester/status**', (route) =>
    fulfillJson(route, {
      free_play: world.freePlay,
      study: { arms: STUDY_ARMS },
      ...(world.kiosk ? { kiosk: world.kiosk } : {}),
    })
  );
  await page.route('**/api/tester/ready-light**', (route) =>
    fulfillJson(route, {
      schema_version: 1,
      state: 'green',
      word: 'SWING',
      cause: 'Ready for a swing',
      time_left_s: null,
      causes: [],
      since: 1,
      checked_at: 2,
      hold: null,
      swing: null,
      source: 'kiosk',
    })
  );
  await page.route('**/api/tester/run', async (route) => {
    world.freePlay = { ...RECORDING };
    world.kiosk = { state: 'starting', progress_message: 'Starting the kiosk…', url: KIOSK_STATUS.url };
    await fulfillJson(route, { job: { state: 'running', action: 'free_play' }, free_play: world.freePlay }, 202);
  });
  await page.route('**/api/tester/free-play/close', async (route) => {
    world.freePlay = { ...world.freePlay, phase: 'closed' };
    world.kiosk = { state: 'stopped', progress_message: null, url: KIOSK_STATUS.url };
    await fulfillJson(route, { closing: true });
  });
}

/** The kiosk's free-play run and its link back, as a kiosk the tester started answers them. */
async function kioskWorld(page: Page, world: World) {
  await page.route('**/api/free-play', async (route) => {
    if (route.request().method() === 'POST' && route.request().postDataJSON().action === 'stop') {
      world.freePlay = { ...world.freePlay, phase: 'packaging' };
    }
    await fulfillJson(route, {
      ...world.freePlay,
      tester_url: TESTER_URL,
      exposure_study: { phase: 'ladder', text: 'Exposure study: 150 µs · swing 3 of 5 · setting 3 of 6' },
    });
  });
  await page.route('**/api/tester-page', (route) =>
    fulfillJson(route, { available: true, url: TESTER_URL, reason: null, final: false })
  );
}

async function greenLight(page: Page) {
  await page.evaluate(async () => {
    const modulePath = '/src/stores/useSystemStore.ts';
    const { useSystemStore } = await import(modulePath);
    useSystemStore
      .getState()
      .setReadyLight({ state: 'green', cause: 'Ready for a swing', word: 'SWING', swing: null, time_left_s: null });
  });
}

for (const size of SIZES) {
  test.describe(`the unit's screen at ${size.width}x${size.height}`, () => {
    test.use({ viewport: size });

    test('tester page → free play → kiosk → a shot → Tester → back → Stop & package → Done', async ({ page }) => {
      await withControlSocket((socket) => resetSession(socket));
      const world: World = { freePlay: { enabled: false }, kiosk: null };
      await useTester(page);
      await testerWorld(page, world);
      await kioskWorld(page, world);
      let popups = 0;
      page.on('popup', () => (popups += 1));

      await page.goto(`${TESTER_URL}?screen=unit`);
      await expect(page.locator('#step-7')).toHaveAttribute('data-state', 'current');
      await page.locator('#free-play-resolution button[data-resolution="arm5"]').tap();
      await page.locator('#first-club button[data-club="7-iron"]').tap();
      await page.locator('#step-free-play').scrollIntoViewIfNeeded();
      await check(page, '01-tester-start-free-play', size);
      await page.locator('#step-free-play').tap();
      await expect(page.locator('#kiosk-progress')).toContainText('The scoreboard opens on this screen');
      await check(page, '02-tester-kiosk-starting', size);

      // the kiosk answers: this window switches to it, with no club picker and no second window
      world.kiosk = KIOSK_STATUS;
      await expect(page).toHaveURL(`${KIOSK_ORIGIN}/?from=tester`);
      await expect(page.getByRole('dialog', { name: 'Select club' })).toHaveCount(0);
      const back = page.getByRole('button', { name: 'Back to the tester page' });
      await expect(back).toBeVisible();
      await greenLight(page);
      await expect(page.locator('.ready-light__word')).toHaveText('SWING');
      await expect(page.getByText('Free play: recording')).toBeVisible();
      await check(page, '03-kiosk-free-play', size, true);

      // a simulated shot, as the mock kiosk records one
      await page.getByRole('button', { name: 'Simulate shot' }).tap();
      await expect(page.locator('.live-panel__grid .metric-card').first()).not.toContainText('—');
      await check(page, '04-kiosk-shot', size, true);

      // Tester: back to the page, where it was
      await back.tap();
      await expect(page).toHaveURL(TESTER_URL);
      await expect(page.locator('#tester-id')).toHaveValue(DEFAULT_TESTER);
      await expect(page.locator('#free-play-status')).toHaveText('Free play is recording.');
      await expect(page.locator('#step-7')).toHaveAttribute('data-state', 'current');
      await check(page, '05-tester-back-recording', size);

      // Kiosk view: the kiosk again in one tap, the shot still there
      await page.locator('#kiosk-view').tap();
      await expect(page).toHaveURL(`${KIOSK_ORIGIN}/?from=tester`);
      await expect(page.locator('.live-panel__grid .metric-card').first()).not.toContainText('—');

      // Stop & package on the kiosk: the window returns to the page, which shows the packaging
      await page.getByRole('button', { name: 'Stop & package', exact: true }).tap();
      await expect(page).toHaveURL(TESTER_URL);
      await expect(page.locator('#free-play-status')).toHaveText('packaging');
      await check(page, '06-tester-packaging', size);
      world.freePlay = { ...world.freePlay, phase: 'ready', bundle: { name: 'indoor-session.zip' } };
      const done = page.locator('#free-play-done');
      await expect(done).toBeVisible();
      await done.scrollIntoViewIfNeeded();
      await check(page, '07-tester-package-ready', size);
      await done.tap();
      await expect(done).toBeHidden();
      await check(page, '08-tester-step8-send', size);
      expect(popups).toBe(0);
    });

    test('every kiosk tab, the club picker and the menu fit beside the Tester button', async ({ page }) => {
      await withControlSocket(async (socket) => {
        await resetSession(socket);
        await simulateShot(socket);
      });
      const world: World = { freePlay: { ...RECORDING }, kiosk: KIOSK_STATUS };
      await kioskWorld(page, world);
      await gotoApp(page, '/?from=tester');
      await greenLight(page);
      await expect(page.getByRole('button', { name: 'Back to the tester page' })).toBeVisible();

      const tabs = ['Live', 'Club', 'Flight', 'Stats', 'Shots', 'Camera', 'Conditions', 'Setup', 'Profiles', 'Debug'];
      for (const [index, tab] of tabs.entries()) {
        // a tab's name may carry its badge (Shots 1)
        const button = page.locator('.panel-footer__tabs').getByRole('button', { name: new RegExp(`^${tab}`) });
        await button.tap();
        await expect(button).toHaveAttribute('aria-pressed', 'true');
        await check(page, `1${index}-kiosk-tab-${tab.toLowerCase()}`, size, true, true);
      }

      await page.locator('.panel-footer__tabs').getByRole('button', { name: 'Live', exact: true }).tap();
      await page.getByRole('button', { name: 'Change club' }).tap();
      const picker = page.getByRole('dialog', { name: 'Select club' });
      await expect(picker).toBeVisible();
      await expect(picker.getByRole('button', { name: /right/i }).first()).toBeVisible();
      await expect(picker.getByRole('button', { name: /left/i }).first()).toBeVisible();
      await check(page, '20-kiosk-club-picker', size, true);
      await picker.getByRole('button', { name: 'Close Select club' }).tap();

      await page.getByRole('button', { name: 'Open menu' }).tap();
      await check(page, '21-kiosk-menu', size, true);
    });

    test('the replay player fits', async ({ page }) => {
      await gotoApp(page, '/tests/e2e/fixtures/camera-replay.html');
      // the kiosk sets its theme at start (theme.ts); the bare fixture page does not
      await page.evaluate(() => (document.documentElement.dataset.theme = 'dark'));
      await expect(page.getByRole('dialog', { name: 'Shot replay' })).toBeVisible();
      await expect(page.getByRole('button', { name: 'Play', exact: true })).toBeVisible();
      await page.waitForTimeout(700); // the player fades in
      await check(page, '22-kiosk-replay-player', size, true);
    });

    test('every tester step sits clear of the sticky band', async ({ page }) => {
      const world: World = { freePlay: { ...RECORDING }, kiosk: KIOSK_STATUS };
      await useTester(page);
      await testerWorld(page, world);
      await page.goto(`${TESTER_URL}?screen=unit`);
      await expect(page.locator('#step-7')).toHaveAttribute('data-state', 'current');
      for (let step = 1; step <= 8; step += 1) {
        // the bar's "You are here" and the page's own scrolling put a step at the top
        await page.evaluate((id) => document.getElementById(id)?.scrollIntoView({ block: 'start' }), `step-${step}`);
        await page.waitForTimeout(150);
        const band = (await page.locator('#sticky-top').boundingBox())!;
        const heading = (await page.locator(`#step-${step} h2`).boundingBox())!;
        expect(heading.y, `step ${step} heading under the band`).toBeGreaterThanOrEqual(band.y + band.height - 1);
        await check(page, `3${step}-tester-step-${step}`, size);
      }
    });
  });
}
