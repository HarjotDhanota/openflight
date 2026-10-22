import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { Shot, ShotMetric } from '../../types/shot';
import { FaceImpactView } from './FaceImpactView';
import { FACE_SHAPES, faceCategory, GRID_STEP_MM, hoselParts, impactReading, type FaceCategory } from './faceImpact';
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

type Point = [number, number];

/** Flattens an absolute M/L/C/Z path into a polygon. */
function polygon(d: string): Point[] {
  const tokens = d.match(/[MLCZ]|-?\d*\.?\d+/g) ?? [];
  const points: Point[] = [];
  let command = '';
  for (let i = 0; i < tokens.length;) {
    if (/[MLCZ]/.test(tokens[i])) command = tokens[i++];
    if (command === 'Z') continue;
    const take = (): Point => [Number(tokens[i++]), Number(tokens[i++])];
    if (command === 'C') {
      const [p0, p1, p2, p3] = [points[points.length - 1], take(), take(), take()];
      for (let k = 1; k <= 16; k += 1) {
        const t = k / 16;
        const a = (1 - t) ** 3,
          b = 3 * (1 - t) ** 2 * t,
          c = 3 * (1 - t) * t ** 2,
          e = t ** 3;
        points.push([a * p0[0] + b * p1[0] + c * p2[0] + e * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + e * p3[1]]);
      }
    } else points.push(take());
  }
  return points;
}

function extent(points: Point[]) {
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  return { minX: Math.min(...xs), maxX: Math.max(...xs), minY: Math.min(...ys), maxY: Math.max(...ys) };
}

/** Top and bottom (svg y, down) of the polygon's crossing at x. */
function span(points: Point[], x: number): [number, number] {
  const ys: number[] = [];
  points.forEach((a, index) => {
    const b = points[(index + 1) % points.length];
    if ((a[0] - x) * (b[0] - x) <= 0 && a[0] !== b[0]) ys.push(a[1] + ((x - a[0]) / (b[0] - a[0])) * (b[1] - a[1]));
  });
  return [Math.min(...ys), Math.max(...ys)];
}

/** Where a horizontal line at y crosses the polygon, toe to heel. */
function crossings(points: Point[], y: number): number[] {
  const xs: number[] = [];
  points.forEach((a, index) => {
    const b = points[(index + 1) % points.length];
    if ((a[1] - y) * (b[1] - y) <= 0 && a[1] !== b[1]) xs.push(a[0] + ((y - a[1]) / (b[1] - a[1])) * (b[0] - a[0]));
  });
  return xs.sort((p, q) => p - q);
}

