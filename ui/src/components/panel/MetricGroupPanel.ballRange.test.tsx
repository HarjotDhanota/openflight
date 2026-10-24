import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { Shot, ShotMetric } from '../../types/shot';
import { MetricGroupPanel } from './MetricGroupPanel';
import { formatMetricValue } from './metricFormat';

// Each shot's ball distance, with where it came from, in the Flight details
function ballRange(overrides: Partial<ShotMetric>): ShotMetric {
  return {
    value: 2.08,
    unit: 'm',
    status: 'measured',
    source: "IWR post-impact launch point (this shot's own capture)",
    range: [2.05, 2.11],
    reason: null,
    frame: null,
    note: 'per-swing IWR range: not validated against a tape yet',
    normalized: null,
    ...overrides,
  };
}

function shot(record: ShotMetric): Shot {
  return {
    ball_speed_mph: 120,
    club_speed_mph: 85,
    smash_factor: 1.41,
    estimated_carry_yards: null,
    carry_range: null,
    club: '7-iron',
    timestamp: '2026-10-13T09:00:00Z',
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
    metrics: { ball_range: record },
  };
}

function cell(html: string): string {
  const start = html.indexOf('data-metric="ball_range"');
  expect(start).toBeGreaterThan(-1);
  return html.slice(start, html.indexOf('data-metric=', start + 30));
}

describe('ball distance in the Flight details', () => {
  it('shows metres to the centimetre', () => {
    expect(formatMetricValue(ballRange({}), 'imperial', 'ball_range')).toEqual(['2.08', 'm']);
  });

  it('says a measured distance came from the radar on this swing', () => {
    const html = cell(
      renderToString(
        <MetricGroupPanel
          view="flight"
          shot={shot(ballRange({}))}
          profileName="H"
          clubLabel="7"
          unitSystem="imperial"
        />
      )
    );
    expect(html).toContain('2.08');
    expect(html).toContain('radar, this swing');
  });

  it('says a camera-prior distance is the camera estimate (coarse)', () => {
    const html = cell(
      renderToString(
        <MetricGroupPanel
          view="flight"
          shot={shot(ballRange({ value: 2.11, status: 'estimated', range: [1.79, 2.43] }))}
          profileName="H"
          clubLabel="7"
          unitSystem="imperial"
        />
      )
    );
    expect(html).toContain('2.11');
    expect(html).toContain('camera estimate (coarse)');
  });
});
