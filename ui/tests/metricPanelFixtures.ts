import { createElement } from 'react';
import { renderToString } from 'react-dom/server';
import { METRIC_CATALOG } from '../src/data/metricCatalog';
import { METRIC_LABELS } from '../src/types/shot';
import type { MetricLabel, Shot, ShotMetric } from '../src/types/shot';
import { MetricGroupPanel } from '../src/components/panel/MetricGroupPanel';

export function record(overrides: Partial<ShotMetric>): ShotMetric {
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

export function everyMetricShot(): Shot {
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

export function render(view: 'club' | 'flight', shot: Shot | null = everyMetricShot()) {
  return renderToString(
    createElement(MetricGroupPanel, {
      view,
      shot,
      profileName: 'Golfer',
      clubLabel: '7 Iron',
      unitSystem: 'imperial',
    })
  );
}
