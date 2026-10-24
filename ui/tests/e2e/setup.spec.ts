import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { test, type Page, type Route } from '@playwright/test';
import { expect, gotoApp } from './helpers';

// The kiosk Setup screen against a mocked setup API: the mock backend has no camera, so it
// can never find a ball. Saved pixels are camera pixels whichever way the view is drawn.
const OUT = process.env.UX_SHOTS_DIR;
const SIZES = [
  { width: 1024, height: 600 },
  { width: 800, height: 480 },
] as const;
const HANDS = ['RH', 'LH'] as const;
type Hand = (typeof HANDS)[number];
type Px = [number, number];

const IMAGE: Px = [1280, 800];
const BALL: Px = [640, 610];
const AIM: Px = [900, 300];
const FLOOR: [Px, Px] = [
  [200, 260],
  [1100, 250],
];

// A picture with a left/right difference (the pole) so a mirrored view shows as one.
const VIEW_SVG = `<svg xmlns="http://www.w3.org/2000/svg" width="${IMAGE[0]}" height="${IMAGE[1]}">
  <rect width="1280" height="800" fill="#1d2420"/>
  <rect y="0" width="1280" height="255" fill="#3b4148"/>
  <path d="M0 255 L1280 255" stroke="#cfd5da" stroke-width="3"/>
  <rect x="120" y="60" width="18" height="420" fill="#c96"/>
  <polygon points="430,560 850,560 900,720 380,720" fill="#2f6b3a"/>
  <circle cx="${BALL[0]}" cy="${BALL[1]}" r="11" fill="#f4f4f0"/>
</svg>`;

const notSet = { state: 'not_set', reason: 'no setup file' };

class MockSetup {
  snapshot: Record<string, unknown> = {
    schema: 'openflight.setup.v1',
    revision: 0,
    box: notSet,
    aim: notSet,
    ball_range: notSet,
    net_range: notSet,
    scene: notSet,
    live: { camera_available: true },
    unapplied: {},
  };
  posts: Array<{ section: string; body: Record<string, unknown> }> = [];
  /** What the next look at the box sees; a test moves the ball by changing it. */
  ball: 'found' | 'outside_box' | 'no_ball' = 'found';
  /** Why the service would keep the default net for a marked floor line, if it would. */
  floorRejected: string | null = null;

  private set(changes: Record<string, unknown>) {
    this.snapshot = { ...this.snapshot, ...changes, revision: (this.snapshot.revision as number) + 1 };
  }

  private live(changes: Record<string, unknown>) {
    this.snapshot = { ...this.snapshot, live: { ...(this.snapshot.live as object), ...changes } };
  }

  answer(section: string, body: Record<string, unknown>): { ok: boolean; reason: string | null } {
    this.posts.push({ section, body });
    const action = body.action;
    if (section === 'box') {
      const state = this.ball;
      this.live({
        box: {
          state,
          message: '',
          ball_px: state === 'found' ? BALL : state === 'outside_box' ? [300, 640] : null,
          diameter_px: state === 'no_ball' ? null : 22,
          outline_px: [
            [430, 560],
            [850, 560],
            [900, 720],
            [380, 720],
          ],
        },
      });
      if (action === 'confirm') {
        if (state !== 'found') return { ok: false, reason: state };
        this.set({
          box: {
            state: 'confirmed',
            id: 'box@640,610#1',
            box_px: [380, 560, 900, 720],
            ball_px: BALL,
            diameter_px: 22,
            image_size_px: IMAGE,
          },
        });
      }
    } else if (section === 'aim') {
      if (action === 'show') this.live({ aim: { default_aim_px: [640, 255] } });
      if (action === 'confirm') {
        const floor = body.floor_line_px ?? null;
        this.set({
          aim: {
            state: 'set',
            aim_px: body.aim_px,
            floor_line: floor,
            yaw_deg: 4.1,
            uncertainty_deg: 0.84,
            floor_line_used: floor !== null && this.floorRejected === null,
            warnings: [],
          },
          net_range:
            floor && this.floorRejected === null
              ? { state: 'set', value_m: 3.917, uncertainty_m: 0.204, source: 'camera_floor_line' }
              : {
                  state: 'default',
                  value_m: 4.6,
                  source: 'default_not_measured',
                  reason: floor ? this.floorRejected : 'no floor line marked',
                },
        });
      }
      if (action === 'skip') {
        this.set({
          aim: { state: 'skipped', floor_line: null, source: 'skipped', reason: 'the tester skipped the aim point' },
          net_range: { state: 'default', value_m: 4.6, source: 'default_not_measured', reason: 'aim point skipped' },
        });
      }
    } else if (section === 'range' && action === 'measure') {
      this.set({
        ball_range: {
          state: 'set',
          range_m: 2.312,
          uncertainty_m: 0.118,
          side_offset_m: 0.02,
          source: 'camera_prior',
          ball_px: BALL,
          diameter_px: 22,
        },
      });
    }
    return { ok: true, reason: null };
  }
}

