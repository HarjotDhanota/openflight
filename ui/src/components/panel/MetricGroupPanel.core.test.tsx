/** A build without the shot models' catalogue and metric names still shows every core metric. */
import { renderToString } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import coreCatalog from '../../data/metricCatalog.json';
import { en } from '../../i18n/en';
import type { MessageKey } from '../../i18n';
import type { Shot, ShotMetric } from '../../types/shot';
import { METRIC_CATALOG } from '../../data/metricCatalog';
import { MetricGroupPanel } from './MetricGroupPanel';

vi.mock('../../data/metricCatalog.shotModels.json', () => ({ default: undefined }));
vi.mock('../../i18n/metricNames.shotModels.ts', () => ({ metricNames: {} }));

function shot(): Shot {
  const metrics: Record<string, ShotMetric> = {};
  for (const spec of coreCatalog.metrics) {
    metrics[spec.id] = {
      value: 1.5,
      unit: spec.unit,
      status: 'measured',
      source: 'test source',
      range: null,
      reason: null,
      frame: null,
      note: null,
      normalized: null,
    };
  }
  return { club: '7-iron', timestamp: 't', profile_id: 'p', metrics } as unknown as Shot;
}

function cells(view: 'club' | 'flight'): Array<[string, string]> {
  const html = renderToString(
    <MetricGroupPanel view={view} shot={shot()} profileName="P" clubLabel="7 Iron" unitSystem="imperial" />
  );
  return [...html.matchAll(/data-metric="([a-z_]+)"[^>]*>\s*<span class="metric-cell__label">([^<]*)</g)].map(
    (match) => [match[1], match[2]]
  );
}

describe('MetricGroupPanel without the shot models', () => {
  it('uses the core catalogue', () => {
    expect(METRIC_CATALOG.map((spec) => spec.id)).toEqual(coreCatalog.metrics.map((spec) => spec.id));
  });

  it('shows every core metric once, under its own name, and nothing else', () => {
    const shown = [...cells('club'), ...cells('flight')];
    expect(shown.map(([id]) => id).sort()).toEqual(coreCatalog.metrics.map((spec) => spec.id).sort());
    for (const [id, name] of shown) {
      expect(name, id).toBe(en[`metricName.${id}` as MessageKey]);
    }
  });
});
