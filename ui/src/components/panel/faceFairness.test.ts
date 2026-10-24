import { describe, expect, it } from 'vitest';
import { FACE_SHAPES, type FaceCategory } from './faceImpact';

/* The outlines are a few fair curves: C2 splines between designed breaks (corners, straight
 * stretches, the joins into the tube). These checks sample curvature along each drawn path and
 * fail on wobble: unintended inflections, kinks, curvature spikes and curvature jumps at joins.
 * Segments shorter than VISIBLE_MM (the closures hidden inside the tube) are left out: their
 * control points, rounded to 0.01 mm, carry no visible shape. */

type Point = [number, number];
type Bezier = [Point, Point, Point, Point];

const VISIBLE_MM = 2;
const FLAT = 0.005; // curvature (1/mm) below which a stretch counts as straight for inflections
const STRAIGHT_MM = 0.02; // control points this close to the chord: a designed straight line
const MIN_RADIUS_MM = 3;
const MAX_KINK_DEG = 0.5;
const MAX_CURVATURE_JUMP = 0.01; // 1/mm, at a smooth join between two curved segments

function beziers(d: string): Bezier[] {
  const tokens = d.match(/[MLCZ]|-?\d*\.?\d+/g) ?? [];
  const out: Bezier[] = [];
  let command = '';
  let current: Point = [0, 0];
  for (let i = 0; i < tokens.length;) {
    if (/[MLCZ]/.test(tokens[i])) command = tokens[i++];
    if (command === 'Z') continue;
    const take = (): Point => [Number(tokens[i++]), Number(tokens[i++])];
    if (command === 'M') current = take();
    else if (command === 'L') {
      const p = take();
      const third = (k: number): Point => [
        current[0] + ((p[0] - current[0]) * k) / 3,
        current[1] + ((p[1] - current[1]) * k) / 3,
      ];
      out.push([current, third(1), third(2), p]);
      current = p;
    } else {
      const [c1, c2, p] = [take(), take(), take()];
      out.push([current, c1, c2, p]);
      current = p;
    }
  }
  return out;
}

/** Curvature (1/mm, signed) and tangent direction at t. */
function local([p0, c1, c2, p3]: Bezier, t: number): { k: number; angle: number } {
  const s = 1 - t;
  const d1 = [0, 1].map((i) => 3 * (s * s * (c1[i] - p0[i]) + 2 * s * t * (c2[i] - c1[i]) + t * t * (p3[i] - c2[i])));
  const d2 = [0, 1].map((i) => 6 * (s * (c2[i] - 2 * c1[i] + p0[i]) + t * (p3[i] - 2 * c2[i] + c1[i])));
  const speed = Math.hypot(d1[0], d1[1]);
  return { k: (d1[0] * d2[1] - d1[1] * d2[0]) / speed ** 3, angle: Math.atan2(d1[1], d1[0]) };
}

interface Fairness {
  inflections: number;
  kinks: number;
  minRadius: number;
  maxJump: number;
}

function fairness(d: string): Fairness {
  const segments = beziers(d);
  const n = segments.length;
  const visible = segments.map(([a, , , b]) => Math.hypot(b[0] - a[0], b[1] - a[1]) >= VISIBLE_MM);
  const samples = segments.map((segment) => Array.from({ length: 41 }, (_, k) => local(segment, k / 40).k));
  const straight = segments.map(([a, c1, c2, b]) => {
    const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
    const off = (c: Point) => Math.abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / len;
    return Math.max(off(c1), off(c2)) < STRAIGHT_MM;
  });
  const signs = segments
    .flatMap((_, i) => (visible[i] ? samples[i].slice(0, -1) : []))
    .filter((k) => Math.abs(k) > FLAT)
    .map(Math.sign);
  let inflections = signs.slice(1).filter((sign, i) => sign !== signs[i]).length;
  if (signs.length && signs[0] !== signs[signs.length - 1]) inflections += 1;
  let kinks = 0;
  let maxJump = 0;
  for (let i = 0; i < n; i += 1) {
    const j = (i + 1) % n;
    if (!visible[i] || !visible[j]) continue;
    const turn = Math.abs(
      ((((local(segments[j], 0).angle - local(segments[i], 1).angle) * 180) / Math.PI + 540) % 360) - 180
    );
    if (turn > MAX_KINK_DEG) kinks += 1;
    else if (!straight[i] && !straight[j]) maxJump = Math.max(maxJump, Math.abs(samples[i][40] - samples[j][0]));
  }
  const curvatures = segments.flatMap((_, i) => (visible[i] ? samples[i] : [])).map(Math.abs);
  return { inflections, kinks, minRadius: 1 / Math.max(...curvatures), maxJump };
}

