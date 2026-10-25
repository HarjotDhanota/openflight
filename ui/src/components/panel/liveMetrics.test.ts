import { describe, expect, it } from 'vitest';
import type { Shot, ShotMetric, SwingSpeedStats } from '../../types/shot';
import {
  buildLiveMetrics,
  LIVE_METRIC_COUNT,
  NO_VALUE,
  pinSelectedMetric,
  SWING_METRIC_COUNT,
  type LiveMetric,
} from './liveMetrics';

const emptySwingStats: SwingSpeedStats = {
  count: 0,
  last_speed_mph: 0,
  best_speed_mph: 0,
  avg_speed_mph: 0,
};

function makeShot(overrides: Partial<Shot> = {}): Shot {
  return {
    ball_speed_mph: 92,
    club_speed_mph: 68,
    smash_factor: 1.35,
    estimated_carry_yards: 210,
    carry_range: [205, 215],
    club: 'driver',
    timestamp: '2026-08-19T10:00:00Z',
    peak_magnitude: 100,
    launch_angle_vertical: 13.4,
    launch_angle_horizontal: -1.2,
    launch_angle_confidence: 0.8,
    angle_source: 'radar',
    club_angle_deg: 2.1,
    club_path_deg: -0.6,
    spin_axis_deg: 3.4,
    spin_rpm: 2650,
    spin_confidence: 0.9,
    spin_quality: 'high',
    spin_source: 'measured',
    spin_method: null,
    carry_spin_adjusted: 214,
    ...overrides,
  };
}

function byId(metrics: LiveMetric[], id: string): LiveMetric {
  const metric = metrics.find((m) => m.id === id);
  if (!metric) throw new Error(`no metric ${id}`);
  return metric;
}

