import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { Shot, ShotMetric } from '../../types/shot';
import { FaceImpactView } from './FaceImpactView';
import {
  FACE_SHAPES,
  faceCategory,
  faceCentre,
  GROOVE_WIDTH_MM,
  hoselParts,
  impactReading,
  USGA,
  type FaceCategory,
} from './faceImpact';
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

  it('keeps the iron at its 7-iron blade length, toe to the heel end of the face', () => {
    const iron = FACE_SHAPES.iron;
    const blade = extent(polygon(iron.face)).maxX - extent(polygon(iron.head)).minX;
    expect(blade).toBeGreaterThanOrEqual(74);
    expect(blade).toBeLessThanOrEqual(87);
  });

  it.each([
    ['driver', [105, 115], [48, 53]],
    ['fairway', [90, 100], [34, 38]],
    ['hybrid', [80, 90], [29, 33]],
    ['iron', [70, 78], [44, 48]],
    ['wedge', [70, 80], [53, 58]],
  ] as const)('draws the %s face at its category size', (category, width, height) => {
    const face = extent(polygon(FACE_SHAPES[category].face));
    expect(face.maxX - face.minX).toBeGreaterThanOrEqual(width[0]);
    expect(face.maxX - face.minX).toBeLessThanOrEqual(width[1]);
    expect(face.maxY - face.minY).toBeGreaterThanOrEqual(height[0]);
    expect(face.maxY - face.minY).toBeLessThanOrEqual(height[1]);
  });

  it.each(['driver', 'fairway', 'hybrid'] as const)(
    'puts the %s centre lines on the face centre: equal heel/toe and sole/crown distances to the face edge',
    (category) => {
      const face = polygon(FACE_SHAPES[category].face);
      const across = crossings(face, 0);
      expect(Math.abs(across[0] + across[across.length - 1])).toBeLessThanOrEqual(0.05);
      const [top, bottom] = span(face, 0);
      expect(Math.abs(top + bottom)).toBeLessThanOrEqual(0.05);
      const [x, y] = faceCentre(category, FACE_SHAPES[category]);
      expect(Math.abs(x)).toBeLessThanOrEqual(0.05);
      expect(Math.abs(y)).toBeLessThanOrEqual(0.05);
    }
  );

  it.each(['iron', 'wedge'] as const)(
    'puts the %s centre lines on the longest score line’s midpoint, midway between leading edge and top line there',
    (category) => {
      const shape = FACE_SHAPES[category];
      const longest = Math.max(...shape.grooves.map((groove) => groove.to - groove.from));
      for (const groove of shape.grooves.filter((g) => g.to - g.from > longest - 0.01))
        expect(Math.abs((groove.from + groove.to) / 2)).toBeLessThanOrEqual(0.05);
      const [top, bottom] = span(polygon(shape.face), 0);
      expect(Math.abs(top + bottom)).toBeLessThanOrEqual(0.05);
      const [x, y] = faceCentre(category, shape);
      expect(Math.abs(x)).toBeLessThanOrEqual(0.05);
      expect(Math.abs(y)).toBeLessThanOrEqual(0.05);
    }
  );

  it.each(['iron', 'wedge'] as const)(
    'draws the %s as a blade: tallest at the toe, topline falling to the heel',
    (category) => {
      const face = polygon(FACE_SHAPES[category].face);
      const box = extent(face);
      const top = face.reduce((best, point) => (point[1] < best[1] ? point : best));
      expect(top[0]).toBeLessThan(box.minX + (box.maxX - box.minX) / 3);
      const toe = span(face, box.minX + 12);
      const heel = span(face, box.maxX - 8);
      expect(toe[1] - toe[0] - (heel[1] - heel[0])).toBeGreaterThan(12);
    }
  );

  it.each([
    ['iron', 13, 6, [2.3, 3], [3, 6], [4, 8]],
    ['wedge', 17, 8, [3.5, 4.5], [6, 10], [3, 6]],
  ] as const)(
    'lays the %s score lines out as the photo: %i lines, the top %i stepped under the topline',
    (category, count, stepped, lowest, toeMargin, heelMargin) => {
      const shape = FACE_SHAPES[category];
      const face = polygon(shape.face);
      const grooves = [...shape.grooves].sort((a, b) => b.y - a.y);
      expect(grooves).toHaveLength(count);
      const toeEnds = grooves.map((groove) => groove.from);
      expect(Math.max(...toeEnds) - Math.min(...toeEnds)).toBeLessThanOrEqual(0.1);
      const pitches = grooves.slice(1).map((groove, k) => grooves[k].y - groove.y);
      for (const pitch of pitches) {
        expect(Math.abs(pitch - pitches[0])).toBeLessThanOrEqual(0.05);
        expect(pitch).toBeGreaterThanOrEqual(2.8);
        expect(pitch).toBeLessThanOrEqual(3);
      }
      const below = span(face, 0)[1] - grooves[0].y;
      expect(below).toBeGreaterThanOrEqual(lowest[0]);
      expect(below).toBeLessThanOrEqual(lowest[1]);
      const heelEnd = Math.max(...grooves.map((groove) => groove.to));
      const steps = grooves.filter((groove) => groove.to < heelEnd - 0.05);
      expect(steps).toHaveLength(stepped);
      steps.forEach((groove, k) => {
        const under = groove.y - span(face, groove.to)[0];
        expect(under).toBeGreaterThanOrEqual(2.5);
        expect(under).toBeLessThanOrEqual(5.5);
        if (k) expect(groove.to).toBeLessThan(steps[k - 1].to);
      });
      expect(heelEnd - grooves[0].from).toBeGreaterThanOrEqual(50);
      expect(heelEnd - grooves[0].from).toBeLessThanOrEqual(56);
      const across = crossings(face, grooves[0].y);
      expect(grooves[0].from - across[0]).toBeGreaterThanOrEqual(toeMargin[0]);
      expect(grooves[0].from - across[0]).toBeLessThanOrEqual(toeMargin[1]);
      expect(across[across.length - 1] - heelEnd).toBeGreaterThanOrEqual(heelMargin[0]);
      expect(across[across.length - 1] - heelEnd).toBeLessThanOrEqual(heelMargin[1]);
    }
  );

  it.each(['iron', 'wedge'] as const)(
    'bounds the %s milled scoring area with straight toe and heel sides just outside the lines',
    (category) => {
      const shape = FACE_SHAPES[category];
      const scoring = polygon(shape.scoring!);
      const box = extent(scoring);
      const toeEnd = shape.grooves[0].from;
      const heelEnd = Math.max(...shape.grooves.map((groove) => groove.to));
      expect(toeEnd - box.minX).toBeGreaterThan(0.2);
      expect(toeEnd - box.minX).toBeLessThan(1.5);
      expect(box.maxX - heelEnd).toBeGreaterThan(0.2);
      expect(box.maxX - heelEnd).toBeLessThan(1.5);
      const toeSide = scoring.filter((point) => Math.abs(point[0] - box.minX) < 0.01);
      const heelSide = scoring.filter((point) => Math.abs(point[0] - box.maxX) < 0.01);
      expect(toeSide.length).toBeGreaterThanOrEqual(2);
      expect(heelSide.length).toBeGreaterThanOrEqual(2);
      const face = polygon(shape.face);
      for (const groove of shape.grooves) {
        expect(inside(scoring, groove.from + 0.5, groove.y)).toBe(true);
        expect(inside(scoring, groove.to - 0.5, groove.y)).toBe(true);
      }
      for (const point of scoring) expect(inside(face, point[0], point[1])).toBe(true);
    }
  );

  it.each(['iron', 'wedge'] as const)('keeps the %s topline highlight on the face, short of the hosel', (category) => {
    const shape = FACE_SHAPES[category];
    const box = extent(polygon(shape.face));
    expect(shape.edges.length).toBeGreaterThan(0);
    for (const { d } of shape.edges)
      for (const [x, y] of polygon(d)) {
        expect(x).toBeLessThanOrEqual(box.maxX + 0.01);
        expect(x).toBeGreaterThanOrEqual(box.minX - 0.01);
        expect(y).toBeGreaterThanOrEqual(box.minY - 0.01);
      }
  });

  it.each(['iron', 'wedge'] as const)(
    'fades the %s topline highlight out down the toe instead of cutting it off',
    (category) => {
      const { edges } = FACE_SHAPES[category];
      const tail = edges[edges.length - 1];
      expect(tail.fade).not.toBeNull();
      const ends = polygon(tail.d);
      expect(Math.hypot(ends[0][0] - tail.fade![0][0], ends[0][1] - tail.fade![0][1])).toBeLessThan(0.02);
      const last = ends[ends.length - 1];
      expect(Math.hypot(last[0] - tail.fade![1][0], last[1] - tail.fade![1][1])).toBeLessThan(0.02);
      const main = polygon(edges[0].d);
      expect(Math.hypot(main.at(-1)![0] - ends[0][0], main.at(-1)![1] - ends[0][1])).toBeLessThan(0.02);
    }
  );

  it.each(Object.keys(FACE_SHAPES) as FaceCategory[])(
    'runs the %s outline from the head into the tube without a step',
    (category) => {
      const shape = FACE_SHAPES[category];
      const numbers = (shape.silhouette.match(/-?\d*\.?\d+/g) ?? []).map(Number);
      const points: Point[] = [];
      for (let i = 0; i < numbers.length; i += 2) points.push([numbers[i], numbers[i + 1]]);
      const tube = polygon(hoselParts(shape.hosel).tube);
      const n = tube.length / 2;
      for (const foot of [tube[0], tube[2 * n - 1]]) {
        // M p0, then C c1 c2 p per segment: an end point sits at index 3j, its handles either side.
        const k = points.findIndex(
          (point, i) => i % 3 === 0 && Math.hypot(point[0] - foot[0], point[1] - foot[1]) < 0.02
        );
        expect(k).toBeGreaterThan(0);
        const [before, at, after] = [points[k - 1], points[k], points[k + 1]];
        const u = [at[0] - before[0], at[1] - before[1]];
        const v = [after[0] - at[0], after[1] - at[1]];
        const turn = Math.abs(Math.atan2(u[0] * v[1] - u[1] * v[0], u[0] * v[0] + u[1] * v[1]));
        expect(turn).toBeLessThan(0.02);
      }
    }
  );

  it('fades the hybrid face into the neck at the heel; the other faces keep a hard edge', () => {
    const face = extent(polygon(FACE_SHAPES.hybrid.face));
    const [from, to] = FACE_SHAPES.hybrid.faceFade!;
    expect(from).toBeGreaterThan(0);
    expect(to).toBeLessThanOrEqual(face.maxX + 0.01);
    expect(to - from).toBeGreaterThan(8);
    for (const category of ['driver', 'fairway', 'iron', 'wedge'] as const)
      expect(FACE_SHAPES[category].faceFade).toBeNull();
  });

  it.each(Object.keys(FACE_SHAPES) as FaceCategory[])('keeps every %s score line on the face', (category) => {
    const shape = FACE_SHAPES[category];
    const face = polygon(shape.face);
    for (const groove of shape.grooves) {
      expect(inside(face, groove.from - 1, groove.y)).toBe(true);
      expect(inside(face, groove.to + 1, groove.y)).toBe(true);
    }
  });

  it('gives the driver 7 lines in each heel and toe block, inner ends on slanted lines, outer ends stepping with the face', () => {
    const shape = FACE_SHAPES.driver;
    const face = polygon(shape.face);
    const toe = shape.grooves.filter((groove) => groove.to < -10).sort((a, b) => a.y - b.y);
    const heel = shape.grooves.filter((groove) => groove.from > 10).sort((a, b) => a.y - b.y);
    expect(toe).toHaveLength(7);
    expect(heel).toHaveLength(7);
    for (const inner of [toe.map((groove) => groove.to), heel.map((groove) => groove.from)]) {
      const slope = (inner[6] - inner[0]) / (toe[6].y - toe[0].y);
      expect(Math.abs(slope)).toBeGreaterThan(0.1);
      inner.forEach((x, k) => expect(Math.abs(inner[0] + slope * (toe[k].y - toe[0].y) - x)).toBeLessThanOrEqual(0.06));
    }
    expect(toe[6].to).toBeGreaterThan(toe[0].to);
    expect(heel[6].from).toBeLessThan(heel[0].from);
    for (const groove of toe) {
      const margin = groove.from - crossings(face, groove.y)[0];
      expect(margin).toBeGreaterThan(2);
      expect(margin).toBeLessThan(16);
    }
    for (const groove of heel) {
      const across = crossings(face, groove.y);
      const margin = across[across.length - 1] - groove.to;
      expect(margin).toBeGreaterThan(2);
      expect(margin).toBeLessThan(16);
    }
  });

  it('gives the driver a plain centre with three bars, the middle one longest, and a small cross on it', () => {
    const shape = FACE_SHAPES.driver;
    const bars = shape.grooves.filter((groove) => groove.from < 0 && groove.to > 0).sort((a, b) => a.y - b.y);
    expect(bars).toHaveLength(3);
    const length = bars.map((bar) => bar.to - bar.from);
    expect(length[1]).toBeGreaterThan(length[0] + 8);
    expect(length[1]).toBeGreaterThan(length[2] + 8);
    const cross = polygon(shape.mark!);
    const x = cross[0][0];
    for (const point of cross) expect(point[0]).toBeCloseTo(x, 5);
    const ys = cross.map((point) => point[1]);
    expect(Math.min(...ys)).toBeLessThan(bars[1].y);
    expect(Math.max(...ys)).toBeGreaterThan(bars[1].y);
    expect(x).toBeGreaterThan(bars[1].from);
    expect(x).toBeLessThan(bars[1].to);
  });

  it('gives the fairway six lines across the face, their ends stepping in with the outline, the lowest shortest', () => {
    const shape = FACE_SHAPES.fairway;
    const face = polygon(shape.face);
    const grooves = [...shape.grooves].sort((a, b) => a.y - b.y);
    expect(grooves).toHaveLength(6);
    expect(shape.mark).toBeNull();
    for (const groove of grooves) {
      expect(groove.from).toBeLessThan(-15);
      expect(groove.to).toBeGreaterThan(15);
      const across = crossings(face, groove.y);
      expect(groove.from - across[0]).toBeGreaterThanOrEqual(2);
      expect(groove.from - across[0]).toBeLessThanOrEqual(14);
      expect(across[across.length - 1] - groove.to).toBeGreaterThanOrEqual(2);
      expect(across[across.length - 1] - groove.to).toBeLessThanOrEqual(14);
    }
    const lengths = grooves.map((groove) => groove.to - groove.from);
    expect(lengths[5]).toBeLessThan(Math.min(...lengths.slice(0, 5)) - 10);
    for (let k = 1; k < 6; k += 1) expect(lengths[k]).toBeLessThanOrEqual(lengths[k - 1] + 0.5);
  });

  it('fits the fairway sleeve with a grey band across it just above where the neck meets the head', () => {
    const { hosel } = FACE_SHAPES.fairway;
    expect(hosel.collar).not.toBeNull();
    const [from, to] = hosel.collar!;
    expect(from).toBeCloseTo(hosel.tube[0][0], 5);
    expect(to - from).toBeGreaterThan(2);
    expect(to - from).toBeLessThan(4);
    const parts = hoselParts(hosel);
    const tube = polygon(parts.tube);
    const band = polygon(parts.collar!);
    const middle = band.reduce(
      (sum, point) => [sum[0] + point[0] / band.length, sum[1] + point[1] / band.length],
      [0, 0]
    );
    expect(inside(tube, middle[0], middle[1])).toBe(true);
    for (const [x, y] of band.slice(0, 4))
      expect(inside(tube, x + (middle[0] - x) * 0.01, y + (middle[1] - y) * 0.01)).toBe(true);
  });

  it('gives the hybrid a short top line on the toe half, five long lines and a shorter bottom line under them', () => {
    const grooves = [...FACE_SHAPES.hybrid.grooves].sort((a, b) => a.y - b.y);
    expect(grooves).toHaveLength(7);
    const [top, ...rest] = grooves;
    const long = rest.slice(0, 5);
    const bottom = rest[5];
    for (const groove of long) expect(groove.to - groove.from).toBeGreaterThanOrEqual(55);
    expect(Math.abs(top.from - long[0].from)).toBeLessThanOrEqual(0.5);
    expect(top.to).toBeLessThan((long[0].from + long[0].to) / 2 + 3);
    expect(top.to - top.from).toBeLessThan(long[0].to - long[0].from - 20);
    expect(bottom.to - bottom.from).toBeLessThan(long[0].to - long[0].from - 15);
    expect(Math.abs(bottom.from + bottom.to - (long[0].from + long[0].to))).toBeLessThanOrEqual(1);
  });

  it.each([
    ['iron', 59.3],
    ['wedge', 55.2],
  ] as const)('raises the %s hosel up and to the heel at the photo’s neck angle', (category, lie) => {
    const { hosel } = FACE_SHAPES[category];
    const tube = polygon(hoselParts(hosel).tube);
    const n = tube.length / 2;
    const bottom = [(tube[0][0] + tube[2 * n - 1][0]) / 2, (tube[0][1] + tube[2 * n - 1][1]) / 2];
    const top = [(tube[n - 1][0] + tube[n][0]) / 2, (tube[n - 1][1] + tube[n][1]) / 2];
    expect(top[0]).toBeGreaterThan(bottom[0]);
    expect(Math.abs((Math.atan2(bottom[1] - top[1], top[0] - bottom[0]) * 180) / Math.PI - lie)).toBeLessThan(0.3);
  });

  it.each(['driver', 'fairway', 'hybrid'] as const)('fits the %s with an adjustable sleeve', (category) => {
    expect(FACE_SHAPES[category].finish).toBe('black');
    expect(hoselParts(FACE_SHAPES[category].hosel).rings.length).toBeGreaterThan(0);
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

  it('puts the TOE and HEEL labels just outside the club on the heel-toe line, inside the view', () => {
    for (const shape of Object.values(FACE_SHAPES)) {
      expect(shape.ends[0]).toBeLessThan(crossings(polygon(shape.head), 0)[0]);
      expect(shape.ends[1]).toBeGreaterThan(crossings(polygon(shape.head), 0).at(-1)!);
      expect(shape.ends[0] - 11).toBeGreaterThanOrEqual(shape.bounds.minX);
      expect(shape.ends[1] + 14).toBeLessThanOrEqual(shape.bounds.maxX);
    }
  });
});

