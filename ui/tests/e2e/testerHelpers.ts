import type { Page, Route } from '@playwright/test';

// The tester page's mocks and helpers; the kiosk's own are in ./helpers.

/** The camera model the page drags the patch through (P8-1): the v3 rig, level. */
export const PATCH_PROJECTION = {
  focal_px: 933.3334,
  image_size_px: [1280, 800],
  principal_point_px: [640, 400],
  pitch_deg: 0,
  roll_correction_deg: 0,
  horizontal_pixel_sign: 1,
  camera_origin_lfu_m: [0, 0, 0.095],
  radar_origin_lfu_m: [0, -0.0014, 0.0588],
  ball_center_height_m: 0.021335,
  size_m: 0.61,
  centre_distance_m: [0.8, 3.0],
  approximate: false,
};

/** The fixed ball box's outline on the 1280×800 preview (1a, 13 Oct). */
export const BOX_OUTLINE_PX = [
  [560, 430],
  [720, 430],
  [740, 520],
  [540, 520],
];

/** The ball the camera found in the box, as the tester server reports it. */
export function foundBall(overrides: object = {}) {
  return {
    state: 'found',
    center_px: [640, 478],
    diameter_px: 22,
    stable_s: 1.2,
    range_m: 2.31,
    range_status: 'validated',
    message: 'Ball found in the box.',
    ...overrides,
  };
}

/** Step 3's ball box as the tester server reports it (1a: a FIXED box, 13 Oct contract). */
export function placementBoxState(testerId: string, state = 'confirmed', overrides: object = {}) {
  return {
    tester_id: testerId,
    state,
    mode: 'fixed',
    previewing: false,
    setup_blockers: [],
    projection: PATCH_PROJECTION,
    box_outline_px: BOX_OUTLINE_PX,
    frame_size_px: [1280, 800],
    placement_window_m: [1.8, 2.9],
    ball: foundBall(),
    net_line: { row_px: 300, range_m: 4.2, status: 'set', message: 'Net line set: about 4.2 m beyond the ball.' },
    distance_note: 'inside the 1.8–2.9 m window',
    box: { arm_id: 'arm5', frame_size_px: [1280, 800], outline_px: BOX_OUTLINE_PX, distance_m: 2.31 },
    confirmed_at_utc: state === 'confirmed' ? '2026-09-30T18:00:00+00:00' : null,
    ...overrides,
  };
}

/**
 * The box already confirmed for whichever tester the page asks about, so the steps
 * after it (hardware, light, ball range, ladder) are open, as they are once a
 * tester has placed it.
 */
export async function mockConfirmedPlacementBox(page: Page) {
  await page.route('**/api/tester/placement-box**', (route) => {
    const testerId = new URL(route.request().url()).searchParams.get('tester_id') ?? DEFAULT_TESTER;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(placementBoxState(testerId)),
    });
  });
}

/** The tester ID the page starts with: typed once, then kept for that browser tab. */
export const DEFAULT_TESTER = '20260922-name';

/**
 * Starts the tester page with a tester already entered, as a reload of the same tab
 * does (the ID is kept in the tab's sessionStorage, never across visits).
 */
export async function useTester(page: Page, testerId = DEFAULT_TESTER) {
  await page.addInitScript((id) => {
    try {
      if (!sessionStorage.getItem('openflight-tester-id')) sessionStorage.setItem('openflight-tester-id', id);
    } catch {
      // a page without storage asks for its tester ID
    }
  }, testerId);
}

/** Enters a tester ID in step 1: opens the entry box if needed, types the ID and uses it. */
export async function pickTester(page: Page, testerId: string) {
  const change = page.locator('#tester-change');
  if (await change.isVisible()) await change.click();
  await page.locator('#tester-entry').fill(testerId);
  await page.locator('#tester-use').click();
}

