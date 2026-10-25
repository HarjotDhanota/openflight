import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { METRIC_CATALOG } from '../../data/metricCatalog';
import { METRIC_LABELS } from '../../types/shot';
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

const LABEL_CYCLE: readonly MetricLabel[] = METRIC_LABELS;

function everyMetricShot(): Shot {
  const metrics: Record<string, ShotMetric> = {};
  METRIC_CATALOG.forEach((spec, index) => {
    const status = LABEL_CYCLE[index % LABEL_CYCLE.length];
    metrics[spec.id] =
      status === 'not_measured'
        ? record({ unit: spec.unit, reason: 'no input' })
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
    <MetricGroupPanel view={view} shot={shot} profileName="Golfer" clubLabel="7 Iron" unitSystem="imperial" />
  );
}

describe('MetricGroupPanel', () => {
  it.each(['right', 'left'] as const)('labels the displayed club shot with its recorded %s hand', (hand) => {
    const shot = everyMetricShot();
    shot.impact_location = { handedness: hand };
    const html = renderToString(
      <MetricGroupPanel
        view="club"
        shot={shot}
        profileName="Golfer"
        clubLabel={`Driver · ${hand === 'left' ? 'Right' : 'Left'}-handed`}
        unitSystem="imperial"
      />
    );
    expect(html.match(/class="panel-header__club">([^<]*)/)?.[1]).toBe(
      `7 Iron · ${hand === 'left' ? 'Left' : 'Right'}-handed`
    );
  });

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

  it('gives each of the five labels its own badge', () => {
    const html = render('club') + render('flight');
    for (const status of METRIC_LABELS.filter((label) => label !== 'not_measured')) {
      expect(html).toContain(`label-badge--${status}`);
    }
  });

  it('shows dynamic lie as unavailable with its camera reason instead of coming later', () => {
    const shot = everyMetricShot();
    shot.metrics!.dynamic_lie = record({ unit: 'deg', reason: 'no visible lower shaft near impact' });
    const html = render('club', shot);
    expect(html).toContain('data-metric="dynamic_lie"');
    expect(html).toContain('no visible lower shaft');
  });

  it('gives impact location its own cells, with the reason when it was not read', () => {
    const shot = everyMetricShot();
    const reason = 'building the head outline: 3 of 5 swings with this club';
    shot.metrics!.impact_offset = record({ unit: 'mm', reason });
    shot.metrics!.impact_height = record({ unit: 'mm', reason });
    const html = render('club', shot);
    expect(html).toMatch(/data-metric="impact_offset"[^>]*data-status="not_measured"/);
    expect(html).toMatch(/data-metric="impact_height"[^>]*data-status="not_measured"/);
    expect(html).toContain('building the head outline');
  });

  it('shows an experimental reading with the EXP badge, its range and that it is not validated', () => {
    const shot = everyMetricShot();
    shot.metrics!.impact_offset = record({
      unit: 'mm',
      status: 'experimental',
      value: 6,
      range: [2, 10],
      reason: null,
      note: 'not validated yet: compare it with the face spray marks',
    });
    const html = render('club', shot);
    const cell = html.slice(html.indexOf('data-metric="impact_offset"'));
    expect(cell).toMatch(/label-badge--experimental[^>]*title="Experimental \(not yet validated\)"/);
    expect(cell).toContain('+6');
    expect(cell).toContain('range 2 to 10');
  });

  it('shows a short reason, with the full one a tap away', () => {
    const shot = everyMetricShot();
    const long = 'the OPS club speed is a percentile over 4-18 ms before impact, not the full speed';
    shot.metrics!.club_path = record({ reason: long });
    const html = render('club', shot);
    const start = html.lastIndexOf('<div', html.indexOf('data-metric="club_path"'));
    const cell = html.slice(start, html.indexOf('data-metric=', html.indexOf('data-metric="club_path"') + 30));
    expect(cell).toContain('role="button"');
    expect(cell).toContain('aria-expanded="false"');
    expect(cell).toContain('the radar measures speed along its line only');
    expect(html).toContain(long);
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
    expect(formatMetricValue(record({ value: 6.2, unit: 'mm' }), 'imperial', 'impact_offset')).toEqual(['+6', 'mm']);
    expect(formatMetricValue(record({ value: -3, unit: 'mm' }), 'imperial', 'impact_height')).toEqual(['-3', 'mm']);
  });
});

describe('spin and smash index', () => {
  it('shows both on the Club panel right after spin loft, in percent', () => {
    const html = render('club');
    const loft = html.indexOf('data-metric="spin_loft"');
    const spin = html.indexOf('data-metric="spin_index"');
    const smash = html.indexOf('data-metric="smash_index"');
    expect(loft).toBeGreaterThan(-1);
    expect(spin).toBeGreaterThan(loft);
    expect(smash).toBeGreaterThan(spin);
    expect(html).toContain('Spin index');
    expect(html).toContain('Smash index');
  });

  it('formats an index as a percentage to one decimal', () => {
    expect(formatMetricValue(record({ unit: '%', value: 98.94, status: 'calculated' }), 'imperial')).toEqual([
      '98.9',
      '%',
    ]);
  });
});
