// @vitest-environment jsdom
/**
 * Integration (B-extra): the App's `acknowledgeable` wiring. A live result the club picker
 * or the menu covers, or that the user cannot see, is reported, never acknowledged as
 * painted; the same result with nothing over it is acknowledged. Through the real App,
 * not only LivePanel's prop.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { clearPendingPaints, noteShotReceived } from './services/paintTrace';
import { socketService } from './services/socketService';
import { useProfileStore } from './stores/useProfileStore';
import { useShotStore } from './stores/useShotStore';
import { makeShot, nextFrames, stubLayoutGlobals, traceTag } from './test/liveTraceFixtures';
import type { Shot } from './types/shot';

vi.mock('./hooks/useSocket', () => ({ useSocket: () => ({ shutdown: vi.fn() }) }));
vi.mock('./services/socketService', () => {
  const emitTrace = vi.fn();
  const others = new Map<PropertyKey, unknown>();
  const socketService = new Proxy(
    {},
    {
      get: (_target, key) => {
        if (key === 'emit') return emitTrace;
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

const emitTrace = vi.mocked(socketService.emit);
let container: HTMLDivElement;
let root: Root;

const calls = (event: string) =>
  emitTrace.mock.calls.filter(([name]) => name === event).map(([, payload]) => payload as Record<string, unknown>);

function click(label: string): void {
  const button = container.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`);
  expect(button, label).not.toBeNull();
  act(() => button!.click());
}

/** A traced shot arrives as socketService delivers it: noted first, then stored. */
function arrive(shot: Shot, revision = 1, attemptId = 't-00007'): void {
  act(() => {
    noteShotReceived(shot, 'shot', traceTag(revision, 'first', attemptId), performance.now());
    useShotStore.getState().addShot(shot);
  });
}

beforeAll(stubLayoutGlobals);
afterAll(() => vi.unstubAllGlobals());

beforeEach(() => {
  emitTrace.mockReset();
  useShotStore.setState({ latestShot: null, shots: [], isNewShot: false, shotProcessingPhase: null, shotVersion: 0 });
  useProfileStore.setState({
    profiles: [{ id: 'james', name: 'James' }] as never,
    activeProfileId: 'james',
    loaded: true,
  });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root.render(<App />));
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  clearPendingPaints();
});

describe('App: covered or hidden live results are never acknowledged', () => {
  it('a result under the club picker (open on load) is reported covered, even after it closes', async () => {
    expect(container.querySelector('[aria-label="Select club"]')).not.toBeNull();
    arrive(makeShot());
    await nextFrames();
    click('Close Select club');
    await nextFrames();

    expect(calls('trace_paint')).toEqual([]);
    expect(calls('trace_not_shown').map((p) => [p.revision, p.reason])).toEqual([[1, 'covered']]);
  });

  it('a result under the open menu is reported covered', async () => {
    click('Close Select club');
    click('Open menu');
    arrive(makeShot());
    await nextFrames();

    expect(calls('trace_paint')).toEqual([]);
    expect(calls('trace_not_shown').map((p) => p.reason)).toEqual(['covered']);
  });

  it('a result received while the document is hidden is reported, not acknowledged', async () => {
    click('Close Select club');
    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    try {
      arrive(makeShot());
      await nextFrames();
    } finally {
      visibility.mockRestore();
    }

    expect(calls('trace_paint')).toEqual([]);
    expect(calls('trace_not_shown').map((p) => p.reason)).toEqual(['document_hidden']);
  });

  it('the same result with nothing over it is acknowledged (control)', async () => {
    click('Close Select club');
    arrive(makeShot());
    await nextFrames();

    expect(calls('trace_not_shown')).toEqual([]);
    expect(calls('trace_paint').map((p) => [p.attempt_id, p.revision])).toEqual([['t-00007', 1]]);
  });
});
