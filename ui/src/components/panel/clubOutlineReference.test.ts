import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { FACE_SHAPES } from './faceImpact';

/** The impact-placement backend fits these outlines; the drawings must agree. */
const REFERENCE = JSON.parse(
  readFileSync(
    fileURLToPath(new URL('../../../../src/openflight/camera/club_outline_reference.json', import.meta.url)),
    'utf8'
  )
) as { categories: Record<'iron' | 'wedge', { head_outline_uv_mm: [number, number][] }> };

type Point = [number, number];

/** faceImpact's own flattening: 12 points per cubic. */
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
      for (let k = 1; k <= 12; k += 1) {
        const t = k / 12;
        const [a, b, c, e] = [(1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t ** 2, t ** 3];
        points.push([a * p0[0] + b * p1[0] + c * p2[0] + e * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + e * p3[1]]);
      }
    } else points.push(take());
  }
  return points;
}

describe('the backend club-outline reference', () => {
  it.each(['iron', 'wedge'] as const)('is the %s drawing in face coordinates (u = -x toe-ward, v = -y up)', (category) => {
    const drawn = polygon(FACE_SHAPES[category].head);
    const kept = REFERENCE.categories[category].head_outline_uv_mm;
    expect(kept.length).toBe(drawn.length);
    drawn.forEach(([x, y], k) => {
      expect(Math.abs(kept[k][0] + x)).toBeLessThanOrEqual(0.01);
      expect(Math.abs(kept[k][1] + y)).toBeLessThanOrEqual(0.01);
    });
  });
});
