import { renderToString } from 'react-dom/server';
import { everyMetricShot, record, render } from '../../../tests/metricPanelFixtures';
import { describe, expect, it } from 'vitest';
import { METRIC_CATALOG } from '../../data/metricCatalog';
import { METRIC_LABELS } from '../../types/shot';
import { MetricGroupPanel } from './MetricGroupPanel';
import { PANEL_GROUPS, formatMetricValue } from './metricFormat';

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

  it('labels the shot with the hand it was hit with when there is no impact reading', () => {
    const shot = everyMetricShot();
    shot.handed = 'LH';
    const html = render('club', shot);
    expect(html.match(/class="panel-header__club">([^<]*)/)?.[1]).toBe('7 Iron · Left-handed');
    expect(html).toContain('data-handed="left"');
  });

  it('draws the selected club, unmeasured, until a shot is hit with it', () => {
    const html = renderToString(
      <MetricGroupPanel
        view="club"
        shot={null}
        club="driver"
        profileName="Golfer"
        clubLabel="Driver · Right-handed"
        unitSystem="imperial"
      />
    );
    expect(html.match(/class="panel-header__club">([^<]*)/)?.[1]).toBe('Driver · Right-handed');
    expect(html).toContain('Hit a shot with your Driver to see its delivery');
    expect(html).toContain('data-face="driver"');
    expect(html).not.toContain('data-face="iron"');
    expect(html).not.toContain('data-metric=');
    expect(html).not.toMatch(/data-view-metric="[a-z_]+" data-status="(?!not_measured)/);
  });

  it('asks for a shot with the selected club on the Flight panel too', () => {
    const html = renderToString(
      <MetricGroupPanel
        view="flight"
        shot={null}
        club="7-iron"
        profileName="Golfer"
        clubLabel="7 Iron · Right-handed"
        unitSystem="imperial"
      />
    );
    expect(html.match(/class="panel-header__club">([^<]*)/)?.[1]).toBe('7 Iron · Right-handed');
    expect(html).toContain('Hit a shot with your 7 Iron to see its flight');
    expect(html).not.toContain('data-metric=');
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
