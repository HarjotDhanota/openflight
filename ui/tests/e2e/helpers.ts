import { expect, type Page } from '@playwright/test';
import { io, type Socket } from 'socket.io-client';

const UI_URL = `http://127.0.0.1:${process.env.OPENFLIGHT_E2E_UI_PORT ?? 5173}`;
const SOCKET_URL = 'http://127.0.0.1:8080';

function connectSocket(): Promise<Socket> {
  return new Promise((resolve, reject) => {
    const socket = io(SOCKET_URL, {
      transports: ['websocket', 'polling'],
    });

    const onConnect = () => {
      socket.off('connect_error', onError);
      resolve(socket);
    };

    const onError = (error: Error) => {
      socket.off('connect', onConnect);
      socket.close();
      reject(error);
    };

    socket.once('connect', onConnect);
    socket.once('connect_error', onError);
  });
}

export async function withControlSocket<T>(run: (socket: Socket) => Promise<T>): Promise<T> {
  const socket = await connectSocket();

  try {
    return await run(socket);
  } finally {
    socket.close();
  }
}

export async function setClub(socket: Socket, club: string) {
  socket.emit('set_club', { club });
  await waitForEvent(socket, 'club_changed');
}

export async function simulateShot(socket: Socket) {
  socket.emit('simulate_shot');
  return waitForEvent(socket, 'shot');
}

export async function waitForEvent<T>(socket: Socket, event: string, timeoutMs = 5000): Promise<T> {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      socket.off(event, onEvent);
      reject(new Error(`Timed out waiting for ${event}`));
    }, timeoutMs);

    const onEvent = (payload: T) => {
      clearTimeout(timeout);
      socket.off(event, onEvent);
      resolve(payload);
    };

    socket.on(event, onEvent);
  });
}

/** Roster + shot state the mock server hands back from a `profiles` snapshot. */
interface ProfilesSnapshot {
  profiles: Array<{ id: string; name: string }>;
  active_profile_id: string;
}

/**
 * Resets the shared session between tests: clears every profile's shots, then
 * switches back to the seeded default profile and removes every other profile
 * (the backend keeps state across connections, so profiles added by one test
 * would otherwise leak into the next and collide on name). Removal is refused
 * while a profile still has session rows, so shots must be cleared first.
 */
export async function resetSession(socket: Socket) {
  const snapshotPromise = waitForEvent<ProfilesSnapshot>(socket, 'profiles');
  socket.emit('get_profiles');
  const { profiles } = await snapshotPromise;

  const defaultProfile = profiles.find((profile) => profile.name === 'Profile 1') ?? profiles[0];
  if (!defaultProfile) return;

  for (const profile of profiles) {
    const cleared = waitForEvent(socket, 'session_cleared');
    socket.emit('clear_session', { profile_id: profile.id });
    await cleared;
  }

  if (profiles.length > 1) {
    const activated = waitForEvent(socket, 'profiles');
    socket.emit('set_active_profile', { profile_id: defaultProfile.id });
    await activated;

    for (const profile of profiles) {
      if (profile.id === defaultProfile.id) continue;
      const removed = waitForEvent(socket, 'profiles');
      socket.emit('remove_profile', { profile_id: profile.id });
      await removed;
    }
  }
}

export async function gotoApp(page: Page, path = '/') {
  await page.goto(`${UI_URL}${path}`);
}

/** Production kiosk sizes the Live grid must fit. */
export const KIOSK_VIEWPORTS = [
  { width: 800, height: 400 },
  { width: 800, height: 480 },
  { width: 1024, height: 600 },
] as const;

/**
 * Widest Live value we format (wedge spin with a thousands separator).
 * Applied after a real shot so units, estimated marks, and captions stay.
 */
export const WIDEST_LIVE_METRIC_VALUE = '10,000';

export async function liveMetricValueFontSizes(page: Page): Promise<number[]> {
  await page.evaluate(() => document.fonts.ready);
  return page.locator('.live-panel__grid .metric-card__value').evaluateAll((values) =>
    values.map((value) => parseFloat(getComputedStyle(value).fontSize))
  );
}

