// @vitest-environment jsdom
/**
 * The Club and Flight tabs follow the club picked, through the real App: they show the
 * latest shot hit with that club, or that club's model unmeasured, and never one club's
 * numbers under another's name. The picker's hand choice is one tap and needs no restart.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { socketService } from './services/socketService';
import { useProfileStore } from './stores/useProfileStore';
import { useShotStore } from './stores/useShotStore';
import { useSystemStore } from './stores/useSystemStore';
import { makeShot, stubLayoutGlobals } from './test/liveTraceFixtures';
import type { Shot, ShotMetric } from './types/shot';

vi.mock('./hooks/useSocket', () => ({ useSocket: () => ({ shutdown: vi.fn() }) }));
vi.mock('./services/socketService', () => {
  const others = new Map<PropertyKey, unknown>();
  const socketService = new Proxy(
    {},
    {
      get: (_target, key) => {
        if (key === 'onSessionCleared') return () => () => {};
        if (!others.has(key)) others.set(key, vi.fn());
        return others.get(key);
      },
    }
  );
  return { socketService };
});

// Node 25 ships its own (method-less without --localstorage-file) localStorage global,
// which shadows jsdom's; the stores read it at import, so give them a working one first.
vi.hoisted(() => {
  const store = new Map<string, string>();
  const storage = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, String(value)),
    removeItem: (key: string) => void store.delete(key),
    clear: () => store.clear(),
    key: (index: number) => [...store.keys()][index] ?? null,
    get length() {
      return store.size;
    },
  };
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: storage });
});

let container: HTMLDivElement;
let root: Root;
let stamp = 0;

function degrees(value: number): ShotMetric {
  return {
    value,
    unit: 'deg',
    status: 'calculated',
    source: 'test',
    range: null,
    reason: null,
    frame: null,
    note: null,
    normalized: null,
  };
}

/** A shot hit with ``club``, its face angle the number that tells it apart. */
function hit(club: string, face: number, overrides: Partial<Shot> = {}): Shot {
  stamp += 1;
  return makeShot({
    club,
    timestamp: `2026-10-24T10:00:${String(stamp).padStart(2, '0')}Z`,
    metrics: { face_angle: degrees(face) },
    ...overrides,
  });
}

function session(shots: Shot[]): void {
  act(() => useShotStore.setState({ shots, latestShot: shots[shots.length - 1] ?? null }));
}

function button(text: string, scope: ParentNode = container): HTMLButtonElement {
  const found = [...scope.querySelectorAll<HTMLButtonElement>('button')].find(
    (candidate) => candidate.textContent?.trim() === text
  );
  expect(found, text).toBeDefined();
  return found!;
}

function tap(text: string, scope?: ParentNode): void {
  act(() => button(text, scope).click());
}

function pickClub(group: string, tile: string): void {
  const picker = container.querySelector('.picker-overlay');
  if (!picker) tap('Change club');
  const overlay = container.querySelector('.picker-overlay')!;
  tap(group, overlay);
  tap(tile, overlay);
  expect(container.querySelector('.picker-overlay')).toBeNull();
}

function openTab(name: string): void {
  tap(name, container.querySelector('.panel-footer')!);
}

const headerClub = () => container.querySelector('.panel-header__club')?.textContent;
const faces = () => [...container.querySelectorAll('[data-face]')].map((node) => node.getAttribute('data-face'));
const faceAngle = () =>
  container.querySelector('[data-club-view="top"] [data-view-metric="face_angle"] dd span')?.textContent;

beforeAll(stubLayoutGlobals);
afterAll(() => vi.unstubAllGlobals());