const CATEGORIES = Object.keys(FACE_SHAPES) as FaceCategory[];
const WOODS = ['driver', 'fairway', 'hybrid'] as const;

describe('the drawings against the USGA Equipment Rules', () => {
  it('draws score lines no wider than the groove limit', () => {
    expect(GROOVE_WIDTH_MM).toBeLessThanOrEqual(USGA.grooveMaxWidthMm);
  });

  it.each(CATEGORIES)(
    'keeps the %s score lines straight and parallel, "width, spacing and cross-section ... consistent" (5c), lengths free',
    (category) => {
      const sets = new Map<string, number[]>();
      for (const groove of FACE_SHAPES[category].grooves) {
        expect(groove.to).toBeGreaterThan(groove.from);
        const key = groove.to < 0 ? 'toe' : groove.from > 0 ? 'heel' : 'centre';
        sets.set(key, [...(sets.get(key) ?? []), groove.y]);
      }
      for (const ys of sets.values()) {
        ys.sort((a, b) => a - b);
        const pitches = ys.slice(1).map((y, k) => y - ys[k]);
        for (const pitch of pitches) {
          expect(Math.abs(pitch - pitches[0])).toBeLessThanOrEqual(0.05);
          const separation = pitch - GROOVE_WIDTH_MM;
          expect(separation).toBeGreaterThanOrEqual(USGA.grooveSeparationPerWidth * GROOVE_WIDTH_MM);
          expect(separation).toBeGreaterThanOrEqual(USGA.grooveMinSeparationMm);
        }
      }
    }
  );

  it('keeps the driver heel and toe blocks out of the central impact strip around the face centre', () => {
    const half = USGA.woodImpactStripMm / 2;
    const blocks = FACE_SHAPES.driver.grooves.filter((groove) => groove.to < 0 || groove.from > 0);
    expect(blocks).toHaveLength(14);
    for (const groove of blocks) {
      const inner = groove.to < 0 ? -groove.to : groove.from;
      expect(half - inner).toBeLessThan(USGA.impactAreaEncroachMm);
    }
  });

  it('keeps the driver centre cross inside the decorative square around the face centre', () => {
    const half = USGA.centreMarkSquareMm / 2;
    for (const [x, y] of polygon(FACE_SHAPES.driver.mark!)) {
      expect(Math.abs(x)).toBeLessThanOrEqual(half);
      expect(Math.abs(y)).toBeLessThanOrEqual(half);
    }
  });

  it.each(WOODS)('keeps the %s head inside the wood heel-to-toe and sole-to-crown limits', (category) => {
    const head = extent(polygon(FACE_SHAPES[category].head));
    expect(head.maxX - head.minX).toBeLessThanOrEqual(USGA.woodHeelToToeMaxMm);
    expect(head.maxY - head.minY).toBeLessThanOrEqual(USGA.woodSoleToCrownMaxMm);
  });

  it.each(CATEGORIES)('fixes the %s shaft at the heel through a neck no longer than the limit', (category) => {
    const shape = FACE_SHAPES[category];
    const { hosel } = shape;
    const lie = (hosel.lieDeg * Math.PI) / 180;
    const joint = hosel.ground[0] + hosel.tube[0][0] * Math.cos(lie);
    expect(joint).toBeGreaterThan(Math.max(...shape.grooves.map((groove) => groove.to)));
    const sole = extent(polygon(shape.head)).maxY;
    const top = hosel.tube[hosel.tube.length - 1][0];
    const neck = top + hosel.ferruleLength + (sole - hosel.ground[1]) / Math.sin(lie);
    expect(neck).toBeLessThanOrEqual(USGA.neckToSoleMaxMm);
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
    expect(attr(html, 'face-impact__groove"', 'stroke-width')).toBe(String(GROOVE_WIDTH_MM));
    const fill = attr(html, 'face-impact__head"', 'fill');
    expect(fill).toMatch(/^url\(#.+\)$/);
    expect(html).toContain(`id="${fill!.slice(5, -1)}"`);
  });

  it('marks the centre of the driver face and keeps its score lines toward heel and toe', () => {
    const html = render(shot({ club: 'driver' }));
    expect(html).toContain('face-impact__mark');
    expect(html.match(/face-impact__groove"/g)).toHaveLength(FACE_SHAPES.driver.grooves.length);
  });

  it('draws the fairway sleeve band', () => {
    expect(render(shot({ club: '3-wood' }))).toContain('face-impact__collar');
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

  it('always reads in millimetres, with centre lines and no grid', () => {
    const html = render(shot());
    expect(html).toContain('Toe 8 mm');
    expect(html).toContain('Illustrative face');
    expect(html).not.toContain('grid');
    expect(html).not.toContain(' in ');
    expect(html.match(/face-impact__centre-line"/g)).toHaveLength(2);
  });

  it.each(
    (Object.keys(FACE_SHAPES) as FaceCategory[]).flatMap((category) =>
      (['right', 'left'] as const).flatMap((hand) => [
        [category, hand, 9, 4],
        [category, hand, -6.3, -2.7],
        [category, hand, 8.64, -0.46],
      ])
    )
  )('places a %s %s-handed strike (%f, %f) at its mm against the centre lines', (category, hand, offset, height) => {
    const club = { driver: 'driver', fairway: '3-wood', hybrid: '4-hybrid', iron: '7-iron', wedge: 'sw' }[category];
    const html = render(
      shot(
        { club, impact_location: { status: 'ok', handedness: hand } },
        { impact_offset: reading(offset, 4), impact_height: reading(height, 5) }
      )
    );
    const lines = [...html.matchAll(/<line[^>]*face-impact__centre-line[^>]*>/g)].map((tag) => {
      const value = (name: string) => Number(tag[0].match(new RegExp(` ${name}="([^"]*)"`))![1]);
      return { x1: value('x1'), x2: value('x2'), y1: value('y1'), y2: value('y2') };
    });
    const across = lines.find((line) => line.y1 === line.y2)!;
    const upright = lines.find((line) => line.x1 === line.x2)!;
    const dx = Number(attr(html, 'face-impact__dot', 'cx')) - upright.x1;
    const dy = Number(attr(html, 'face-impact__dot', 'cy')) - across.y1;
    const toeward = hand === 'left' ? dx : -dx;
    expect(Math.abs(toeward - offset)).toBeLessThanOrEqual(0.1);
    expect(Math.abs(-dy - height)).toBeLessThanOrEqual(0.1);
    expect(Number(attr(html, 'face-impact__ellipse', 'rx'))).toBeCloseTo(4, 5);
    expect(Number(attr(html, 'face-impact__ellipse', 'ry'))).toBeCloseTo(5, 5);
    const toe = html.indexOf('>Toe<') > -1;
    expect(toe).toBe(true);
  });

  it('labels the rounded mm but places the dot at the unrounded value', () => {
    const html = render(shot({}, { impact_offset: reading(8.64, 4), impact_height: reading(-0.46, 5) }));
    expect(html).toContain('Toe 9 mm');
    expect(attr(html, 'face-impact__dot', 'cx')).toBe('-8.64');
    expect(attr(html, 'face-impact__dot', 'cy')).toBe('0.46');
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