function inside(points: Point[], x: number, y: number): boolean {
  let hit = false;
  points.forEach((a, index) => {
    const b = points[(index + 1) % points.length];
    if (a[1] > y !== b[1] > y && x < a[0] + ((y - a[1]) / (b[1] - a[1])) * (b[0] - a[0])) hit = !hit;
  });
  return hit;
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

  it('keeps the iron at a 7-iron length', () => {
    const iron = extent(polygon(FACE_SHAPES.iron.head));
    expect(iron.maxX - iron.minX).toBeGreaterThanOrEqual(74);
    expect(iron.maxX - iron.minX).toBeLessThanOrEqual(87);
  });

  it.each([
    ['driver', [105, 115], [55, 62]],
    ['fairway', [90, 100], [35, 40]],
    ['hybrid', [80, 90], [40, 45]],
    ['iron', [60, 80], [48, 52]],
    ['wedge', [60, 80], [55, 60]],
  ] as const)('draws the %s face at its category size', (category, width, height) => {
    const face = extent(polygon(FACE_SHAPES[category].face));
    expect(face.maxX - face.minX).toBeGreaterThanOrEqual(width[0]);
    expect(face.maxX - face.minX).toBeLessThanOrEqual(width[1]);
    expect(face.maxY - face.minY).toBeGreaterThanOrEqual(height[0]);
    expect(face.maxY - face.minY).toBeLessThanOrEqual(height[1]);
  });

  it.each(['driver', 'fairway', 'hybrid'] as const)(
    'puts the %s origin midway between its score-line ends and midway up the face there',
    (category) => {
      const shape = FACE_SHAPES[category];
      const face = polygon(shape.face);
      const toeEnd = Math.min(...shape.grooves.map((groove) => groove.from));
      const heelEnd = Math.max(...shape.grooves.map((groove) => groove.to));
      expect(Math.abs((toeEnd + heelEnd) / 2)).toBeLessThanOrEqual(0.5);
      const [top, bottom] = span(face, 0);
      expect(Math.abs((top + bottom) / 2)).toBeLessThanOrEqual(0.5);
      const { minX, maxX } = extent(face);
      expect(Math.abs((minX + maxX) / 2)).toBeLessThanOrEqual(1);
    }
  );

  it.each(['iron', 'wedge'] as const)('puts the %s origin on the centre of its groove block', (category) => {
    const grooves = FACE_SHAPES[category].grooves;
    const ys = grooves.map((groove) => groove.y);
    expect(Math.abs((Math.min(...ys) + Math.max(...ys)) / 2)).toBeLessThanOrEqual(0.5);
    for (const groove of grooves) expect(Math.abs((groove.from + groove.to) / 2)).toBeLessThanOrEqual(0.5);
  });

  it.each(['iron', 'wedge'] as const)(
    'draws the %s as a blade: tallest at the toe, topline falling to the heel',
    (category) => {
      const face = polygon(FACE_SHAPES[category].face);
      const box = extent(face);
      const top = face.reduce((best, point) => (point[1] < best[1] ? point : best));
      expect(top[0]).toBeLessThan(box.minX + (box.maxX - box.minX) / 3);
      const toe = span(face, box.minX + 12);
      const heel = span(face, box.maxX - 8);
      expect(toe[1] - toe[0] - (heel[1] - heel[0])).toBeGreaterThan(8);
    }
  );

  it('gives the iron a 30-35 mm heel', () => {
    const face = polygon(FACE_SHAPES.iron.face);
    const heel = span(face, extent(face).maxX - 8);
    expect(heel[1] - heel[0]).toBeGreaterThanOrEqual(30);
    expect(heel[1] - heel[0]).toBeLessThanOrEqual(35);
  });

  it.each([
    ['iron', [8, 9]],
    ['wedge', [10, 12]],
  ] as const)('lays the %s score lines out like a real club', (category, count) => {
    const shape = FACE_SHAPES[category];
    const face = polygon(shape.face);
    const grooves = [...shape.grooves].sort((a, b) => b.y - a.y);
    expect(grooves.length).toBeGreaterThanOrEqual(count[0]);
    expect(grooves.length).toBeLessThanOrEqual(count[1]);
    const pitches = grooves.slice(1).map((groove, k) => grooves[k].y - groove.y);
    for (const pitch of pitches) {
      expect(Math.abs(pitch - pitches[0])).toBeLessThanOrEqual(0.05);
      expect(pitch).toBeGreaterThanOrEqual(3.5);
      expect(pitch).toBeLessThanOrEqual(3.8);
    }
    const lowest = span(face, 0)[1] - grooves[0].y;
    expect(lowest).toBeGreaterThanOrEqual(4);
    expect(lowest).toBeLessThanOrEqual(5);
    const top = grooves[grooves.length - 1];
    expect(top.y - span(face, top.to)[0]).toBeGreaterThanOrEqual(3);
    for (const groove of grooves) {
      expect(groove.to - groove.from).toBeGreaterThanOrEqual(58);
      expect(groove.to - groove.from).toBeLessThanOrEqual(62);
      const across = crossings(face, groove.y);
      expect(groove.from - across[0]).toBeGreaterThanOrEqual(8);
      expect(groove.from - across[0]).toBeLessThanOrEqual(10);
      expect(across[across.length - 1] - groove.to).toBeGreaterThanOrEqual(4);
      expect(across[across.length - 1] - groove.to).toBeLessThanOrEqual(6);
    }
    expect(FACE_SHAPES.wedge.grooves.length).toBeGreaterThan(FACE_SHAPES.iron.grooves.length);
  });

  it.each(Object.keys(FACE_SHAPES) as FaceCategory[])('keeps every %s score line on the face', (category) => {
    const shape = FACE_SHAPES[category];
    const face = polygon(shape.face);
    for (const groove of shape.grooves) {
      expect(inside(face, groove.from - 1, groove.y)).toBe(true);
      expect(inside(face, groove.to + 1, groove.y)).toBe(true);
    }
  });

  it('leaves the centre of the woods plain and gives the hybrid a few lines', () => {
    for (const category of ['driver', 'fairway'] as const) {
      const shape = FACE_SHAPES[category];
      expect(shape.grooves.length).toBeGreaterThan(0);
      for (const groove of shape.grooves) expect(groove.to < -15 || groove.from > 15).toBe(true);
      expect(shape.mark).toBeTruthy();
    }
    expect(FACE_SHAPES.hybrid.grooves.length).toBeGreaterThanOrEqual(3);
    expect(FACE_SHAPES.hybrid.grooves.length).toBeLessThanOrEqual(7);
  });

  it.each([
    ['iron', 62],
    ['wedge', 64],
  ] as const)('raises the %s hosel up and to the heel at its lie angle', (category, lie) => {
    const [a, b, c, d] = polygon(hoselParts(FACE_SHAPES[category].hosel).tube);
    const bottom = [(a[0] + d[0]) / 2, (a[1] + d[1]) / 2];
    const top = [(b[0] + c[0]) / 2, (b[1] + c[1]) / 2];
    expect(top[0]).toBeGreaterThan(bottom[0]);
    expect((Math.atan2(bottom[1] - top[1], top[0] - bottom[0]) * 180) / Math.PI).toBeCloseTo(lie, 0);
  });

  it('keeps every drawing inside its view', () => {
    for (const shape of Object.values(FACE_SHAPES)) {
      const { tube, ferrule } = hoselParts(shape.hosel);
      const box = extent([shape.head, shape.face, tube, ferrule].flatMap(polygon));
      expect(box.minX).toBeGreaterThanOrEqual(shape.bounds.minX);
      expect(box.maxX).toBeLessThanOrEqual(shape.bounds.maxX);
      expect(box.maxY).toBeLessThanOrEqual(shape.bounds.maxY);
      expect(shape.bounds.minY).toBeLessThan(box.minY);
    }
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

  it('draws the club as a filled silhouette: head, face, hosel, ferrule, shaft and score lines', () => {
    const html = render(shot());
    for (const part of ['head', 'face', 'hosel', 'ferrule', 'shaft']) expect(html).toContain(`face-impact__${part}"`);
    expect(html.match(/face-impact__groove"/g)).toHaveLength(FACE_SHAPES.iron.grooves.length);
    const fill = attr(html, 'face-impact__head"', 'fill');
    expect(fill).toMatch(/^url\(#.+\)$/);
    expect(html).toContain(`id="${fill!.slice(5, -1)}"`);
  });

  it('marks the centre of the driver face and keeps its score lines toward heel and toe', () => {
    const html = render(shot({ club: 'driver' }));
    expect(html).toContain('face-impact__mark');
    expect(html.match(/face-impact__groove"/g)).toHaveLength(FACE_SHAPES.driver.grooves.length);
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
