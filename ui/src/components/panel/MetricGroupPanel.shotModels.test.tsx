import { describe, expect, it } from 'vitest';
import { everyMetricShot, record, render } from '../../../tests/metricPanelFixtures';
import { formatMetricValue } from './metricFormat';

describe('optional shot-model metrics', () => {
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