describe('buildLiveMetrics', () => {
  it('returns a fixed ten metrics for a ball-strike shot, including club AoA', () => {
    const metrics = buildLiveMetrics(makeShot(), 'imperial', emptySwingStats);

    expect(metrics).toHaveLength(LIVE_METRIC_COUNT);
    expect(new Set(metrics.map((m) => m.id)).size).toBe(LIVE_METRIC_COUNT);
    expect(metrics.map((m) => m.id)).toEqual([
      'ball_speed',
      'carry',
      'club_speed',
      'smash',
      'launch_v',
      'launch_h',
      'spin',
      'spin_axis',
      'club_path',
      'club_aoa',
    ]);
    expect(byId(metrics, 'club_aoa')).toMatchObject({ value: '+2.1', unit: '°', label: 'Club AoA' });
  });

  it('keeps the same ten metrics in the same order when values are missing', () => {
    const full = buildLiveMetrics(makeShot(), 'imperial', emptySwingStats);
    const sparse = buildLiveMetrics(
      makeShot({
        club_speed_mph: null,
        smash_factor: null,
        launch_angle_vertical: null,
        launch_angle_horizontal: null,
        launch_angle_confidence: null,
        club_path_deg: null,
        spin_axis_deg: null,
        spin_rpm: null,
        spin_quality: null,
        spin_source: null,
        carry_spin_adjusted: null,
      }),
      'imperial',
      emptySwingStats
    );

    // The grid must not reflow between shots.
    expect(sparse.map((m) => m.id)).toEqual(full.map((m) => m.id));
    expect(byId(sparse, 'club_speed').value).toBe(NO_VALUE);
    expect(byId(sparse, 'spin').value).toBe(NO_VALUE);
    expect(byId(sparse, 'spin').confidence).toBeNull();
    // No stale unit hanging off a placeholder.
    expect(byId(sparse, 'club_speed').unit).toBeUndefined();
  });

  it('formats ball speed and carry in the selected unit system', () => {
    const imperial = buildLiveMetrics(makeShot(), 'imperial', emptySwingStats);
    const metric = buildLiveMetrics(makeShot(), 'metric', emptySwingStats);

    expect(byId(imperial, 'ball_speed')).toMatchObject({ value: '92.0', unit: 'mph' });
    expect(byId(metric, 'ball_speed')).toMatchObject({ value: '148.1', unit: 'km/h' });
    expect(byId(imperial, 'carry')).toMatchObject({ value: '214', unit: 'yds' });
    expect(byId(metric, 'carry')).toMatchObject({ value: '196', unit: 'm' });
  });

  it('prefers the spin-adjusted carry and labels a table carry estimated', () => {
    const adjusted = byId(buildLiveMetrics(makeShot(), 'imperial', emptySwingStats), 'carry');
    expect(adjusted.value).toBe('214');
    expect(adjusted.subtext).toBe('Spin-adjusted');
    // an unlabelled committed carry (a replay, an older log) is an estimate
    expect(adjusted.status).toBe('estimated');

    const estimated = byId(
      buildLiveMetrics(makeShot({ carry_spin_adjusted: null }), 'imperial', emptySwingStats),
      'carry'
    );
    expect(estimated.value).toBe('210');
    expect(estimated.subtext).toBeUndefined();
    expect(estimated.status).toBe('estimated');
  });

  it('shows a fusion carry that is not measured as no value, never the table', () => {
    const carry = byId(
      buildLiveMetrics(
        makeShot({ carry_status: 'not_measured', carry_spin_adjusted: null, estimated_carry_yards: 210 }),
        'imperial',
        emptySwingStats
      ),
      'carry'
    );

    expect(carry.value).toBe(NO_VALUE);
    expect(carry.unit).toBeUndefined();
    expect(carry.subtext).toBeUndefined();
    expect(carry.status).toBeUndefined();
  });

  it('labels a simulated fusion carry calculated, even one an older log called measured', () => {
    const measured = byId(
      buildLiveMetrics(makeShot({ carry_status: 'measured' }), 'imperial', emptySwingStats),
      'carry'
    );
    const calculated = byId(
      buildLiveMetrics(makeShot({ carry_status: 'calculated' }), 'imperial', emptySwingStats),
      'carry'
    );

    expect(measured).toMatchObject({ value: '214', unit: 'yds' });
    expect(measured.status).toBe('calculated');
    expect(calculated).toMatchObject({ value: '214', status: 'calculated' });
  });

  it('signs the directional angles and labels the shot shape', () => {
    const metrics = buildLiveMetrics(makeShot(), 'imperial', emptySwingStats);

    expect(byId(metrics, 'launch_h').value).toBe('-1.2');
    expect(byId(metrics, 'club_path').value).toBe('-0.6');
    expect(byId(metrics, 'club_aoa').value).toBe('+2.1');

    const negativeAoa = buildLiveMetrics(makeShot({ club_angle_deg: -3.2 }), 'imperial', emptySwingStats);
    expect(byId(negativeAoa, 'club_aoa').value).toBe('-3.2');
    expect(byId(metrics, 'spin_axis')).toMatchObject({ value: '+3.4', subtext: 'Fade' });

    const draw = buildLiveMetrics(makeShot({ spin_axis_deg: -4 }), 'imperial', emptySwingStats);
    expect(byId(draw, 'spin_axis').subtext).toBe('Draw');

    const straight = buildLiveMetrics(makeShot({ spin_axis_deg: 0.5 }), 'imperial', emptySwingStats);
    expect(byId(straight, 'spin_axis').subtext).toBe('Straight');
  });

  it('maps launch-angle confidence onto the dot scale', () => {
    const high = buildLiveMetrics(makeShot({ launch_angle_confidence: 0.9 }), 'imperial', emptySwingStats);
    const medium = buildLiveMetrics(makeShot({ launch_angle_confidence: 0.5 }), 'imperial', emptySwingStats);
    const low = buildLiveMetrics(makeShot({ launch_angle_confidence: 0.1 }), 'imperial', emptySwingStats);

    expect(byId(high, 'launch_v').confidence).toBe('high');
    expect(byId(medium, 'launch_v').confidence).toBe('medium');
    expect(byId(low, 'launch_v').confidence).toBe('low');
  });

  it('preserves camera-fused club delivery values and confidence', () => {
    const metrics = buildLiveMetrics(
      makeShot({
        club_angle_deg: null,
        club_path_deg: null,
        experimental_fused_attack_angle_deg: -4.2,
        experimental_fused_attack_angle_confidence: 'medium',
        experimental_fused_club_path_deg: 3.1,
        experimental_fused_club_path_confidence: 'high',
        experimental_fused_status: 'approach_mixed',
      }),
      'imperial',
      emptySwingStats
    );

    expect(byId(metrics, 'club_aoa')).toMatchObject({
      value: '-4.2',
      unit: '°',
      subtext: 'camera fused',
      confidence: 'medium',
      confidenceLabel: 'experimental',
    });
    expect(byId(metrics, 'club_path')).toMatchObject({
      value: '+3.1',
      unit: '°',
      subtext: 'camera fused',
      confidence: 'high',
      confidenceLabel: 'experimental',
    });
  });

  it('keeps camera-fusion rejection status but hides superseded radar candidates', () => {
    const metrics = buildLiveMetrics(
      makeShot({
        club_angle_deg: null,
        club_path_deg: null,
        experimental_attack_angle_deg: -32.2,
        experimental_club_path_deg: 130.9,
        experimental_fused_status: 'rejected_no_impact',
      }),
      'imperial',
      emptySwingStats
    );

    expect(byId(metrics, 'club_aoa')).toMatchObject({
      value: NO_VALUE,
      subtext: 'rejected: no impact',
      confidence: 'experimental',
    });
    expect(byId(metrics, 'club_path')).toMatchObject({
      value: NO_VALUE,
      subtext: 'rejected: no impact',
      confidence: 'experimental',
    });
  });

  it('labels camera-assisted horizontal launch', () => {
    const metrics = buildLiveMetrics(
      makeShot({ launch_angle_horizontal_source: 'camera_assisted_experimental' }),
      'imperial',
      emptySwingStats
    );

    expect(byId(metrics, 'launch_h')).toMatchObject({
      subtext: 'camera assisted',
      confidenceLabel: 'experimental',
    });
  });

  describe('horizontal launch tile reads its own axis (audit F5)', () => {
    it('shows the horizontal confidence, not the vertical one', () => {
      const metrics = buildLiveMetrics(
        makeShot({
          launch_angle_confidence: 0.9,
          launch_angle_vertical_confidence: 0.9,
          launch_angle_horizontal_confidence: 0.3,
          launch_angle_horizontal_source: 'camera_only_experimental',
        }),
        'imperial',
        emptySwingStats
      );

      expect(byId(metrics, 'launch_v').confidence).toBe('high');
      expect(byId(metrics, 'launch_h').confidence).toBe('low');
    });

    it('flags only the axis that is estimated', () => {
      const verticalEstimated = buildLiveMetrics(
        makeShot({
          angle_source: 'estimated',
          launch_angle_vertical_source: 'estimated',
          launch_angle_horizontal_source: 'radar',
          launch_angle_horizontal_confidence: 0.8,
        }),
        'imperial',
        emptySwingStats
      );
      expect(byId(verticalEstimated, 'launch_v').status).toBe('estimated');
      expect(byId(verticalEstimated, 'launch_h').status).toBe('measured');

      const horizontalEstimated = buildLiveMetrics(
        makeShot({
          angle_source: 'radar',
          launch_angle_vertical_source: 'radar',
          launch_angle_horizontal: 0.0,
          launch_angle_horizontal_source: 'estimated',
          launch_angle_horizontal_confidence: 0.5,
        }),
        'imperial',
        emptySwingStats
      );
      expect(byId(horizontalEstimated, 'launch_h').status).toBe('estimated');
      expect(byId(horizontalEstimated, 'launch_h').confidence).toBe('medium');
    });

    it.each([
      ['camera_only_experimental', 'camera only'],
      ['camera_legacy_fallback', 'camera legacy'],
      ['camera_low_consensus', 'camera, low consensus'],
      ['radar_low_coherence', 'radar, low coherence'],
    ])('labels %s as experimental', (source, subtext) => {
      const metrics = buildLiveMetrics(
        makeShot({ launch_angle_horizontal_source: source, launch_angle_horizontal_confidence: 0.3 }),
        'imperial',
        emptySwingStats
      );

      expect(byId(metrics, 'launch_h')).toMatchObject({
        subtext,
        confidence: 'low',
        confidenceLabel: 'experimental',
      });
    });

    it('labels a radial-ratio smash factor, shown not hidden (D21, D37)', () => {
      const labelled = buildLiveMetrics(
        makeShot({ smash_factor: 1.5, smash_factor_status: 'radial_ratio' }),
        'imperial',
        emptySwingStats
      );
      const plain = buildLiveMetrics(makeShot({ smash_factor: 1.5 }), 'imperial', emptySwingStats);

      expect(byId(labelled, 'smash')).toMatchObject({ value: '1.50', subtext: 'radial ratio' });
      expect(byId(plain, 'smash').value).toBe('1.50');
      expect(byId(plain, 'smash').subtext).toBeUndefined();
    });

    it('labels an uncalibrated radar horizontal (audit F8)', () => {
      const metrics = buildLiveMetrics(
        makeShot({
          launch_angle_horizontal_source: 'radar',
          launch_angle_horizontal_status: 'azimuth_uncalibrated',
          launch_angle_horizontal_confidence: 0.9,
        }),
        'imperial',
        emptySwingStats
      );

      expect(byId(metrics, 'launch_h')).toMatchObject({
        subtext: 'azimuth uncalibrated',
        confidence: 'high',
      });
    });

    it('labels an uncalibrated IWR club path (audit F8)', () => {
      const metrics = buildLiveMetrics(
        makeShot({
          club_path_deg: null,
          experimental_club_path_deg: 1.5,
          experimental_club_path_status: 'azimuth_uncalibrated',
        }),
        'imperial',
        emptySwingStats
      );

      expect(byId(metrics, 'club_path')).toMatchObject({
        value: '+1.5',
        subtext: 'azimuth uncalibrated',
      });
    });

    it('falls back to the shared fields for shots recorded before per-axis fields', () => {
      const metrics = buildLiveMetrics(
        makeShot({ launch_angle_confidence: 0.9, angle_source: 'radar' }),
        'imperial',
        emptySwingStats
      );

      expect(byId(metrics, 'launch_h').confidence).toBe('high');
      expect(byId(metrics, 'launch_h').subtext).toBeUndefined();
    });
  });

  it('marks estimated launch and spin with a flag, not provenance subtext', () => {
    const measured = buildLiveMetrics(makeShot(), 'imperial', emptySwingStats);
    expect(byId(measured, 'launch_v').subtext).toBeUndefined();
    expect(byId(measured, 'launch_v').status).toBe('measured');
    expect(byId(measured, 'launch_h').status).toBe('measured');
    expect(byId(measured, 'spin').subtext).toBeUndefined();
    expect(byId(measured, 'spin').status).toBe('measured');

    const estimated = buildLiveMetrics(
      makeShot({ angle_source: 'estimated', spin_source: 'calculated' }),
      'imperial',
      emptySwingStats
    );
    expect(byId(estimated, 'launch_v').status).toBe('estimated');
    expect(byId(estimated, 'launch_h').status).toBe('estimated');
    expect(byId(estimated, 'spin').status).toBe('calculated');
    expect(byId(estimated, 'launch_v').subtext).toBeUndefined();
    expect(byId(estimated, 'spin').subtext).toBeUndefined();
  });

  it.each(['estimated', 'model_geometry'])('labels a %s spin (a weak start direction, D39) as such', (source) => {
    const spin = byId(buildLiveMetrics(makeShot({ spin_source: source }), 'imperial', emptySwingStats), 'spin');
    expect(spin.status).toBe(source);
  });

  it('returns the swing-speed set, with ids that never collide with ball-strike ids', () => {
    const stats: SwingSpeedStats = { count: 12, last_speed_mph: 101, best_speed_mph: 108, avg_speed_mph: 99 };
    const swing = buildLiveMetrics(
      makeShot({ mode: 'swing-speed', training_implement_label: 'Stack 100g', swing_speed_reading_count: 7 }),
      'imperial',
      stats
    );
    const ballStrike = buildLiveMetrics(makeShot(), 'imperial', emptySwingStats);

    expect(swing).toHaveLength(SWING_METRIC_COUNT);
    expect(byId(swing, 'swing_best').value).toBe('108.0');
    expect(byId(swing, 'swing_implement').value).toBe('Stack 100g');

    const shared = swing.map((m) => m.id).filter((id) => ballStrike.some((m) => m.id === id));
    expect(shared).toEqual([]);
  });
});

