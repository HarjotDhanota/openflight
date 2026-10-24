// @vitest-environment jsdom
/** A tap on a cell with a long reason shows the full reason; a second tap folds it. */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { METRIC_CATALOG } from '../../data/metricCatalog';
import type { Shot, ShotMetric } from '../../types/shot';

vi.hoisted(() => {
  const store = new Map<string, string>();
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => void store.set(key, String(value)),
      removeItem: (key: string) => void store.delete(key),
      clear: () => store.clear(),
      key: (index: number) => [...store.keys()][index] ?? null,
      get length() {
        return store.size;
      },
    },
  });
});

const { MetricGroupPanel } = await import('./MetricGroupPanel');

const LONG = 'the OPS club speed is a percentile over 4-18 ms before impact, not the full speed';

function shot(): Shot {
  const metrics: Record<string, ShotMetric> = {};
  for (const spec of METRIC_CATALOG) {
    metrics[spec.id] = {
      value: null,
      unit: spec.unit,
      status: 'not_measured',
      source: null,
      range: null,
      reason: spec.id === 'club_path' ? LONG : 'no input',
      frame: null,
      note: null,
      normalized: null,
    };
  }
  return { club: 'driver', timestamp: 't', profile_id: 'p', metrics } as unknown as Shot;
}

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
});

describe('MetricGroupPanel taps', () => {
  it('opens and folds a long reason', () => {
    act(() => {
      root.render(<MetricGroupPanel view="club" shot={shot()} profileName="P" clubLabel="DR" unitSystem="imperial" />);
    });
    const cell = container.querySelector('[data-metric="club_path"]') as HTMLElement;
    const reason = () => cell.querySelector('.metric-cell__reason')?.textContent;
    expect(cell.getAttribute('role')).toBe('button');
    expect(reason()).toBe('the radar measures speed along its line only ›');
    act(() => cell.click());
    expect(cell.getAttribute('aria-expanded')).toBe('true');
    expect(reason()).toBe(LONG);
    act(() => cell.click());
    expect(reason()).toBe('the radar measures speed along its line only ›');
  });

  it('lists why each not-built metric is coming later on a tap', () => {
    act(() => {
      root.render(<MetricGroupPanel view="club" shot={shot()} profileName="P" clubLabel="DR" unitSystem="imperial" />);
    });
    const later = container.querySelector('[data-metric="coming-later"]') as HTMLElement;
    const list = later.querySelector('.metric-cell__later-list') as HTMLElement;
    expect(list.hidden).toBe(true);
    act(() => later.click());
    expect(list.hidden).toBe(false);
    expect(list.textContent).toContain('Dynamic lie: no input');
  });
});
