import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { FACE_SHAPES, faceCentre, type FaceCategory } from './faceImpact';

/** The impact-location backend reads its face centres from this file; the drawings must agree. */
const REFERENCE = JSON.parse(
  readFileSync(
    fileURLToPath(new URL('../../../../src/openflight/camera/face_centre_reference.json', import.meta.url)),
    'utf8'
  )
) as {
  categories: Record<
    FaceCategory,
    {
      centre_from_toe_mm: number;
      centre_above_leading_edge_mm: number;
      centre_above_face_bottom_mm: number;
      lowest_line_is_longest: boolean;
      centre_toeward_of_lowest_line_middle_mm: number;
      lowest_line_length_mm: number;
      lowest_line_above_face_bottom_mm: number;
      leading_edge_band_mm: number;
      longest_line_length_mm: number;
      toe_margin_mm?: number;
    }
  >;
};

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
        const [a, b, c, e] = [(1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t ** 2, t ** 3];
        points.push([a * p0[0] + b * p1[0] + c * p2[0] + e * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + e * p3[1]]);
      }
    } else points.push(take());
  }
  return points;
}

/** The lowest point (svg y, down) of the polygon's crossing at x. */
function bottom(points: Point[], x: number): number {
  const ys: number[] = [];
  points.forEach((a, index) => {
    const b = points[(index + 1) % points.length];
    if ((a[0] - x) * (b[0] - x) <= 0 && a[0] !== b[0]) ys.push(a[1] + ((x - a[0]) / (b[0] - a[0])) * (b[1] - a[1]));
  });
  return Math.max(...ys);
}

describe('the backend face-centre reference', () => {
  it('names every drawn category', () => {
    expect(Object.keys(REFERENCE.categories).sort()).toEqual(Object.keys(FACE_SHAPES).sort());
  });

  it.each(Object.keys(FACE_SHAPES) as FaceCategory[])(
    'matches the %s drawing: centre heel-ward of the toe extreme, and above the leading edge',
    (category) => {
      const shape = FACE_SHAPES[category];
      const entry = REFERENCE.categories[category];
      const [x, y] = faceCentre(category, shape);
      const head = polygon(shape.head);
      const toe = Math.min(...head.map((point) => point[0]));
      expect(Math.abs(x - toe - entry.centre_from_toe_mm)).toBeLessThanOrEqual(0.1);
      expect(Math.abs(bottom(head, x) - y - entry.centre_above_leading_edge_mm)).toBeLessThanOrEqual(0.1);
      expect(Math.abs(bottom(polygon(shape.face), x) - y - entry.centre_above_face_bottom_mm)).toBeLessThanOrEqual(0.1);
    }
  );

  /* A marked face photo gives the lowest score line (its row end to end, across any gap) and the
   * face's bottom edge; these carry it to the drawn centre. Toe is -x, so toe-ward is mid - x. */
  it.each(Object.keys(FACE_SHAPES) as FaceCategory[])(
    'matches the %s drawing: the lowest score line against the centre',
    (category) => {
      const shape = FACE_SHAPES[category];
      const entry = REFERENCE.categories[category];
      const [x] = faceCentre(category, shape);
      const lowest = Math.max(...shape.grooves.map((groove) => groove.y));
      const row = shape.grooves.filter((groove) => Math.abs(groove.y - lowest) < 0.01);
      const [from, to] = [Math.min(...row.map((g) => g.from)), Math.max(...row.map((g) => g.to))];
      const longest = Math.max(...shape.grooves.map((groove) => groove.to - groove.from));
      expect(row.length === 1 && to - from >= longest - 0.01).toBe(entry.lowest_line_is_longest);
      expect(Math.abs((from + to) / 2 - x - entry.centre_toeward_of_lowest_line_middle_mm)).toBeLessThanOrEqual(0.1);
      expect(Math.abs(to - from - entry.lowest_line_length_mm)).toBeLessThanOrEqual(0.1);
      expect(
        Math.abs(bottom(polygon(shape.face), x) - lowest - entry.lowest_line_above_face_bottom_mm)
      ).toBeLessThanOrEqual(0.1);
    }
  );

  it.each(Object.keys(FACE_SHAPES) as FaceCategory[])(
    'matches the %s drawing: leading-edge band, longest line and the lines’ toe margin',
    (category) => {
      const shape = FACE_SHAPES[category];
      const entry = REFERENCE.categories[category];
      const [x] = faceCentre(category, shape);
      const head = polygon(shape.head);
      const band = bottom(head, x) - bottom(polygon(shape.face), x);
      expect(Math.abs(band - entry.leading_edge_band_mm)).toBeLessThanOrEqual(0.1);
      const longest = Math.max(...shape.grooves.map((groove) => groove.to - groove.from));
      expect(Math.abs(longest - entry.longest_line_length_mm)).toBeLessThanOrEqual(0.1);
      if (entry.toe_margin_mm !== undefined) {
        const toe = Math.min(...head.map((point) => point[0]));
        const margin = Math.min(...shape.grooves.map((groove) => groove.from)) - toe;
        expect(Math.abs(margin - entry.toe_margin_mm)).toBeLessThanOrEqual(0.1);
      }
    }
  );
});
