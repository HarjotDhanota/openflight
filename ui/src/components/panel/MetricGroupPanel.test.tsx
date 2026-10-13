import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { METRIC_CATALOG } from '../../data/metricCatalog';
import type { MetricLabel, Shot, ShotMetric } from '../../types/shot';
import { MetricGroupPanel } from './MetricGroupPanel';
import { PANEL_GROUPS, formatMetricValue } from './metricFormat';

function record(overrides: Partial<ShotMetric>): ShotMetric {
  return {
    value: null,
    unit: 'deg',
    status: 'not_measured',
    source: null,
    range: null,
    reason: 'not measured on this shot',
    frame: null,
    note: null,
    normalized: null,
    ...overrides,
  };
}

const LABEL_CYCLE: MetricLabel[] = ['measured', 'calculated', 'model_geometry', 'estimated', 'not_measured'];

/** A shot whose metrics cycle through every label, so every badge renders. */
function everyMetricShot(): Shot {
  const metrics: Record<string, ShotMetric> = {};
  METRIC_CATALOG.forEach((spec, index) => {
    const status = spec.pending ? 'not_measured' : LABEL_CYCLE[index % LABEL_CYCLE.length];
    metrics[spec.id] =
      status === 'not_measured'
        ? record({ unit: spec.unit, reason: spec.pending ? `pending ${spec.pending}: not built yet` : 'no input' })
        : record({ unit: spec.unit, status, value: 1.5 + index, reason: null, source: 'test source' });
  });
  return {
    ball_speed_mph: 120,
    club_speed_mph: 85,
    smash_factor: 1.41,
    estimated_carry_yards: null,
    carry_range: null,
    club: '7-iron',
    timestamp: '2026-10-12T09:00:00Z',
    peak_magnitude: 1,
    launch_angle_vertical: 18,
    launch_angle_horizontal: 1,
    launch_angle_confidence: 0.8,
    angle_source: 'radar',
    club_angle_deg: null,
    club_path_deg: null,
    spin_axis_deg: null,
    spin_rpm: 6800,
    spin_confidence: 0.9,
    spin_quality: 'high',
    spin_source: 'measured',
    carry_spin_adjusted: null,
    profile_id: 'p',
    metrics,
  };
}

function render(view: 'club' | 'flight', shot: Shot | null = everyMetricShot()) {
  return renderToString(
    <MetricGroupPanel view={view} shot={shot} profileName="Harjot" clubLabel="7 Iron" unitSystem="imperial" />
  );
}

describe('MetricGroupPanel', () => {
  it('puts every metric in the catalog on the Club or the Flight panel, once', () => {
    const shown = [...PANEL_GROUPS.club, ...PANEL_GROUPS.flight];
    const groups = new Set(METRIC_CATALOG.map((spec) => spec.group));
    expect(new Set(shown)).toEqual(groups);
    expect(shown).toHaveLength(groups.size);
  });

  it.each(METRIC_CATALOG.map((spec) => [spec.id, spec.group]))('shows %s with its label', (id, group) => {
    const html = render(PANEL_GROUPS.club.includes(group as never) ? 'club' : 'flight');
    const cell = html.match(new RegExp(`<div[^>]*data-metric="${id}"[^>]*data-status="([a-z_]+)"`));
    expect(cell, id).not.toBeNull();
    const status = cell![1];
    if (status === 'not_measured') {
      expect(html).toMatch(new RegExp(`data-metric="${id}"[\\s\\S]*?metric-cell__reason`));
    } else {
      expect(html).toMatch(new RegExp(`data-metric="${id}"[\\s\\S]*?label-badge--${status}`));
    }
  });

  it('gives each of the four labels its own badge', () => {
    const html = render('club') + render('flight');
    for (const status of ['measured', 'calculated', 'model_geometry', 'estimated']) {
      expect(html).toContain(`label-badge--${status}`);
    }
  });

  it('names the item that builds an unbuilt metric', () => {
    const html = render('club');
    expect(html).toContain('pending E5: not built yet');
    expect(html).toContain('pending E6: not built yet');
  });

  it('shows the empty state before the first shot', () => {
    const html = render('flight', null);
    expect(html).toContain('No shot yet');
    expect(html).not.toContain('data-metric=');
  });

  it('formats each unit, converting to the chosen system', () => {
    expect(formatMetricValue(record({ value: 123.45, unit: 'mph' }), 'metric')).toEqual(['198.7', 'km/h']);
    expect(formatMetricValue(record({ value: 201.4, unit: 'yd' }), 'imperial')).toEqual(['201', 'yds']);
    expect(formatMetricValue(record({ value: -2.04, unit: 'deg' }), 'imperial')).toEqual(['-2.0', '°']);
    expect(formatMetricValue(record({ value: 6812.3, unit: 'rpm' }), 'imperial')[0]).toMatch(/^6,?812$/);
    expect(formatMetricValue(record({ value: 1.4123, unit: '' }), 'imperial')).toEqual(['1.41', '']);
    expect(formatMetricValue(record({ value: 5.432, unit: 's' }), 'imperial')).toEqual(['5.43', 's']);
    expect(formatMetricValue(record({ value: 41.6, unit: 'mm' }), 'imperial')).toEqual(['42', 'mm']);
    expect(formatMetricValue(record({ value: null, unit: 'mm' }), 'imperial')).toEqual(['—', '']);
  });
});