describe('face angle', () => {
  it.each([
    [undefined, 'face +1.5° (D-plane)'],
    ['d_plane_estimate_azimuth_uncalibrated', 'face +1.5° (D-plane, azimuth uncalibrated)'],
  ])('rides on the club path tile as a D-plane subtitle (status %s)', (status, subtext) => {
    const metrics = buildLiveMetrics(
      makeShot({ club_path_deg: -0.6, experimental_face_angle_deg: 1.5, experimental_face_angle_status: status }),
      'imperial',
      emptySwingStats
    );
    expect(byId(metrics, 'club_path').subtext).toBe(subtext);
    expect(metrics).toHaveLength(LIVE_METRIC_COUNT);
  });

  it('rides on the camera path the server built it from, not an accepted IWR path', () => {
    // Server displayed_club_path() mirrors this precedence.
    const metrics = buildLiveMetrics(
      makeShot({
        club_path_deg: null,
        experimental_fused_club_path_deg: -4.0,
        experimental_fused_status: 'chained_high',
        experimental_club_path_deg: 6.0,
        experimental_club_path_status: 'accepted',
        experimental_face_angle_deg: 3.5,
        experimental_face_angle_path_source: 'camera_fused_chained',
      }),
      'imperial',
      emptySwingStats
    );

    expect(byId(metrics, 'club_path')).toMatchObject({
      value: '-4.0',
      subtext: 'camera fused · face +3.5° (D-plane)',
    });
  });
});