export async function overflowingLiveMetricValues(page: Page): Promise<string[]> {
  await page.evaluate(() => document.fonts.ready);

  return page.locator('.live-panel__grid .metric-card').evaluateAll((cards) => {
    const slop = 2;
    const overflows: string[] = [];

    for (const card of cards) {
      const value = card.querySelector('.metric-card__value');
      const row = card.querySelector('.metric-card__value-row');
      if (!(value instanceof HTMLElement) || !(row instanceof HTMLElement)) {
        continue;
      }

      const cardRect = card.getBoundingClientRect();
      const rowRect = row.getBoundingClientRect();
      const label = card.querySelector('.metric-card__label');
      const meta = card.querySelector('.metric-card__meta');
      const range = document.createRange();
      range.selectNodeContents(value);
      const textRect = range.getBoundingClientRect();
      const unit = card.querySelector('.metric-card__unit');
      const unitOverlapsValue =
        unit instanceof HTMLElement &&
        (() => {
          const unitRange = document.createRange();
          unitRange.selectNodeContents(unit);
          return textRect.right > unitRange.getBoundingClientRect().left + slop;
        })();
      const unitOverflowsCard =
        unit instanceof HTMLElement &&
        (() => {
          const unitRange = document.createRange();
          unitRange.selectNodeContents(unit);
          const unitInk = unitRange.getBoundingClientRect();
          return (
            unitInk.right > cardRect.right + slop ||
            unitInk.left < cardRect.left - slop ||
            unit.scrollWidth > unit.clientWidth + slop
          );
        })();
      const valueOverflowsCard =
        textRect.right > cardRect.right + slop ||
        textRect.left < cardRect.left - slop ||
        textRect.bottom > cardRect.bottom + slop ||
        textRect.top < cardRect.top - slop;
      const rowOverflowsCard = rowRect.right > cardRect.right + slop || row.scrollWidth > row.clientWidth + slop;
      const overlapsLabel =
        label instanceof HTMLElement && rowRect.top < label.getBoundingClientRect().bottom - slop;
      const overlapsMeta =
        meta instanceof HTMLElement && rowRect.bottom > meta.getBoundingClientRect().top + slop;

      if (
        valueOverflowsCard ||
        rowOverflowsCard ||
        overlapsLabel ||
        overlapsMeta ||
        unitOverlapsValue ||
        unitOverflowsCard
      ) {
        const labelText = card.querySelector('.metric-card__label')?.textContent?.trim() ?? 'unknown';
        overflows.push(`${labelText} (${value.textContent?.trim() ?? ''})`);
      }
    }

    return overflows;
  });
}

export async function overflowingLiveMetricMetadata(page: Page): Promise<string[]> {
  await page.evaluate(() => document.fonts.ready);

  return page.locator('.live-panel__grid .metric-card').evaluateAll((cards) => {
    const slop = 2;
    const overflows: string[] = [];

    for (const card of cards) {
      const cardRect = card.getBoundingClientRect();
      const meta = card.querySelector('.metric-card__meta');
      if (!(meta instanceof HTMLElement)) {
        continue;
      }

      const overflowingText = [...meta.querySelectorAll('.metric-card__confidence-label')].find((label) => {
        if (!(label instanceof HTMLElement)) {
          return false;
        }
        const range = document.createRange();
        range.selectNodeContents(label);
        const textRects = [...range.getClientRects()];
        return (
          label.scrollWidth > label.clientWidth + slop ||
          textRects.some(
            (rect) =>
              rect.left < cardRect.left - slop ||
              rect.right > cardRect.right + slop ||
              rect.top < cardRect.top - slop ||
              rect.bottom > cardRect.bottom + slop
          )
        );
      });

      if (overflowingText) {
        const labelText = card.querySelector('.metric-card__label')?.textContent?.trim() ?? 'unknown';
        overflows.push(`${labelText} (${overflowingText.textContent?.trim() ?? ''})`);
      }
    }

    return overflows;
  });
}

type Px = [number, number];

/**
 * A setup made on a 1280x800 picture, off centre so a mirrored drawing shows as one. The
 * confirmed outline is not the box_px rectangle around it, so drawing the rectangle shows.
 */
export const SET_UP = {
  image: [1280, 800] as Px,
  box: [460, 560, 980, 720] as const,
  outline: [
    [500, 710],
    [940, 710],
    [900, 575],
    [540, 575],
  ] as Px[],
  ball: [720, 610] as Px,
  aim: [900, 300] as Px,
};

