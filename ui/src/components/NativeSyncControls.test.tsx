// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NativeSyncControls } from './NativeSyncControls';

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

describe('Native controls in the normal kiosk', () => {
  it('prepares one attempt using the current phase identity', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ enabled: true, phase: 'next', phase_token: 'fresh', message: 'Remove the ball' }),
      })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ accepted: true }) });
    vi.stubGlobal('fetch', fetcher);
    await act(async () => root.render(<NativeSyncControls />));
    expect(container.textContent).toContain('Remove the ball');
    await act(async () => container.querySelector('button')!.click());
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ phase_token: 'fresh' });
  });
  it('shows an armed instruction without offering a second arm', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ enabled: true, phase: 'armed', message: 'Armed: make one swing' }),
      })
    );
    await act(async () => root.render(<NativeSyncControls />));
    expect(container.textContent).toContain('Armed: make one swing');
    expect(container.querySelector('button')).toBeNull();
  });
  it('keeps a stale phase refusal visible', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce({ ok: true, json: async () => ({ enabled: true, phase: 'next', phase_token: 'old' }) })
        .mockResolvedValueOnce({ ok: false, json: async () => ({ error: 'Phase changed; refresh first' }) })
    );
    await act(async () => root.render(<NativeSyncControls />));
    await act(async () => container.querySelector('button')!.click());
    expect(container.querySelector('[role=alert]')?.textContent).toContain('Phase changed');
  });
});