async function json(route: Route, payload: object, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(payload) });
}

async function mockSetupApi(page: Page): Promise<MockSetup> {
  const mock = new MockSetup();
  await page.route('**/api/setup/view.jpg*', (route) =>
    route.fulfill({ status: 200, contentType: 'image/svg+xml', body: VIEW_SVG })
  );
  await page.route(/\/api\/setup(\/(box|aim|range))?$/, async (route) => {
    const request = route.request();
    if (request.method() === 'GET') return json(route, mock.snapshot);
    const section = new URL(request.url()).pathname.split('/').pop()!;
    const result = mock.answer(section, request.postDataJSON());
    return json(route, { ...result, setup: mock.snapshot }, result.ok ? 200 : 409);
  });
  return mock;
}

async function open(page: Page, hand: Hand): Promise<MockSetup> {
  const mock = await mockSetupApi(page);
  await gotoApp(page);
  await page.getByRole('button', { name: 'Close Select club' }).click();
  // the kiosk's own hand arrives on connect; set the golfer's after it
  await expect(page.locator('.panel-header__club')).toContainText('Right-handed');
  await page.evaluate(async (handed) => {
    const { useSystemStore } = await import('/src/stores/useSystemStore.ts');
    useSystemStore.getState().setHanded(handed);
  }, hand);
  return mock;
}

/** Where a camera pixel is drawn: the picture fits the view, mirrored for a left-hander. */
async function screenPoint(page: Page, px: Px, hand: Hand): Promise<Px> {
  const box = (await page.locator('.setup-view__stage').boundingBox())!;
  const scale = Math.min(box.width / IMAGE[0], box.height / IMAGE[1]);
  const left = (box.width - IMAGE[0] * scale) / 2;
  const top = (box.height - IMAGE[1] * scale) / 2;
  const x = hand === 'LH' ? IMAGE[0] - px[0] : px[0];
  return [box.x + left + x * scale, box.y + top + px[1] * scale];
}

async function tap(page: Page, px: Px, hand: Hand) {
  const [x, y] = await screenPoint(page, px, hand);
  await page.mouse.click(x, y);
}

async function expectDrawnAt(page: Page, selector: string, px: Px, hand: Hand) {
  const [x, y] = await screenPoint(page, px, hand);
  const box = (await page.locator(selector).boundingBox())!;
  expect(Math.abs(box.x + box.width / 2 - x)).toBeLessThan(2);
  expect(Math.abs(box.y + box.height / 2 - y)).toBeLessThan(2);
}

function expectNear(actual: unknown, expected: Px, tolerance: number) {
  const [x, y] = actual as Px;
  expect(Math.abs(x - expected[0])).toBeLessThan(tolerance);
  expect(Math.abs(y - expected[1])).toBeLessThan(tolerance);
}

/** In every ready-check row the label, badge and words sit side by side, never on each other. */
async function expectRowsApart(page: Page) {
  const rows = page.locator('.setup-view__check');
  await expect(rows).toHaveCount(5);
  for (const row of await rows.all()) {
    const parts = [
      (await row.locator('.setup-view__check-label').boundingBox())!,
      (await row.locator('.setup-view__badge').boundingBox())!,
    ];
    const words = await row.locator('.setup-view__check-words').boundingBox();
    if (words && words.width > 0) parts.push(words);
    for (let index = 1; index < parts.length; index += 1) {
      const sameLine = parts[index].y < parts[index - 1].y + parts[index - 1].height;
      if (sameLine) expect(parts[index].x).toBeGreaterThanOrEqual(parts[index - 1].x + parts[index - 1].width + 4);
    }
    const label = parts[0];
    // a label never wraps onto a second line
    expect(label.height).toBeLessThan(24);
  }
}

async function shot(page: Page, name: string) {
  if (!OUT) return;
  mkdirSync(OUT, { recursive: true });
  await page.screenshot({ path: join(OUT, `${name}.png`) });
}