/** The three lenses the backend offers (13 Oct contract), the stock one fitted. */
export function lensState(testerId = DEFAULT_TESTER, lensId: string | null = 'stock-2.8mm') {
  const settings = (ladder5: number[], ladder6: number[]) => ({
    modes: ['arm5', 'arm6'],
    default_arm: 'arm5',
    ladder: { arm5: ladder5, arm6: ladder6 },
  });
  return {
    tester_id: testerId,
    lens_id: lensId,
    lenses: [
      {
        lens_id: 'stock-2.8mm',
        label: 'Stock 2.8 mm',
        focal_mm: 2.8,
        f_number: 2.2,
        // the listed spec: its strong barrel distortion, not the pinhole's 2·atan(1.92/2.8)
        hfov_deg: 118,
        intrinsics: 'nominal',
        // OV9281, 3 µm pixels: f_mm / 0.003 px at 1280×800, half that at 640×400
        focal_px_by_arm: { arm5: 933.33, arm6: 466.67 },
        settings: settings([300, 200, 150, 100, 75, 50, 30, 20, 10], [300, 150, 75, 30, 15]),
      },
      {
        lens_id: '6mm',
        label: '6 mm',
        focal_mm: 6,
        f_number: 2.0,
        // pinhole 2·atan(1.92 mm / 6 mm)
        hfov_deg: 35.5,
        intrinsics: 'nominal',
        focal_px_by_arm: { arm5: 2000, arm6: 1000 },
        settings: settings([300, 150, 75], [300, 150]),
      },
      {
        lens_id: '8mm',
        label: '8 mm',
        focal_mm: 8,
        f_number: null,
        // pinhole 2·atan(1.92 mm / 8 mm)
        hfov_deg: 27,
        intrinsics: 'nominal',
        focal_px_by_arm: { arm5: 2666.67, arm6: 1333.33 },
        settings: settings([300, 150, 75], [300]),
      },
    ],
    allowed_ladder_us: { arm5: [300, 200, 150, 100, 75, 50, 30, 20, 10], arm6: [300, 150, 75, 30, 15] },
    setup_reset_on_change: true,
  };
}

/** GET /api/tester/lens for any tester, with the stock lens fitted. */
export async function mockLens(page: Page, lensId: string | null = 'stock-2.8mm') {
  await page.route('**/api/tester/lens?**', (route) => {
    const testerId = new URL(route.request().url()).searchParams.get('tester_id') ?? DEFAULT_TESTER;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(lensState(testerId, lensId)),
    });
  });
}

/** Where an element is drawn now, measured in one step (a live picture is replaced on every frame). */
export async function rectOf(page: Page, selector: string) {
  return page.evaluate((query) => {
    const node = document.querySelector(query);
    if (!node) return null;
    const rect = node.getBoundingClientRect();
    return rect.width && rect.height ? { x: rect.left, y: rect.top, width: rect.width, height: rect.height } : null;
  }, selector);
}

/**
 * Opens the tester page's "Advanced: manual exposure ladder (maintainer)" section on
 * every load, as a maintainer working the manual ladder does. The section holds the
 * manual ladder, its verdict card and the capture-rate tally, and is closed by default
 * since free play runs the exposure study itself.
 */
export async function keepManualLadderOpen(page: Page) {
  await page.addInitScript(() => {
    document.addEventListener('DOMContentLoaded', () => {
      const section = document.getElementById('advanced-ladder') as HTMLDetailsElement | null;
      if (section) section.open = true;
    });
  });
}

export async function fulfillJson(route: Route, payload: object, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(payload) });
}

export const RECORDING = { enabled: true, tester_id: DEFAULT_TESTER, run_id: 'run-1', phase: 'recording' };

/** Every setup step done (setup confirmed, box, aim skipped, light, range): step 7 is open. */
export async function mockSetupDone(page: Page, world: { freePlay: object; aim?: object }) {
  await mockConfirmedPlacementBox(page);
  await page.route('**/api/tester/lens?**', (route) => fulfillJson(route, lensState()));
  await page.route('**/api/tester/tee-range?**', (route) =>
    fulfillJson(route, {
      state: {
        epoch_id: 'fixture-range',
        phase: 'raw_only',
        reason: 'qualification_artifact_missing',
        evidence: {},
        solution: { status: 'unresolved', selected_range_m: null },
      },
    })
  );
  await page.route('**/api/tester/setup-eligibility?**', (route) =>
    fulfillJson(route, {
      schema_version: 1,
      tester_id: new URL(route.request().url()).searchParams.get('tester_id'),
      config_hash: 'measured-v3-hash',
      eligible: true,
      stage: 'tester_admission',
      checks: [],
      blockers: [],
      operator_confirmation: {
        confirmed: true,
        confirmed_at: '2026-09-24T00:00:00Z',
        config_hash: 'measured-v3-hash',
        authority: 'operator_physical_setup',
      },
      runtime_requirements: ['ops', 'camera', 'iwr6843', 'lis3dh'],
    })
  );
  await page.route('**/api/tester/aim-point**', (route) =>
    fulfillJson(route, world.aim ?? { tester_id: DEFAULT_TESTER, state: 'skipped', runs: {} })
  );
  await page.route('**/api/camera/preview.jpg**', (route) => route.fulfill({ status: 204 }));
  await page.route('**/api/tester/attempts?**', (route) => fulfillJson(route, { schema_version: 1, scopes: [] }));
  await page.route('**/api/tester/ladder?**', (route) => fulfillJson(route, { ladder: { rungs: {} }, stopped: true }));
  await page.route('**/api/tester/status**', (route) => fulfillJson(route, { free_play: world.freePlay }));
}