describe('pinSelectedMetric', () => {
  const metrics = buildLiveMetrics(makeShot(), 'imperial', emptySwingStats);

  it('moves the selected metric to the front and keeps the rest in order', () => {
    const pinned = pinSelectedMetric(metrics, 'spin');

    expect(pinned.map((m) => m.id)).toEqual([
      'spin',
      'ball_speed',
      'carry',
      'club_speed',
      'smash',
      'launch_v',
      'launch_h',
      'spin_axis',
      'club_path',
      'club_aoa',
    ]);
  });

  it('falls back to the first metric when the stored id is absent', () => {
    expect(pinSelectedMetric(metrics, 'swing_best')[0]?.id).toBe('ball_speed');
    expect(pinSelectedMetric(metrics, null)[0]?.id).toBe('ball_speed');
    expect(pinSelectedMetric(metrics, 'nonsense')).toHaveLength(LIVE_METRIC_COUNT);
  });

  it('handles an empty metric list', () => {
    expect(pinSelectedMetric([], 'ball_speed')).toEqual([]);
  });
});

describe('missing values: one rule, never a unit next to the no-value mark', () => {
  const tile = (metrics: LiveMetric[], id: string) => metrics.find((metric) => metric.id === id);

  it.each([
    ['null', null],
    ['undefined', undefined],
    ['NaN', Number.NaN],
    ['infinite', Number.POSITIVE_INFINITY],
  ])('shows a %s value as the no-value mark with no unit', (_label, missing) => {
    const metrics = buildLiveMetrics(
      makeShot({
        ball_speed_mph: missing as number,
        club_speed_mph: missing as number,
        smash_factor: missing as number,
        launch_angle_vertical: missing as number,
        launch_angle_horizontal: missing as number,
        spin_rpm: missing as number,
        spin_axis_deg: missing as number,
        estimated_carry_yards: missing as number,
        carry_spin_adjusted: missing as number,
        club_angle_deg: missing as number,
        club_path_deg: missing as number,
      }),
      'imperial',
      emptySwingStats
    );
    for (const id of ['ball_speed', 'club_speed', 'smash', 'launch_v', 'launch_h', 'spin', 'spin_axis', 'carry']) {
      expect(tile(metrics, id)?.value, id).toBe(NO_VALUE);
      expect(tile(metrics, id)?.unit, id).toBeUndefined();
    }
    for (const metric of metrics) {
      expect(metric.value).not.toContain('NaN');
      expect(metric.value).not.toContain('Infinity');
      if (metric.value === NO_VALUE) expect(metric.unit, metric.id).toBeUndefined();
    }
  });
});