/** Points along the path, `per` to a segment. */
function samples(d: string, per = 24): Point[] {
  return beziers(d).flatMap(([p0, c1, c2, p3]) =>
    Array.from({ length: per }, (_, k): Point => {
      const t = k / per;
      const [a, b, c, e] = [(1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t ** 2, t ** 3];
      return [a * p0[0] + b * c1[0] + c * c2[0] + e * p3[0], a * p0[1] + b * c1[1] + c * c2[1] + e * p3[1]];
    })
  );
}

/** Distance from p to the closed polyline. */
function distance(p: Point, polyline: Point[]): number {
  let best = Infinity;
  polyline.forEach((a, i) => {
    const b = polyline[(i + 1) % polyline.length];
    const [dx, dy] = [b[0] - a[0], b[1] - a[1]];
    const t = Math.min(1, Math.max(0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy || 1)));
    best = Math.min(best, Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy));
  });
  return best;
}

/* The head rims the face with a band of one width all the way round, as on a real club, except at
 * the heel end where the band runs into the neck: these polar angles about the face centre (deg,
 * 0 heel-ward, 90 up; the drawings are centred on it). */
const NECK_DEG: Record<FaceCategory, [number, number]> = {
  driver: [-8, 45],
  fairway: [-2, 43],
  hybrid: [-6, 39],
  iron: [-32, 8],
  wedge: [-42, 12],
};
const MAX_BAND_RATIO = 1.25;

const CATEGORIES = Object.keys(FACE_SHAPES) as FaceCategory[];
const BLADES: readonly FaceCategory[] = ['iron', 'wedge'];

describe('fair outlines', () => {
  it.each(CATEGORIES)('draws the %s head as fair curves: only the crotch turns inward', (category) => {
    for (const d of [FACE_SHAPES[category].head, FACE_SHAPES[category].silhouette]) {
      const fair = fairness(d);
      expect(fair.inflections).toBe(2);
      expect(fair.minRadius).toBeGreaterThanOrEqual(MIN_RADIUS_MM);
      expect(fair.maxJump).toBeLessThanOrEqual(MAX_CURVATURE_JUMP);
    }
    expect(fairness(FACE_SHAPES[category].head).kinks).toBe(0);
  });

  it.each(CATEGORIES)('rims the %s face with an even band, outside the neck', (category) => {
    const head = samples(FACE_SHAPES[category].head);
    const [from, to] = NECK_DEG[category];
    const band = samples(FACE_SHAPES[category].face)
      .filter(([x, y]) => {
        const angle = (Math.atan2(-y, x) * 180) / Math.PI;
        return angle < from || angle > to;
      })
      .map((point) => distance(point, head));
    expect(band.length).toBeGreaterThan(100);
    expect(Math.max(...band) / Math.min(...band)).toBeLessThanOrEqual(MAX_BAND_RATIO);
  });

  it.each(CATEGORIES)('draws the %s face convex, with corners only where designed', (category) => {
    const fair = fairness(FACE_SHAPES[category].face);
    expect(fair.inflections).toBe(0);
    expect(fair.kinks).toBe(BLADES.includes(category) ? 2 : 0);
    expect(fair.minRadius).toBeGreaterThanOrEqual(MIN_RADIUS_MM);
    expect(fair.maxJump).toBeLessThanOrEqual(MAX_CURVATURE_JUMP);
  });
});
