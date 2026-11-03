import { renderToString } from 'react-dom/server';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import type { Shot, ShotMetric } from '../../types/shot';
import { FaceImpactView } from './FaceImpactView';
import {
  BLAST_EDGE_OUTSIDE_MM,
  BLAST_FIELD_LIFT_MM,
  BLAST_SOFT_EDGE_MM,
  BLASTED_GROOVE_WIDTH_MM,
  FACE_SHAPES,
  bladeCrotch,
  blastedField,
  clubModel,
  faceCategory,
  faceCentre,
  GROOVE_WIDTH_MM,
  hoselParts,
  impactReading,
  USGA,
  type FaceCategory,
} from './faceImpact';
import { MetricGroupPanel } from './MetricGroupPanel';
import { ALL_CLUBS } from '../../data/clubs';

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

  /* Face height at the centre (bottom edge to top line) and a wood face's width are population
   * means (docs/camera/impact-location.md); a blade's face width follows its head. */
  it.each([
    ['driver', 110, 50.4],
    ['fairway', 90, 30],
    ['hybrid', 82, 28.2],
    ['iron', 75.6, 44.4],
    ['wedge', 74.6, 51.8],
  ] as const)('draws the %s face %f mm wide and %f mm tall at the centre', (category, width, height) => {
    const face = polygon(FACE_SHAPES[category].face);
    const box = extent(face);
    expect(Math.abs(box.maxX - box.minX - width)).toBeLessThanOrEqual(0.5);
    const [top, bottom] = span(face, 0);
    expect(Math.abs(bottom - top - height)).toBeLessThanOrEqual(0.1);
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
    ['iron', 13, 6, 3.46, 4.3, 53.6, 17.4, [5, 9]],
    ['wedge', 18, 8, 2.98, 3.6, 52.2, 18.9, [5, 9]],
  ] as const)(
    'lays the %s score lines out as the population: %i lines, the top %i stepped under the topline',
    (category, count, stepped, pitch, lowest, length, toeMargin, heelMargin) => {
      const shape = FACE_SHAPES[category];
      const face = polygon(shape.face);
      const grooves = [...shape.grooves].sort((a, b) => b.y - a.y);
      expect(grooves).toHaveLength(count);
      const toeEnds = grooves.map((groove) => groove.from);
      expect(Math.max(...toeEnds) - Math.min(...toeEnds)).toBeLessThanOrEqual(0.1);
      const pitches = grooves.slice(1).map((groove, k) => grooves[k].y - groove.y);
      for (const gap of pitches) expect(Math.abs(gap - pitch)).toBeLessThanOrEqual(0.01);
      expect(Math.abs(span(face, 0)[1] - grooves[0].y - lowest)).toBeLessThanOrEqual(0.05);
      const heelEnd = Math.max(...grooves.map((groove) => groove.to));
      const steps = grooves.filter((groove) => groove.to < heelEnd - 0.05);
      expect(steps).toHaveLength(stepped);
      steps.forEach((groove, k) => {
        const under = groove.y - span(face, groove.to)[0];
        expect(under).toBeGreaterThanOrEqual(2.5);
        expect(under).toBeLessThanOrEqual(6.5);
        if (k) expect(groove.to).toBeLessThan(steps[k - 1].to);
      });
      expect(Math.abs(heelEnd - grooves[0].from - length)).toBeLessThanOrEqual(0.05);
      expect(Math.abs(grooves[0].from - extent(polygon(shape.head)).minX - toeMargin)).toBeLessThanOrEqual(0.1);
      const across = crossings(face, grooves[0].y);
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

  it('gives the driver a plain centre with three bars, the middle one longest, the cross on the computed face centre', () => {
    const shape = FACE_SHAPES.driver;
    const bars = shape.grooves.filter((groove) => groove.from < 0 && groove.to > 0).sort((a, b) => a.y - b.y);
    expect(bars).toHaveLength(3);
    const length = bars.map((bar) => bar.to - bar.from);
    expect(length[1]).toBeGreaterThan(length[0] + 8);
    expect(length[1]).toBeGreaterThan(length[2] + 8);
    const cross = polygon(shape.mark!);
    for (const point of cross) expect(Math.abs(point[0])).toBeLessThanOrEqual(0.05);
    const ys = cross.map((point) => point[1]);
    expect(Math.abs((Math.min(...ys) + Math.max(...ys)) / 2)).toBeLessThanOrEqual(0.05);
    expect(Math.abs(bars[1].y)).toBeLessThanOrEqual(0.05);
    expect(Math.abs((bars[1].from + bars[1].to) / 2)).toBeLessThanOrEqual(0.05);
  });

  /* Population 3-wood: 5 lines at 4.7 mm, the longest (74) the top or second, shortening toward
   * the sole to 0.54 of it, the lowest 5.3 above the face bottom. */
  it('gives the fairway five lines, the longest 74 mm near the top, shortening toward the sole', () => {
    const shape = FACE_SHAPES.fairway;
    const face = polygon(shape.face);
    const grooves = [...shape.grooves].sort((a, b) => a.y - b.y);
    expect(grooves).toHaveLength(5);
    expect(shape.mark).toBeNull();
    const lengths = grooves.map((groove) => groove.to - groove.from);
    const longest = Math.max(...lengths);
    expect(longest).toBeCloseTo(74, 1);
    expect(lengths.indexOf(longest)).toBeLessThanOrEqual(1);
    for (let k = 2; k < 5; k += 1) expect(lengths[k]).toBeLessThan(lengths[k - 1]);
    expect(lengths[4] / longest).toBeCloseTo(0.54, 2);
    expect(Math.abs(span(face, 0)[1] - grooves[4].y - 5.3)).toBeLessThanOrEqual(0.05);
    for (const groove of grooves) {
      const across = crossings(face, groove.y);
      expect(groove.from - across[0]).toBeGreaterThanOrEqual(4);
      expect(across[across.length - 1] - groove.to).toBeGreaterThanOrEqual(4);
    }
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

  /* Population 3/4-hybrid: 6 lines at 3.5 mm, the longest 65, the lowest 0.55 of it and 4.8 above
   * the face bottom. */
  it('gives the hybrid six lines, the longest 65 mm, shortening toward the sole', () => {
    const face = polygon(FACE_SHAPES.hybrid.face);
    const grooves = [...FACE_SHAPES.hybrid.grooves].sort((a, b) => a.y - b.y);
    expect(grooves).toHaveLength(6);
    const lengths = grooves.map((groove) => groove.to - groove.from);
    const longest = Math.max(...lengths);
    expect(longest).toBeCloseTo(65, 1);
    expect(lengths[5] / longest).toBeCloseTo(0.55, 2);
    for (let k = 2; k < 6; k += 1) expect(lengths[k]).toBeLessThan(lengths[k - 1]);
    expect(Math.abs(span(face, 0)[1] - grooves[5].y - 4.8)).toBeLessThanOrEqual(0.05);
    for (const groove of grooves) {
      const across = crossings(face, groove.y);
      expect(groove.from - across[0]).toBeGreaterThanOrEqual(4);
      expect(across[across.length - 1] - groove.to).toBeGreaterThanOrEqual(4);
    }
  });

  // The population's mean loft and lie (docs/camera/impact-location.md); seen along the face
  // normal the hosel axis rises at atan(tan(lie) * cos(loft)).
  it.each([
    ['driver', 10.4, 57.4],
    ['fairway', 14.9, 56.6],
    ['hybrid', 18.8, 57.7],
    ['iron', 31.4, 62.3],
    ['wedge', 56.3, 64],
  ] as const)(
    'raises the %s hosel up and to the heel as seen along the face normal (loft %f, lie %f)',
    (category, loft, lie) => {
      const { hosel } = FACE_SHAPES[category];
      const rad = Math.PI / 180;
      const seen = Math.atan(Math.tan(lie * rad) * Math.cos(loft * rad)) / rad;
      const tube = polygon(hoselParts(hosel).tube);
      const n = tube.length / 2;
      const bottom = [(tube[0][0] + tube[2 * n - 1][0]) / 2, (tube[0][1] + tube[2 * n - 1][1]) / 2];
      const top = [(tube[n - 1][0] + tube[n][0]) / 2, (tube[n - 1][1] + tube[n][1]) / 2];
      expect(top[0]).toBeGreaterThan(bottom[0]);
      expect(Math.abs((Math.atan2(bottom[1] - top[1], top[0] - bottom[0]) * 180) / Math.PI - seen)).toBeLessThan(0.5);
    }
  );

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

  /* 4b(i): heel to toe from the toe extreme to the heel at 0.875 in (22.23 mm) above the ground
   * (Appendix II); the population's R&A head lengths are 117 (driver), 105 and 101 mm. The drawn
   * heads rim their faces with an even band, so their toes sit 5-8 mm inside the population's. */
  it.each([
    ['driver', 116.8],
    ['fairway', 105.3],
    ['hybrid', 101],
  ] as const)(
    'keeps the %s head inside the wood heel-to-toe and sole-to-crown limits, near its population length',
    (category, length) => {
      const head = polygon(FACE_SHAPES[category].head);
      const box = extent(head);
      const heel = Math.max(...crossings(head, box.maxY - USGA.woodHeelPointHeightMm));
      expect(heel - box.minX).toBeLessThanOrEqual(USGA.woodHeelToToeMaxMm);
      expect(Math.abs(heel - box.minX - length)).toBeLessThanOrEqual(10);
      expect(box.maxY - box.minY).toBeLessThanOrEqual(USGA.woodSoleToCrownMaxMm);
    }
  );

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
  it.each(['right', 'left'] as const)(
    'centres the illustrative contact mark on the unrounded %s-handed reading',
    (hand) => {
      const html = render(
        shot(
          { impact_location: { status: 'ok', handedness: hand } },
          { impact_offset: reading(8.64, 4), impact_height: reading(-0.46, 5) }
        )
      );
      expect(attr(html, 'face-impact__imprint"', 'transform')).toBe(
        `translate(${hand === 'left' ? 8.64 : -8.64} 0.46) scale(${hand === 'left' ? -1 : 1} 1)`
      );
      const clip = attr(html, 'face-impact__contact"', 'clip-path');
      expect(clip).toMatch(/^url\(#.+\)$/);
      expect(html).toContain(`id="${clip!.slice(5, -1)}"`);
      expect(html).toContain('Illustrative face and contact mark');
      // the label is the rounded mm; the dot sits at the unrounded value
      expect(html).toContain('Toe 9 mm');
      expect(attr(html, 'face-impact__dot', 'cx')).toBe(hand === 'left' ? '8.64' : '-8.64');
    }
  );

  it.each(['right', 'left'] as const)(
    'marks the %s-handed reading with the imprint, the 1-sigma ellipse and the anchor only: no cross',
    (hand) => {
      const html = render(
        shot(
          { impact_location: { status: 'ok', handedness: hand } },
          { impact_offset: reading(8.64, 4), impact_height: reading(-0.46, 5) }
        )
      );
      expect(html).toContain('face-impact__imprint');
      expect(html).toContain('face-impact__ellipse');
      expect(html).toContain('face-impact__dot');
      expect(html).not.toContain('face-impact__cross');
    }
  );

  it('keeps the decorative contact shape independent of measurement uncertainty', () => {
    const narrow = render(shot({}, { impact_offset: reading(8, 1), impact_height: reading(3, 2) }));
    const wide = render(shot({}, { impact_offset: reading(8, 7), impact_height: reading(3, 9) }));
    expect(attr(narrow, 'face-impact__contact-shape"', 'd')).toBeTruthy();
    expect(attr(narrow, 'face-impact__contact-shape"', 'd')).toBe(attr(wide, 'face-impact__contact-shape"', 'd'));
    expect(attr(narrow, 'face-impact__ellipse', 'rx')).toBe('1');
    expect(attr(wide, 'face-impact__ellipse', 'rx')).toBe('7');
  });

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
    // always millimetres, with centre lines and no grid
    expect(html).not.toContain('grid');
    expect(html).not.toContain(' in ');
    expect(html.match(/face-impact__centre-line"/g)).toHaveLength(2);
  });

  it('draws the club as a filled silhouette: head, blasted field, hosel, ferrule, shaft and score lines', () => {
    const html = render(shot());
    for (const part of ['head', 'field', 'hosel', 'ferrule', 'shaft']) expect(html).toContain(`face-impact__${part}"`);
    expect(html.match(/face-impact__groove"/g)).toHaveLength(FACE_SHAPES.iron.grooves.length);
    expect(attr(html, 'face-impact__groove"', 'stroke-width')).toBe(String(BLASTED_GROOVE_WIDTH_MM));
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
    expect(html.indexOf('>Toe<')).toBeGreaterThan(-1);
    expect(html).toContain(`data-face="${category}"`);
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
    expect(html).not.toContain('face-impact__cross');
    expect(html).not.toContain('class="face-impact__contact"');
  });

  it('keeps the heel-toe reading when only the height was refused', () => {
    const html = render(shot({}, { impact_height: record({ reason: 'no sole line at contact' }) }));
    expect(html).toContain('face-impact__dot');
    expect(html).toContain('High/low not measured: no sole line at contact');
    expect(attr(html, 'face-impact__dot', 'cy')).toBe('0');
    expect(html).not.toContain('class="face-impact__contact"');
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

/* An iron's or wedge's face: one chrome head, and a sand-blasted groove field (the owner's reference
 * photo and approved mock-up v3, 2026-11-03) between two vertical edge lines 1.25 mm outside the
 * longest scorelines, across the whole head from its top edge to its bottom edge. */
describe('the blasted groove field', () => {
  const blades = ['iron', 'wedge'] as const;
  const bladeHands = blades.flatMap((category) =>
    (['right', 'left'] as const).map((hand) => [category, hand] as const)
  );
  const club = { iron: '7-iron', wedge: 'sw' };
  const html = (category: 'iron' | 'wedge', hand: 'right' | 'left') =>
    render(shot({ club: club[category], impact_location: { handedness: hand } } as Partial<Shot>));
  const tags = (markup: string, cls: string) =>
    [...markup.matchAll(new RegExp(`<[^>]*class="${cls}"[^>]*>`, 'g'))].map((m) => m[0]);
  const num = (tag: string, name: string) => Number(tag.match(new RegExp(` ${name}="([^"]*)"`))![1]);

  it.each(blades)('puts the %s edge lines 1.25 mm outside the longest scorelines', (category) => {
    const ends = FACE_SHAPES[category].grooves.flatMap((g) => [g.from, g.to]);
    expect(BLAST_EDGE_OUTSIDE_MM).toBe(1.25);
    expect(blastedField(category)).toEqual({
      x0: Math.min(...ends) - BLAST_EDGE_OUTSIDE_MM,
      x1: Math.max(...ends) + BLAST_EDGE_OUTSIDE_MM,
    });
  });

  it.each(bladeHands)(
    'draws the %s field softly between its edge lines, clipped to the head and lifted off its bottom (%s-handed)',
    (category, hand) => {
      const markup = html(category, hand);
      const { x0, x1 } = blastedField(category)!;
      // no stroke lines at its ends: the matte blends into the chrome over BLAST_SOFT_EDGE_MM
      expect(markup).not.toContain('face-impact__field-edge');
      expect(markup).not.toContain('face-impact__field-lip');
      const soft = BLAST_SOFT_EDGE_MM;
      expect(soft).toBe(0.8);
      const { minY, maxY } = FACE_SHAPES[category].bounds;
      const [fill] = tags(markup, 'face-impact__field-fill');
      expect([num(fill, 'x'), num(fill, 'width'), num(fill, 'y'), num(fill, 'height')]).toEqual([
        x0 - soft / 2,
        x1 - x0 + soft,
        minY,
        maxY - minY,
      ]);
      const ramp = markup.match(/<linearGradient id="[^"]*field-ramp"[^>]*>(.*?)<\/linearGradient>/)!;
      expect(num(ramp[0], 'x1')).toBe(x0 - soft / 2);
      expect(num(ramp[0], 'x2')).toBe(x1 + soft / 2);
      const span = x1 - x0 + soft;
      expect([...ramp[1].matchAll(/offset="([^"]*)"/g)].map((m) => Number(m[1]))).toEqual([
        0,
        soft / span,
        1 - soft / span,
        1,
      ]);
      // a faint shadow on the matte side and a faint highlight on the chrome side of each end
      expect(tags(markup, 'face-impact__field-shadow').map((tag) => [num(tag, 'x'), num(tag, 'width')])).toEqual([
        [x0, 0.9],
        [x1 - 0.9, 0.9],
      ]);
      expect(tags(markup, 'face-impact__field-shine').map((tag) => [num(tag, 'x'), num(tag, 'width')])).toEqual([
        [x0 - 1.4, 1],
        [x1 + 0.4, 1],
      ]);
      // cut to the head's own outline: the top edge as before ...
      const clipOf = (tag: string) => tag.match(/clip-path="url\(#([^)]*)\)"/)![1];
      const clipPath = (clip: string) => markup.match(new RegExp(`<clipPath id="${clip}"><path ([^>]*)>`))![1];
      const [field] = tags(markup, 'face-impact__field');
      expect(clipPath(clipOf(field))).toBe(`d="${FACE_SHAPES[category].head}"`);
      // ... and the bottom lifted 1.2 mm, so the head's rounded leading edge shows under it
      expect(BLAST_FIELD_LIFT_MM).toBe(1.2);
      const [lift] = tags(markup, 'face-impact__field-lift');
      expect(clipPath(clipOf(lift))).toBe(`d="${FACE_SHAPES[category].head}" transform="translate(0 -1.2)"`);
      expect(markup.indexOf('face-impact__field-lift"')).toBeLessThan(markup.indexOf('face-impact__field"'));
      // drawn inside the mirrored head drawing, in the face's own frame, before the centre lines
      expect(markup.indexOf('face-impact__field"')).toBeGreaterThan(markup.indexOf('face-impact__outline-group'));
      expect(markup.indexOf('face-impact__field"')).toBeLessThan(markup.indexOf('face-impact__centre-lines'));
      expect(markup).toContain('face-impact__face-marks face-impact__face-marks--blasted');
    }
  );

  it.each(bladeHands)('shades the %s head edge as a radius, lit from the upper left (%s-handed)', (category, hand) => {
    const markup = html(category, hand);
    const shape = FACE_SHAPES[category];
    const [band] = tags(markup, 'face-impact__edge-radius');
    // along the outer outline of head and hosel together, clipped to it, kept off the round tube
    expect(band).toContain(`clip-path="url(#`);
    const clip = band.match(/clip-path="url\(#([^)]*)\)"/)![1];
    expect(markup).toContain(`<clipPath id="${clip}"><path d="${shape.silhouette}"`);
    const mask = band.match(/mask="url\(#([^)]*)\)"/)![1];
    expect(markup.match(new RegExp(`<mask id="${mask}"[^>]*>.*?</mask>`))![0]).toContain('fill="#000"');
    const strokes = [
      ...markup.matchAll(/<path class="face-impact__edge-radius-stroke" d="([^"]*)"[^>]*stroke-width="([^"]*)"/g),
    ];
    expect(strokes.map((m) => Number(m[2]))).toEqual([4.6, 3.4, 2.2, 1.1]);
    for (const m of strokes) expect(m[1]).toBe(shape.silhouette);
    // and a bright rounded leading edge just under the lifted field
    const [lead] = tags(markup, 'face-impact__leading-edge');
    expect(lead).toContain(`d="${shape.head}"`);
    expect(lead).toContain('transform="translate(0 -0.6)"');
    // the light comes from the screen's upper left whichever hand
    const lit = markup.match(/<linearGradient id="[^"]*edge-lit"[^>]*>/)![0];
    const left = hand === 'left';
    expect(num(lit, 'x1') < num(lit, 'x2')).toBe(!left);
    expect(num(lit, 'y1')).toBeLessThan(num(lit, 'y2'));
  });

  it.each(blades)('darkens the %s hosel-to-head crotch with a soft reflection', (category) => {
    const markup = html(category, 'right');
    const { x1 } = blastedField(category)!;
    const [cx, cy] = bladeCrotch(category)!;
    // on the heel side of the field, the lowest point of the head's top edge between topline and hosel
    expect(cx).toBeGreaterThan(x1);
    expect(cx).toBeLessThan(x1 + 25);
    const head = polygon(FACE_SHAPES[category].head);
    const topAt = (x: number) =>
      Math.min(
        ...head.flatMap(([ax, ay], i) => {
          const [bx, by] = head[(i + 1) % head.length];
          if ((ax - x) * (bx - x) > 0 || ax === bx) return [];
          return [ay + ((x - ax) / (bx - ax)) * (by - ay)];
        })
      );
    expect(cy).toBeCloseTo(topAt(cx), 1);
    for (let x = x1 + 0.5; x < x1 + 25; x += 0.5) expect(topAt(x)).toBeLessThanOrEqual(cy + 0.05);
    const [shade] = tags(markup, 'face-impact__neck-shade');
    expect([num(shade, 'cx'), num(shade, 'cy'), num(shade, 'r')]).toEqual([cx, cy + 2.5, 9]);
  });

  it.each(blades)('draws the %s scorelines as recessed grooves, in place', (category) => {
    const markup = html(category, 'right');
    const grooves = FACE_SHAPES[category].grooves;
    const dark = tags(markup, 'face-impact__groove');
    expect(dark).toHaveLength(grooves.length);
    expect(BLASTED_GROOVE_WIDTH_MM).toBeCloseTo(GROOVE_WIDTH_MM * 0.85, 12);
    for (const [k, g] of grooves.entries()) {
      const [a, b] = [Math.min(g.from, g.to), Math.max(g.from, g.to)];
      expect([num(dark[k], 'x1'), num(dark[k], 'x2'), num(dark[k], 'y1')]).toEqual([g.from, g.to, g.y]);
      expect(num(dark[k], 'stroke-width')).toBe(BLASTED_GROOVE_WIDTH_MM);
      // a hairline light lower edge, tapered in at the ends, and a darker core
      const lip = tags(markup, 'face-impact__groove-lip')[k];
      expect([num(lip, 'x1'), num(lip, 'x2'), num(lip, 'y1')]).toEqual([
        a + 0.35,
        b - 0.35,
        g.y + GROOVE_WIDTH_MM * 0.62,
      ]);
      const core = tags(markup, 'face-impact__groove-core')[k];
      expect([num(core, 'x1'), num(core, 'x2'), num(core, 'y1')]).toEqual([
        a + 0.2,
        b - 0.2,
        g.y - GROOVE_WIDTH_MM * 0.1,
      ]);
    }
  });

  it.each(bladeHands)('leaves no separate face panel on the %s (%s-handed): one chrome head', (category, hand) => {
    const markup = html(category, hand);
    for (const panel of ['face', 'face-reflection', 'grain', 'scoring', 'edge'])
      expect(markup).not.toContain(`class="face-impact__${panel}"`);
    // the head, its reflection and its rim highlights stay
    for (const part of ['head', 'head-reflection', 'leading-radius', 'rim'])
      expect(markup).toContain(`face-impact__${part}"`);
  });

  it.each(['driver', 'fairway', 'hybrid'] as const)('keeps the %s face panel and draws no field', (category) => {
    expect(blastedField(category)).toBeNull();
    const markup = render(shot({ club: { driver: 'driver', fairway: '3-wood', hybrid: '5-hybrid' }[category] }));
    expect(markup).toContain('class="face-impact__face"');
    expect(markup).not.toContain('face-impact__field');
  });

  /** WCAG contrast ratio of two #rrggbb colours. */
  const contrast = (a: string, b: string) => {
    const lum = (hex: string) => {
      const [r, g, bl] = [1, 3, 5].map((i) => {
        const c = parseInt(hex.slice(i, i + 2), 16) / 255;
        return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * r + 0.7152 * g + 0.0722 * bl;
    };
    const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  };
  const css = readFileSync(new URL('./FaceImpactView.css', import.meta.url), 'utf8');
  const theme = readFileSync(new URL('../../index.css', import.meta.url), 'utf8');
  const block = (text: string, selector: string) => text.slice(text.indexOf(selector)).split('}')[0];
  const value = (text: string, name: string) => text.match(new RegExp(`${name}:\\s*(#[0-9a-f]{6})`, 'i'))![1];

  it.each(['chrome', 'satin'])('reads on the %s finish in the dark and the light theme', (finish) => {
    const art = block(css, `[data-finish='${finish}']`);
    const blast = value(art, '--face-art-blast');
    // the grooves stand out on the matte field, a shade darker than on the polished face
    expect(contrast(blast, value(art, '--face-art-blast-groove'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(value(art, '--face-art-blast-groove'), '#ffffff')).toBeGreaterThan(
      contrast(value(art, '--face-art-groove'), '#ffffff')
    );
    // the field reaches the head's edge: on the dark panel it reads by itself, on the light panel
    // by its rim line
    const dark = value(block(theme, "[data-theme='dark']"), '--color-surface');
    const light = value(block(theme, "[data-theme='light']"), '--color-surface');
    expect(contrast(blast, dark)).toBeGreaterThanOrEqual(3);
    const rim = value(block(css, "[data-theme='light'] .face-impact"), '--face-art-blast-rim');
    expect(contrast(rim, light)).toBeGreaterThanOrEqual(3);
  });

  /* The face view as drawn, every club, both hands, no shot: sha-256 of its markup. Woods and
   * hybrids as before the blasted field; irons and wedges since it. */
  const APPROVED_FACE_VIEWS: Record<string, string> = {
    '2-iron:right': 'e9bd50083ab762df',
    '2-iron:left': '2d990556c6ba5c8a',
    '3-iron:right': 'e9bd50083ab762df',
    '3-iron:left': '2d990556c6ba5c8a',
    '4-iron:right': 'e9bd50083ab762df',
    '4-iron:left': '2d990556c6ba5c8a',
    '5-iron:right': 'e9bd50083ab762df',
    '5-iron:left': '2d990556c6ba5c8a',
    '6-iron:right': 'e9bd50083ab762df',
    '6-iron:left': '2d990556c6ba5c8a',
    '7-iron:right': 'e9bd50083ab762df',
    '7-iron:left': '2d990556c6ba5c8a',
    '8-iron:right': 'e9bd50083ab762df',
    '8-iron:left': '2d990556c6ba5c8a',
    '9-iron:right': 'e9bd50083ab762df',
    '9-iron:left': '2d990556c6ba5c8a',
    'pw:right': '4c8e0ecf3d4d81e7',
    'pw:left': '0708f28e61c7154b',
    'gw:right': '4c8e0ecf3d4d81e7',
    'gw:left': '0708f28e61c7154b',
    'sw:right': '4c8e0ecf3d4d81e7',
    'sw:left': '0708f28e61c7154b',
    'lw:right': '4c8e0ecf3d4d81e7',
    'lw:left': '0708f28e61c7154b',
    '3-hybrid:right': '2c47b8c3a51c7d03',
    '3-hybrid:left': 'ab18ad44899b8a7c',
    '5-hybrid:right': '2c47b8c3a51c7d03',
    '5-hybrid:left': 'ab18ad44899b8a7c',
    '7-hybrid:right': '2c47b8c3a51c7d03',
    '7-hybrid:left': 'ab18ad44899b8a7c',
    '9-hybrid:right': '2c47b8c3a51c7d03',
    '9-hybrid:left': 'ab18ad44899b8a7c',
    'driver:right': '604308b49492be66',
    'driver:left': '850929eeb337e6fb',
    '3-wood:right': '7c8bb63b3765c3a6',
    '3-wood:left': 'e3dd7ecc2f41ba1e',
    '5-wood:right': '7c8bb63b3765c3a6',
    '5-wood:left': 'e3dd7ecc2f41ba1e',
    '7-wood:right': '7c8bb63b3765c3a6',
    '7-wood:left': 'e3dd7ecc2f41ba1e',
  };
  it('pins every selectable club in the approved face views', () => {
    expect(Object.keys(APPROVED_FACE_VIEWS).sort()).toEqual(
      ALL_CLUBS.flatMap((c) => [`${c.id}:left`, `${c.id}:right`]).sort()
    );
  });
  it.each(Object.keys(APPROVED_FACE_VIEWS))('keeps the %s face view byte-identical to the approved drawing', (key) => {
    const [id, handed] = key.split(':') as [string, 'right' | 'left'];
    const s = { ...clubModel(id), impact_location: { handedness: handed } } as unknown as Shot;
    const markup = renderToString(<FaceImpactView shot={s} sessionShots={[s]} />);
    expect(createHash('sha256').update(markup).digest('hex').slice(0, 16)).toBe(APPROVED_FACE_VIEWS[key]);
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
