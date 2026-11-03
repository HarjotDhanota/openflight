import { describe, expect, it } from 'vitest';
import { FACE_SHAPES, type FaceCategory } from './faceImpact';

/* The outlines are C2 splines between designed breaks; these checks sample curvature along each
 * path and fail on wobble (inflections, kinks, spikes, jumps at joins). Segments shorter than
 * VISIBLE_MM (closures hidden inside the tube) are left out. */

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
 * 0 heel-ward, 90 up; the drawings are centred on it). The iron's runs up to where the face's
 * straight top line starts (17 deg): its rounded top-heel corner sits under the top line's fillet
 * into the hosel, which widens the band there as on a real iron. */
const NECK_DEG: Record<FaceCategory, [number, number]> = {
  driver: [-8, 45],
  fairway: [-2, 43],
  hybrid: [-6, 39],
  iron: [-32, 17],
  wedge: [-42, 12],
};
const MAX_BAND_RATIO = 1.25;
/** The smallest radius the top-line-to-hosel fillet of a blade may turn through, mm. */
const CROTCH_MIN_RADIUS_MM = 10;

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

  /* Real blades run the top line into the hosel through one fillet tangent to both: no dip
   * under the top line where it meets the neck, and no tight turn in the crotch. */
  it.each(BLADES)('runs the %s top line into the hosel through a generous fillet', (category) => {
    for (const d of [FACE_SHAPES[category].head, FACE_SHAPES[category].silhouette]) {
      const segments = beziers(d).filter(([a, , , b]) => Math.hypot(b[0] - a[0], b[1] - a[1]) >= VISIBLE_MM);
      const convex = Math.sign(segments.reduce((sum, segment) => sum + local(segment, 0.5).k, 0));
      const crotch = segments.flatMap((segment) =>
        Array.from({ length: 41 }, (_, k) => local(segment, k / 40).k).filter((k) => Math.sign(k) === -convex)
      );
      expect(1 / Math.max(...crotch.map(Math.abs))).toBeGreaterThanOrEqual(CROTCH_MIN_RADIUS_MM);
    }
  });

  /* No bump where the top line meets the neck: the head reaches the hosel fillet along a straight
   * stretch tangent to the top line before it, and from there to the tube's foot never drops below
   * that stretch's line. */
  it.each(BLADES)('runs the %s top line straight into its hosel fillet, never dipping below it', (category) => {
    const segments = beziers(FACE_SHAPES[category].head);
    const length = ([a, , , b]: Bezier) => Math.hypot(b[0] - a[0], b[1] - a[1]);
    const straight = ([a, c1, c2, b]: Bezier) => {
      const off = (c: Point) =>
        Math.abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / length([a, c1, c2, b]);
      return Math.max(off(c1), off(c2)) < STRAIGHT_MM;
    };
    // the tube's foot: the first short closure in the upper half; before it, the fillet's curves
    const foot = segments.findIndex(([a, , , b]) => length([a, a, b, b]) < VISIBLE_MM && a[1] < 0);
    let start = foot - 1;
    while (start > 0 && !straight(segments[start])) start -= 1;
    const topline = segments[start];
    const [[x0, y0], , , [x1, y1]] = topline;
    // a visible straight stretch in the upper half, running heel-ward and down, tangent on both sides
    expect(length(topline)).toBeGreaterThanOrEqual(VISIBLE_MM);
    expect(Math.max(y0, y1)).toBeLessThan(0);
    expect(x1 - x0).toBeGreaterThan(0);
    expect(y1 - y0).toBeGreaterThan(0);
    expect(foot - start).toBeGreaterThan(1);
    const slope = (y1 - y0) / (x1 - x0);
    for (const segment of segments.slice(start + 1, foot)) {
      for (let k = 0; k <= 40; k += 1) {
        const [p0, c1, c2, p3] = segment;
        const t = k / 40;
        const [a, b, c, e] = [(1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t ** 2, t ** 3];
        const x = a * p0[0] + b * c1[0] + c * c2[0] + e * p3[0];
        const y = a * p0[1] + b * c1[1] + c * c2[1] + e * p3[1];
        expect(y).toBeLessThanOrEqual(y0 + slope * (x - x0) + 0.01);
      }
    }
  });

  it.each(CATEGORIES)('draws the %s face convex, with corners only where designed', (category) => {
    const fair = fairness(FACE_SHAPES[category].face);
    expect(fair.inflections).toBe(0);
    expect(fair.kinks).toBe(BLADES.includes(category) ? 2 : 0);
    expect(fair.minRadius).toBeGreaterThanOrEqual(MIN_RADIUS_MM);
    expect(fair.maxJump).toBeLessThanOrEqual(MAX_CURVATURE_JUMP);
  });
});