// with a left/right difference (the pole), so a mirrored picture shows as one
const SET_UP_PICTURE = `<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="800">
  <rect width="1280" height="800" fill="#1d2420"/>
  <rect width="1280" height="255" fill="#3b4148"/>
  <rect x="120" y="60" width="18" height="420" fill="#c96"/>
  <rect x="890" y="270" width="24" height="40" fill="#e8e05a"/>
  <polygon points="510,560 930,560 980,720 460,720" fill="#2f6b3a"/>
  <circle cx="720" cy="610" r="11" fill="#f4f4f0"/>
</svg>`;

const notSet = { state: 'not_set', reason: 'not set' };

/**
 * A finished setup, as a kiosk that applied it reports it: box confirmed, aim set, ball range
 * measured, the labelled default net. `changes` replaces sections, `unapplied` or `live`.
 */
export function setUpSnapshot(changes: Record<string, unknown> = {}) {
  return {
    schema: 'openflight.setup.v1',
    revision: 3,
    box: {
      state: 'confirmed',
      id: 'box@720,610#1',
      box_px: [...SET_UP.box],
      outline_px: SET_UP.outline,
      ball_px: SET_UP.ball,
      diameter_px: 22,
      image_size_px: SET_UP.image,
    },
    aim: { state: 'set', aim_px: SET_UP.aim, yaw_deg: 4.1, uncertainty_deg: 0.84 },
    ball_range: { state: 'set', range_m: 2.31, uncertainty_m: 0.12, source: 'camera_prior' },
    net_range: { state: 'default', value_m: 4.6, source: 'default_not_measured', reason: 'no floor line marked' },
    scene: notSet,
    live: { camera_available: true },
    unapplied: {},
    ...changes,
  };
}

/** Nothing set up: what a fresh kiosk with a camera reports. */
export function notSetUpSnapshot() {
  return setUpSnapshot({ box: notSet, aim: notSet, ball_range: notSet, net_range: notSet });
}

/** The target line a kiosk reports for `snapshot`: set only when its aim was applied. */
function targetLineFor(snapshot: ReturnType<typeof setUpSnapshot>) {
  const aim = snapshot.aim as { state: string };
  const applied = aim.state === 'set' && !('aim' in snapshot.unapplied);
  return applied ? { status: 'set', yaw_deg: 4.1, total_uncertainty_deg: 0.84 } : { status: 'not_set' };
}

/**
 * The kiosk's setup API, its target line and its camera preview, all from one setup record,
 * so the views never show a state the kiosk could not be in. `use` swaps the record.
 */
export async function mockSetupAndPreview(page: Page, snapshot: ReturnType<typeof setUpSnapshot>) {
  let current = snapshot;
  await page.route(/\/api\/setup$/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(current) })
  );
  await page.route(/\/api\/target-line$/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(targetLineFor(current)) })
  );
  await page.route('**/api/camera/preview.jpg*', (route) =>
    route.fulfill({ status: 200, contentType: 'image/svg+xml', body: SET_UP_PICTURE })
  );
  return {
    use(next: ReturnType<typeof setUpSnapshot>) {
      current = next;
    },
  };
}

/** Set the golfer's hand after the kiosk's own arrives on connect. */
export async function setHanded(page: Page, hand: 'RH' | 'LH') {
  await expect(page.locator('.panel-header__club')).toContainText('Right-handed');
  await page.evaluate(async (handed) => {
    const { useSystemStore } = await import('/src/stores/useSystemStore.ts');
    useSystemStore.getState().setHanded(handed);
  }, hand);
}

/** Where a camera pixel of SET_UP is drawn in `selector`: whole and centred, mirrored for LH. */
export async function drawnAt(page: Page, selector: string, px: Px, hand: 'RH' | 'LH'): Promise<Px> {
  const box = (await page.locator(selector).boundingBox())!;
  const [width, height] = SET_UP.image;
  const scale = Math.min(box.width / width, box.height / height);
  const x = hand === 'LH' ? width - px[0] : px[0];
  return [
    box.x + (box.width - width * scale) / 2 + x * scale,
    box.y + (box.height - height * scale) / 2 + px[1] * scale,
  ];
}

export { expect };