test.describe('a kiosk without a camera', () => {
  test.use({ viewport: { width: 800, height: 480 }, hasTouch: true });

  test('offers no setup, and the Setup tab says it needs the camera', async ({ page }) => {
    const mock = await open(page, 'RH');
    mock.snapshot = { ...mock.snapshot, live: { camera_available: false } };
    // the first read may have happened before the change: the kiosk comes back
    await page.reload();
    await page.getByRole('button', { name: 'Close Select club' }).click();
    await expect(page.locator('.panel-header__club')).toContainText('Right-handed');
    await expect(page.getByRole('button', { name: 'Finish setup' })).toHaveCount(0);
    await expect(page.locator('.nav__setup-dot')).toHaveCount(0);
    await page.getByRole('button', { name: 'Setup', exact: true }).click();
    await expect(page.locator('.setup-view')).toHaveText(/Setup needs the camera, and this kiosk has none\./);
    await expect(page.locator('.setup-view__steps')).toHaveCount(0);
    await page.waitForTimeout(2500);
    expect(mock.posts).toEqual([]);
  });
});

for (const size of SIZES) {
  for (const hand of HANDS) {
    test.describe(`${size.width}x${size.height} ${hand}`, () => {
      test.use({ viewport: size, hasTouch: true });
      const tag = `${size.width}x${size.height}-${hand}`;

      test('walks the four steps; the saved aim is in camera pixels', async ({ page }) => {
        const mock = await open(page, hand);
        mock.ball = 'no_ball';
        await expect(page.locator('.nav__setup-dot')).toHaveCount(1);
        await shot(page, `${tag}-0-offer`);
        await page.getByRole('button', { name: 'Finish setup' }).click();
        await expect(page.locator('.setup-view')).toBeVisible();

        // 1. ball in the box: not seen, then beside the box, then in it
        await expect(page.locator('.setup-view__step--active')).toContainText('Ball in the box');
        await expect(page.locator('.setup-view__look')).toHaveText('No ball seen. Put a ball in the box.');
        await expect(page.getByRole('button', { name: 'Confirm', exact: true })).toBeDisabled();
        await expect(page.locator('.setup-view__saved')).toHaveText('Not confirmed');
        await shot(page, `${tag}-1-box-not-seen`);
        mock.ball = 'outside_box';
        await expect(page.locator('.setup-view__look')).toHaveText('The ball is outside the box. Move it in.', {
          timeout: 6000,
        });
        await shot(page, `${tag}-1-box-outside`);
        mock.ball = 'found';
        await expect(page.locator('.setup-view__look')).toHaveText('Ball found in the box', { timeout: 6000 });
        await expect(page.locator('.setup-view__box-outline')).toBeVisible();
        await expectDrawnAt(page, '.setup-view__ball', BALL, hand);
        await shot(page, `${tag}-1-box`);
        await page.getByRole('button', { name: 'Confirm', exact: true }).click();
        await expect(page.locator('.setup-view__saved')).toContainText('Confirmed');
        await page.getByRole('button', { name: 'Next', exact: true }).click();

        // 2. aim: a tap, no floor line
        await expect(page.locator('.setup-view__step--active')).toContainText('Aim');
        await tap(page, AIM, hand);
        await expectDrawnAt(page, '.setup-view__aim-point', AIM, hand);
        await expect(page.locator('.setup-view__aim-line')).toBeVisible();
        await page.getByRole('button', { name: 'Confirm', exact: true }).click();
        await expect(page.locator('.setup-view__saved')).toContainText('± 0.8°');
        const aimPost = mock.posts.find((post) => post.section === 'aim' && post.body.action === 'confirm')!;
        expectNear(aimPost.body.aim_px, AIM, 4);
        expect(aimPost.body.floor_line_px ?? null).toBeNull();
        await expect(page.locator('.setup-view__aim-line--saved')).toBeVisible();
        await shot(page, `${tag}-2-aim`);
        await page.getByRole('button', { name: 'Next', exact: true }).click();

        // 3. ball range, measured on arrival
        await expect(page.locator('.setup-view__step--active')).toContainText('Ball range');
        await expect(page.locator('.setup-view__range')).toHaveText(
          '2.31 m ± 0.12, from the camera; the radar refines it each shot'
        );
        expect(mock.posts.some((post) => post.section === 'range' && post.body.action === 'measure')).toBe(true);
        await shot(page, `${tag}-3-range`);
        await page.getByRole('button', { name: 'Next', exact: true }).click();

        // 4. ready check
        await expect(page.locator('.setup-view__step--active')).toContainText('Ready check');
        await expect(page.locator('[data-section="box"]')).toContainText('Confirmed');
        await expect(page.locator('[data-section="aim"]')).toContainText('± 0.8°');
        await expect(page.locator('[data-section="ball_range"]')).toContainText('from the camera');
        await expect(page.locator('[data-section="net_range"]')).toContainText('4.6 m (not measured)');
        await expectRowsApart(page);
        await shot(page, `${tag}-4-ready`);
        await page.getByRole('button', { name: 'Done', exact: true }).click();
        await expect(page.locator('.setup-view')).toHaveCount(0);
        await expect(page.getByRole('button', { name: 'Finish setup' })).toHaveCount(0);
        await expect(page.locator('.nav__setup-dot')).toHaveCount(0);
      });

      test('a floor line is saved in camera pixels and gives a measured net', async ({ page }) => {
        const mock = await open(page, hand);
        await page.getByRole('button', { name: 'Setup', exact: true }).click();
        await expect(page.locator('.setup-view__look')).toContainText('Ball found in the box', { timeout: 6000 });
        await page.getByRole('button', { name: 'Confirm', exact: true }).click();
        await page.getByRole('button', { name: 'Next', exact: true }).click();

        await tap(page, AIM, hand);
        await page.getByRole('button', { name: 'Floor line', exact: true }).click();
        await tap(page, FLOOR[0], hand);
        await tap(page, FLOOR[1], hand);
        await expect(page.locator('.setup-view__floor-line')).toBeVisible();
        await shot(page, `${tag}-2-aim-floor-line`);
        await page.getByRole('button', { name: 'Confirm', exact: true }).click();
        await expect(page.locator('.setup-view__saved')).toContainText('3.92 m ± 0.20, from the floor line');
        const post = mock.posts.find((item) => item.section === 'aim' && item.body.action === 'confirm')!;
        expectNear(post.body.aim_px, AIM, 4);
        const [first, second] = post.body.floor_line_px as [Px, Px];
        expectNear(first, FLOOR[0], 4);
        expectNear(second, FLOOR[1], 4);
      });

      test('a skipped aim shows the net as the default, not measured', async ({ page }) => {
        await open(page, hand);
        await page.getByRole('button', { name: 'Setup', exact: true }).click();
        await expect(page.locator('.setup-view__look')).toContainText('Ball found in the box', { timeout: 6000 });
        await page.getByRole('button', { name: 'Confirm', exact: true }).click();
        await page.getByRole('button', { name: 'Next', exact: true }).click();
        await page.getByRole('button', { name: 'Skip', exact: true }).click();
        await expect(page.locator('.setup-view__saved')).toContainText('Skipped');
        await page.getByRole('button', { name: 'Ready check' }).click();
        await expect(page.locator('[data-section="aim"]')).toContainText('Skipped');
        await expect(page.locator('[data-section="net_range"]')).toContainText('4.6 m (not measured)');
        await expect(page.locator('[data-section="ball_range"]')).toContainText('Not set');
      });

      test('a rejected floor line says why and is drawn as not used', async ({ page }) => {
        const mock = await open(page, hand);
        mock.floorRejected = 'above the horizon';
        await page.getByRole('button', { name: 'Setup', exact: true }).click();
        await expect(page.locator('.setup-view__look')).toContainText('Ball found in the box', { timeout: 6000 });
        await page.getByRole('button', { name: 'Confirm', exact: true }).click();
        await page.getByRole('button', { name: 'Next', exact: true }).click();
        await tap(page, AIM, hand);
        await page.getByRole('button', { name: 'Floor line', exact: true }).click();
        await tap(page, FLOOR[0], hand);
        await tap(page, FLOOR[1], hand);
        await expect(page.locator('.setup-view__floor-line--rejected')).toHaveCount(0);
        await page.getByRole('button', { name: 'Confirm', exact: true }).click();
        await expect(page.locator('.setup-view__saved')).toContainText(
          '4.6 m (not measured): the floor line is above the horizon'
        );
        await expect(page.locator('.setup-view__saved')).toContainText("The floor line wasn't used for the aim.");
        await expect(page.locator('.setup-view__floor-line--rejected')).toBeVisible();
      });
    });
  }
}
