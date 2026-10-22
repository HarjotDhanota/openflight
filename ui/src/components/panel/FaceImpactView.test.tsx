import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { Shot, ShotMetric } from '../../types/shot';
import { FaceImpactView } from './FaceImpactView';
import { FACE_SHAPES, faceCategory, GRID_STEP_MM, impactReading } from './faceImpact';
import { MetricGroupPanel } from './MetricGroupPanel';

function record(overrides: Partial<ShotMetric>): ShotMetric {
  return {
    value: null,
    unit: 'mm',
    status: 'not_measured',
    source: null,
    range: null,
    reason: 'needs this shot’s camera clip',
    frame: null,
    note: null,
    normalized: null,
    ...overrides,
  };
}

function reading(value: number, sigma: number): ShotMetric {
  return record({ status: 'experimental', value, range: [value - sigma, value + sigma], reason: null });
}

let stamp = 0;

function shot(overrides: Partial<Shot> = {}, metrics: Record<string, ShotMetric> = {}): Shot {
  return {
    ball_speed_mph: 120,
    club_speed_mph: 85,
    smash_factor: 1.41,
    estimated_carry_yards: null,
    carry_range: null,
    club: '7-iron',
    timestamp: `2026-10-22T09:00:${String(stamp++).padStart(2, '0')}Z`,
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
    impact_location: { status: 'ok', handedness: 'right' },
    metrics: {
      impact_offset: reading(8, 4),
      impact_height: reading(3, 5),
      ...metrics,
    },
    ...overrides,
  };
}

function render(latest: Shot, earlier: Shot[] = []) {
  return renderToString(<FaceImpactView shot={latest} sessionShots={[...earlier, latest]} />);
}

function attr(html: string, selector: string, name: string): string | null {
  const tag = html.match(new RegExp(`<[^>]*${selector}[^>]*>`));
  return tag?.[0].match(new RegExp(`${name}="([^"]*)"`))?.[1] ?? null;
}

describe('faceImpact', () => {
  it.each([
    ['driver', 'driver'],
    ['3-wood', 'fairway'],
    ['5-hybrid', 'hybrid'],
    ['7-iron', 'iron'],
    ['pw', 'wedge'],
    ['lw', 'wedge'],
    ['putter-ish', 'iron'],
  ])('draws a %s as a %s face', (club, category) => {
    expect(faceCategory(club)).toBe(category);
  });

  it('keeps every face inside the published limits (USGA woods 127 x 71 mm)', () => {
    for (const shape of Object.values(FACE_SHAPES)) {
      expect(shape.widthMm).toBeLessThanOrEqual(127);
      expect(shape.heightMm).toBeLessThanOrEqual(71);
    }
    expect(FACE_SHAPES.iron.widthMm).toBeGreaterThanOrEqual(74);
    expect(FACE_SHAPES.iron.widthMm).toBeLessThanOrEqual(87);
  });

  it('reads both axes and their 1-sigma from the metric records', () => {
    const point = impactReading(shot());
    expect(point).toMatchObject({ offset: 8, offsetSigma: 4, height: 3, heightSigma: 5, handedness: 'right' });
  });

  it('grids in 10 mm', () => {
    expect(GRID_STEP_MM).toBe(10);
  });
});

describe('FaceImpactView', () => {
  it('draws the shot on its club face with the EXP badge and toe and high callouts', () => {
    const html = render(shot());
    expect(html).toContain('data-face="iron"');
    expect(html).toContain('label-badge--experimental');
    expect(html).toContain('Toe 8 mm');
    expect(html).toContain('± 4 mm');
    expect(html).toContain('High 3 mm');
    expect(html).toContain('± 5 mm');
    expect(html).toContain('Illustrative face');
    expect(attr(html, 'face-impact__dot', 'cx')).toBe('-8');
    expect(attr(html, 'face-impact__dot', 'cy')).toBe('-3');
    expect(attr(html, 'face-impact__ellipse', 'rx')).toBe('4');
    expect(attr(html, 'face-impact__ellipse', 'ry')).toBe('5');
  });

  it('says heel and low for a strike on the other side', () => {
    const html = render(shot({}, { impact_offset: reading(-6, 3), impact_height: reading(-4, 5) }));
    expect(html).toContain('Heel 6 mm');
    expect(html).toContain('Low 4 mm');
  });

  it('mirrors the drawing for a left-handed golfer', () => {
    const html = render(shot({ impact_location: { status: 'ok', handedness: 'left' } }));
    expect(html).toContain('data-handed="left"');
    expect(attr(html, 'face-impact__outline-group', 'transform')).toBe('scale(-1 1)');
    expect(attr(html, 'face-impact__dot', 'cx')).toBe('8');
  });

  it('draws each club category with its own face', () => {
    expect(render(shot({ club: 'driver' }))).toContain('data-face="driver"');
    expect(render(shot({ club: '3-wood' }))).toContain('data-face="fairway"');
    expect(render(shot({ club: '4-hybrid' }))).toContain('data-face="hybrid"');
    expect(render(shot({ club: 'sw' }))).toContain('data-face="wedge"');
  });

  it('always reads in millimetres on a 10 mm grid', () => {
    const html = render(shot());
    expect(html).toContain('Toe 8 mm');
    expect(html).toContain('grid 10 mm');
    expect(html).not.toContain(' in ');
  });

  it('says why when the shot was not read, and draws no dot', () => {
    const reason = 'building the head outline: 3 of 5 swings with this club';
    const html = render(
      shot(
        { impact_location: { status: 'rejected' } },
        { impact_offset: record({ reason }), impact_height: record({ reason }) }
      )
    );
    expect(html).toContain(`Not measured: ${reason}`);
    expect(html).not.toContain('face-impact__dot');
  });

  it('keeps the heel-toe reading when only the height was refused', () => {
    const html = render(shot({}, { impact_height: record({ reason: 'no sole line at contact' }) }));
    expect(html).toContain('face-impact__dot');
    expect(html).toContain('High/low not measured: no sole line at contact');
    expect(attr(html, 'face-impact__dot', 'cy')).toBe('0');
  });

  it('shows this session’s earlier shots with the same club as faint dots', () => {
    const same = [shot({}, { impact_offset: reading(-5, 3) }), shot({}, { impact_offset: reading(2, 3) })];
    const other = shot({ club: 'driver' }, { impact_offset: reading(12, 3) });
    const unread = shot({}, { impact_offset: record({}) });
    const html = render(shot(), [...same, other, unread]);
    expect(html.match(/face-impact__earlier"/g)).toHaveLength(2);
    expect(html).toContain('2 earlier with this club');
  });
});

describe('the Club panel', () => {
  it('shows the face impact view above the club metrics, and the Flight panel does not', () => {
    const latest = shot();
    const club = renderToString(
      <MetricGroupPanel
        view="club"
        shot={latest}
        sessionShots={[latest]}
        profileName="P"
        clubLabel="7 Iron"
        unitSystem="metric"
      />
    );
    const flight = renderToString(
      <MetricGroupPanel view="flight" shot={latest} profileName="P" clubLabel="7 Iron" unitSystem="metric" />
    );
    expect(club.indexOf('face-impact')).toBeGreaterThan(-1);
    expect(club.indexOf('face-impact')).toBeLessThan(club.indexOf('class="metric-group"'));
    expect(flight).not.toContain('face-impact');
  });
});