describe('buildLiveMetrics with the server labels (Shot.metrics)', () => {
  const record = (overrides: Partial<ShotMetric>): ShotMetric => ({
    value: null,
    unit: 'deg',
    status: 'not_measured',
    source: null,
    range: null,
    reason: null,
    frame: null,
    note: null,
    normalized: null,
    ...overrides,
  });

  it('takes each tile label from the server record', () => {
    const metrics = buildLiveMetrics(
      makeShot({
        metrics: {
          carry: record({ value: 214, unit: 'yd', status: 'calculated', source: 'Ferguson 2022' }),
          spin_axis: record({ value: 3.4, status: 'model_geometry', source: 'model geometry (D-plane)' }),
          smash_factor: record({ value: 1.35, unit: '', status: 'calculated' }),
        },
      }),
      'imperial',
      emptySwingStats
    );
    expect(byId(metrics, 'carry').status).toBe('calculated');
    expect(byId(metrics, 'spin_axis').status).toBe('model_geometry');
    expect(byId(metrics, 'smash').status).toBe('calculated');
  });

  it("shows the arc's model-geometry club path when no measured path exists", () => {
    const metrics = buildLiveMetrics(
      makeShot({
        club_path_deg: null,
        club_angle_deg: null,
        metrics: {
          club_path: record({ value: -3.25, status: 'model_geometry', source: 'club_arc:published_prior' }),
          attack_angle: record({ value: -4.5, status: 'calculated', source: 'club_arc:shot_fit' }),
        },
      }),
      'imperial',
      emptySwingStats
    );
    expect(byId(metrics, 'club_path')).toMatchObject({ value: '-3.3', unit: '°', status: 'model_geometry' });
    expect(byId(metrics, 'club_aoa')).toMatchObject({ value: '-4.5', status: 'calculated' });
  });

  it('shows why a metric is not measured, compactly', () => {
    const metrics = buildLiveMetrics(
      makeShot({
        carry_status: 'not_measured',
        carry_spin_adjusted: null,
        metrics: { carry: record({ unit: 'yd', reason: 'spin not measured on this shot' }) },
      }),
      'imperial',
      emptySwingStats
    );
    expect(byId(metrics, 'carry')).toMatchObject({ value: NO_VALUE, reason: 'spin not measured on this shot' });
    expect(byId(metrics, 'carry').status).toBeUndefined();
  });

  it('formats every tile from the server value when the shot has none of its own', () => {
    const server = (id: string, value: number, unit: string) => [id, record({ value, unit, status: 'calculated' })];
    const shot = makeShot({
      ball_speed_mph: null as unknown as number,
      club_speed_mph: null,
      smash_factor: null,
      estimated_carry_yards: null,
      carry_spin_adjusted: null,
      carry_status: 'not_measured',
      launch_angle_vertical: null,
      launch_angle_horizontal: null,
      launch_angle_horizontal_source: 'camera_only_experimental',
      club_angle_deg: null,
      club_path_deg: null,
      spin_axis_deg: null,
      spin_rpm: null,
      metrics: Object.fromEntries([
        server('ball_speed', 150.26, 'mph'),
        server('carry', 230.4, 'yd'),
        server('club_speed', 101.04, 'mph'),
        server('smash_factor', 1.487, ''),
        server('launch_vertical', 12.34, 'deg'),
        server('launch_horizontal', 2, 'deg'),
        server('spin_rate', 2650.4, 'rpm'),
        server('spin_axis', -3.44, 'deg'),
        server('club_path', 0, 'deg'),
        server('attack_angle', -4.56, 'deg'),
      ]),
    });
    const shown = (unitSystem: 'imperial' | 'metric') =>
      Object.fromEntries(
        buildLiveMetrics(shot, unitSystem, emptySwingStats).map((m) => [
          m.id,
          [m.value, m.unit, m.status, m.subtext, m.confidence, m.confidenceLabel],
        ])
      );
    expect(shown('imperial')).toEqual({
      ball_speed: ['150.3', 'mph', 'calculated', undefined, undefined, undefined],
      carry: ['230', 'yds', 'calculated', undefined, undefined, undefined],
      club_speed: ['101.0', 'mph', 'calculated', undefined, undefined, undefined],
      smash: ['1.49', undefined, 'calculated', undefined, undefined, undefined],
      launch_v: ['12.3', '°', 'calculated', undefined, null, undefined],
      launch_h: ['+2.0', '°', 'calculated', undefined, null, undefined],
      spin: ['2,650', 'rpm', 'calculated', undefined, null, undefined],
      spin_axis: ['-3.4', '°', 'calculated', undefined, null, undefined],
      club_path: ['+0.0', '°', 'calculated', undefined, null, undefined],
      club_aoa: ['-4.6', '°', 'calculated', undefined, null, undefined],
    });
    const metric = shown('metric');
    expect(metric.ball_speed.slice(0, 2)).toEqual(['241.8', 'km/h']);
    expect(metric.carry.slice(0, 2)).toEqual(['211', 'm']);
    expect(metric.club_speed.slice(0, 2)).toEqual(['162.6', 'km/h']);
  });
});
