// @vitest-environment jsdom
/** The target-line strip opens the aim view over the panel body; it fetches only while open. */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SetupSnapshot } from '../../hooks/useSetup';
import { LivePanel } from './LivePanel';

vi.mock('../../services/socketService', () => ({ socketService: { emit: vi.fn() } }));

// Node 25's own localStorage global shadows jsdom's; the stores read it at import.
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

const SETUP: SetupSnapshot = {
  revision: 4,
  box: {
    state: 'confirmed',
    box_px: [380, 560, 900, 720],
    ball_px: [640, 610],
    diameter_px: 22,
    image_size_px: [1280, 800],
  },
  aim: { state: 'set', aim_px: [900, 300] },
  ball_range: { state: 'set' },
  net_range: { state: 'default' },
  scene: { state: 'not_set' },
  live: { camera_available: true },
  unapplied: {},
};

let container: HTMLDivElement;
let root: Root;
const fetchMock = vi.fn(async () => new Response(new Blob(['x'], { type: 'image/jpeg' }), { status: 200 }));

function render() {
  act(() => {
    root.render(
      <LivePanel
        shot={null}
        shots={[]}
        profileId="james"
        profileName="James"
        clubLabel="DR"
        targetLine={{ status: 'set', yawDeg: 2.1, uncertaintyDeg: 0.8 }}
        setup={SETUP}
        handed="RH"
      />
    );
  });
}

const previewFetches = () =>
  fetchMock.mock.calls.filter((call: unknown[]) => String(call[0]).includes('/api/camera/preview.jpg')).length;

beforeEach(() => {
  vi.useFakeTimers();
  fetchMock.mockClear();
  vi.stubGlobal('fetch', fetchMock);
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: () => 'blob:aim', revokeObjectURL: () => {} }));
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('the aim view', () => {
  it('fetches nothing until the strip is tapped, then closes by itself after 10 s', async () => {
    render();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(previewFetches()).toBe(0);
    expect(container.querySelector('.live-panel__aim-view')).toBeNull();

    act(() => container.querySelector<HTMLButtonElement>('.live-panel__target-button')!.click());
    expect(container.querySelector('.live-panel__aim-view')).not.toBeNull();
    expect(container.querySelector('.live-panel__target-button')!.getAttribute('aria-expanded')).toBe('true');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(previewFetches()).toBe(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(container.querySelector('.live-panel__aim-view')).toBeNull();
    const fetched = previewFetches();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(previewFetches()).toBe(fetched);
  });

  it('closes on a tap', () => {
    render();
    act(() => container.querySelector<HTMLButtonElement>('.live-panel__target-button')!.click());

    act(() => container.querySelector<HTMLButtonElement>('.live-panel__aim-view')!.click());

    expect(container.querySelector('.live-panel__aim-view')).toBeNull();
  });
});
