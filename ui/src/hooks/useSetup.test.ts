// @vitest-environment jsdom
import { act, createElement, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SetupSnapshot } from './useSetup';

const listeners = new Set<(snapshot: unknown) => void>();
const connectListeners = new Set<() => void>();
vi.mock('../services/socketService', () => ({
  socketService: {
    onSetupChanged: (listener: (snapshot: unknown) => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    onConnect: (listener: () => void) => {
      connectListeners.add(listener);
      return () => connectListeners.delete(listener);
    },
  },
}));

const { useSetup } = await import('./useSetup');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function snapshot(revision: number, boxState = 'not_set'): SetupSnapshot {
  const section = { state: 'not_set', reason: 'no setup file' };
  return {
    schema: 'openflight.setup.v1',
    revision,
    box: { state: boxState, reason: boxState === 'not_set' ? 'no setup file' : undefined },
    aim: section,
    ball_range: section,
    net_range: section,
    scene: section,
    live: {},
    unapplied: {},
  };
}

let latest: ReturnType<typeof useSetup> | null = null;
function Probe() {
  const value = useSetup();
  useEffect(() => {
    latest = value;
  });
  return null;
}

let container: HTMLDivElement;
let root: Root;
const fetchMock = vi.fn();

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe('useSetup', () => {
  beforeEach(() => {
    listeners.clear();
    connectListeners.clear();
    latest = null;
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('reads the setup again when the kiosk comes back', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => snapshot(3) });
    act(() => root.render(createElement(Probe)));
    await flush();
    fetchMock.mockResolvedValue({ ok: true, json: async () => snapshot(1, 'stale') });
    act(() => connectListeners.forEach((listener) => listener()));
    await flush();
    expect(latest?.setup?.revision).toBe(1);
    expect(latest?.setup?.box.state).toBe('stale');
  });

  it('retries a failed first read, then says it failed, until a read works', async () => {
    vi.useFakeTimers();
    fetchMock.mockResolvedValue({ ok: false, status: 503, json: async () => ({ ok: false }) });
    act(() => root.render(createElement(Probe)));
    await flush();
    expect(latest?.loadFailed).toBe(false);
    for (let retry = 0; retry < 5; retry += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
    }
    expect(fetchMock.mock.calls.length).toBe(3);
    expect(latest?.setup).toBeNull();
    expect(latest?.loadFailed).toBe(true);

    fetchMock.mockResolvedValue({ ok: true, json: async () => snapshot(2) });
    act(() => connectListeners.forEach((listener) => listener()));
    await flush();
    expect(latest?.setup?.revision).toBe(2);
    expect(latest?.loadFailed).toBe(false);
  });

  it('keeps the same setup when an event repeats it, so nothing re-renders', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => snapshot(3) });
    act(() => root.render(createElement(Probe)));
    await flush();
    const before = latest?.setup;
    act(() => listeners.forEach((listener) => listener(snapshot(3))));
    expect(latest?.setup).toBe(before);
  });

  it('reads the setup at start', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => snapshot(3) });
    act(() => root.render(createElement(Probe)));
    await flush();
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/api\/setup$/);
    expect(latest?.setup?.revision).toBe(3);
  });

  it('an incoming setup_changed replaces the state', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => snapshot(3) });
    act(() => root.render(createElement(Probe)));
    await flush();
    const next = snapshot(4, 'confirmed');
    act(() => listeners.forEach((listener) => listener(next)));
    expect(latest?.setup).toEqual(next);
    expect(latest?.setup?.box.state).toBe('confirmed');
  });

  it('ignores a setup_changed that is not a snapshot', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => snapshot(3) });
    act(() => root.render(createElement(Probe)));
    await flush();
    act(() => listeners.forEach((listener) => listener({ revision: 'x' })));
    expect(latest?.setup?.revision).toBe(3);
  });

  it('posts an action and takes the setup it answers with, refused or not', async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => snapshot(3) });
    act(() => root.render(createElement(Probe)));
    await flush();

    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 409,
      json: async () => ({ ok: false, reason: 'no_ball', setup: snapshot(3) }),
    });
    let result: Awaited<ReturnType<NonNullable<typeof latest>['act']>> | undefined;
    await act(async () => {
      result = await latest!.act('box', { action: 'confirm' });
    });
    expect(result).toEqual({ ok: false, reason: 'no_ball' });
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toMatch(/\/api\/setup\/box$/);
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ action: 'confirm' });

    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ ok: true, reason: null, setup: snapshot(5, 'confirmed') }),
    });
    await act(async () => {
      result = await latest!.act('box', { action: 'confirm' });
    });
    expect(result).toEqual({ ok: true, reason: null });
    expect(latest?.setup?.revision).toBe(5);
  });

  it('says an action failed when the kiosk cannot be reached', async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => snapshot(3) });
    act(() => root.render(createElement(Probe)));
    await flush();
    fetchMock.mockRejectedValueOnce(new TypeError('network down'));
    let result: { ok: boolean; reason: string | null } | undefined;
    await act(async () => {
      result = await latest!.act('range', { action: 'measure' });
    });
    expect(result).toEqual({ ok: false, reason: null });
    expect(latest?.setup?.revision).toBe(3);
  });
});