beforeEach(() => {
  vi.mocked(socketService.setHandedness).mockReset();
  vi.mocked(socketService.setClub).mockReset();
  useShotStore.setState({ latestShot: null, shots: [], isNewShot: false, shotProcessingPhase: null, shotVersion: 0 });
  useProfileStore.setState({
    profiles: [{ id: 'james', name: 'James', created_at: '', settings: {} }],
    activeProfileId: 'james',
    loaded: true,
  });
  useSystemStore.setState({ serverClub: null, handed: 'RH', handedSource: 'default' });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root.render(<App />));
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('App: the Club tab follows the club picked', () => {
  it('switching club changes the header and the model', () => {
    session([hit('7-iron', 4.4)]);
    pickClub('Irons', '7i');
    openTab('Club');
    expect(headerClub()).toBe('7 Iron · Right-handed');
    expect(new Set(faces())).toEqual(new Set(['iron']));
    expect(faceAngle()).toContain('4.4');

    pickClub('Woods', 'DR');

    expect(headerClub()).toBe('Driver · Right-handed');
    expect(new Set(faces())).toEqual(new Set(['driver']));
  });

  it('with no shot hit with the new club, draws its model unmeasured and asks for one', () => {
    session([hit('7-iron', 4.4)]);
    pickClub('Woods', 'DR');
    openTab('Club');

    expect(headerClub()).toBe('Driver · Right-handed');
    expect(container.textContent).toContain('Hit a shot with your Driver to see its delivery');
    expect(new Set(faces())).toEqual(new Set(['driver']));
    // never the 7-iron's numbers under the driver's name
    expect(container.textContent).not.toContain('4.4');
    expect(faceAngle()).toBe('—');
  });

  it('uses the latest shot hit with the selected club, not the latest shot', () => {
    session([hit('7-iron', 2.2), hit('7-iron', 4.4), hit('driver', -1.5)]);
    pickClub('Irons', '7i');
    openTab('Club');

    expect(headerClub()).toBe('7 Iron · Right-handed');
    expect(faceAngle()).toContain('4.4');
    expect(container.textContent).not.toContain('-1.5');
  });

  it("never counts another profile's shot with that club", () => {
    session([hit('7-iron', 4.4, { profile_id: 'someone-else' })]);
    pickClub('Irons', '7i');
    openTab('Club');

    expect(container.textContent).toContain('Hit a shot with your 7 Iron to see its delivery');
    expect(faceAngle()).toBe('—');
  });

  it('labels a shot with the hand it was hit with, not the hand now', () => {
    session([hit('7-iron', 4.4, { handed: 'LH' })]);
    pickClub('Irons', '7i');
    openTab('Club');

    expect(headerClub()).toBe('7 Iron · Left-handed');
  });

  it('the Flight tab follows the club picked too', () => {
    session([hit('7-iron', 4.4)]);
    pickClub('Woods', 'DR');
    openTab('Flight');

    expect(headerClub()).toBe('Driver · Right-handed');
    expect(container.textContent).toContain('Hit a shot with your Driver to see its flight');

    pickClub('Irons', '7i');
    expect(headerClub()).toBe('7 Iron · Right-handed');
    expect(container.querySelector('[data-metric-panel="flight"]')).not.toBeNull();
  });
});

describe('App: the hand is one tap in the club picker', () => {
  it('a tap on Left sends it, shows it at once and keeps the picker open', () => {
    const picker = container.querySelector('.picker-overlay')!;
    tap('Left-handed', picker);

    expect(socketService.setHandedness).toHaveBeenCalledWith('LH');
    expect(useSystemStore.getState().handed).toBe('LH');
    expect(container.querySelector('.picker-overlay')).not.toBeNull();
    expect(button('Left-handed', picker).getAttribute('aria-pressed')).toBe('true');

    tap('✕', picker);
    expect(headerClub()).toBe('Driver · Left-handed');
  });

  it('a simulator that sets the hand locks the choice', () => {
    act(() => useSystemStore.setState({ handed: 'RH', handedSource: 'simulator' }));
    const picker = container.querySelector('.picker-overlay')!;

    expect(button('Left-handed', picker).disabled).toBe(true);
    expect(picker.textContent).toContain('Set by the simulator');
  });
});
