// @vitest-environment jsdom
import { renderToString } from 'react-dom/server';
import { createRoot } from 'react-dom/client';
import { flushSync } from 'react-dom';
import { describe, expect, it } from 'vitest';
import type { Shot, ShotMetric } from '../../types/shot';
import { useSystemStore } from '../../stores/useSystemStore';
import { ClubDeliveryViews, ClubProjection, DeliveryNumbers } from './ClubDeliveryViews';
import {
  BLADE_EDGE_BAND_MM,
  CLUB_PROFILES,
  VIEW_METRICS,
  bladeNeck,
  clubProfilePaths,
  deliveryValue,
  faceDeliveryRotation,
  hoselLayout,
  staticLoftLie,
} from './clubDelivery';
import { FACE_SHAPES, USGA, faceCategory, type FaceCategory } from './faceImpact';
import { ALL_CLUBS } from '../../data/clubs';

const clubs = { driver: 'driver', fairway: '3-wood', hybrid: '5-hybrid', iron: '7-iron', wedge: 'sw' };
const categories = Object.keys(clubs) as FaceCategory[];
const clubHands = categories.flatMap((category) =>
  (['right', 'left'] as const).map((hand) => [category, hand] as const)
);
function metric(value: number | null, status: ShotMetric['status'] = 'measured'): ShotMetric {
  return {
    value,
    status,
    unit: 'deg',
    source: null,
    range: null,
    reason: null,
    frame: null,
    note: null,
    normalized: null,
  };
}
function shot(category: FaceCategory = 'iron', hand: 'right' | 'left' = 'right'): Shot {
  return {
    club: clubs[category],
    timestamp: '2026-10-23T22:00:00Z',
    profile_id: 'p',
    ball_speed_mph: 120,
    club_speed_mph: 85,
    smash_factor: 1.41,
    estimated_carry_yards: null,
    carry_range: null,
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
    impact_location: { status: 'ok', handedness: hand },
    metrics: {
      impact_height: { ...metric(4, 'experimental'), unit: 'mm' },
      impact_offset: { ...metric(9, 'experimental'), unit: 'mm' },
      dynamic_lie: metric(65, 'model_geometry'),
      face_to_path: metric(-5.04, 'calculated'),
      face_angle: metric(-1.44, 'estimated'),
      club_path: metric(3.6),
      spin_loft: metric(36.888931297205716, 'calculated'),
      dynamic_loft: metric(33.34, 'experimental'),
      attack_angle: metric(-3.24),
    },
  };
}
function svg(shot: Shot, view: 'top' | 'side') {
  const doc = new DOMParser().parseFromString(renderToString(<ClubProjection shot={shot} view={view} />), 'text/html');
  return doc.querySelector('svg')!;
}
const attr = (element: Element, name: string) => Number(element.getAttribute(name));

type Point = [number, number];
/** The tightest turn either top-view neck edge may make, mm: below this it reads as a corner. */
const TOP_NECK_MIN_RADIUS_MM = 7;
const cross = (a: Point, b: Point) => a[0] * b[1] - a[1] * b[0];
/** Flattens an absolute M/L/Q/C/Z path, 24 points per curve. */
function polygon(d: string): Point[] {
  const tokens = d.match(/[MLQCZ]|-?\d*\.?\d+(?:e-?\d+)?/g) ?? [];
  const points: Point[] = [];
  let command = '';
  for (let i = 0; i < tokens.length;) {
    if (/[MLQCZ]/.test(tokens[i])) command = tokens[i++];
    if (command === 'Z') continue;
    const take = (): Point => [Number(tokens[i++]), Number(tokens[i++])];
    if (command === 'C' || command === 'Q') {
      const p0 = points[points.length - 1];
      const controls = command === 'C' ? [take(), take(), take()] : [take(), take()];
      for (let k = 1; k <= 24; k += 1) {
        const t = k / 24;
        let level: Point[] = [p0, ...controls];
        while (level.length > 1)
          level = level
            .slice(1)
            .map((q, j): Point => [level[j][0] + (q[0] - level[j][0]) * t, level[j][1] + (q[1] - level[j][1]) * t]);
        points.push(level[0]);
      }
    } else points.push(take());
  }
  return points;
}
/** Distance from p to the closed polyline, and the unit direction of the nearest edge. */
function nearest(p: Point, poly: Point[]): [number, Point] {
  let best: [number, Point] = [Infinity, [1, 0]];
  poly.forEach((a, i) => {
    const b = poly[(i + 1) % poly.length];
    const [dx, dy] = [b[0] - a[0], b[1] - a[1]];
    const len = Math.hypot(dx, dy);
    if (len < 1e-9) return;
    const t = Math.min(1, Math.max(0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (len * len)));
    const distance = Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
    if (distance < best[0]) best = [distance, [dx / len, dy / len]];
  });
  return best;
}
/** The unit direction in which the path's curve ending (or starting) at `at` meets it. */
function arrival(d: string, at: Point): Point {
  const tokens = d.match(/[MLQCZ]|-?\d*\.?\d+(?:e-?\d+)?/g)!;
  const numbers: Point[] = [];
  for (let i = 0; i < tokens.length; i += 1) {
    if (/[MLQCZ]/.test(tokens[i])) continue;
    numbers.push([Number(tokens[i]), Number(tokens[++i])]);
  }
  const k = numbers.findIndex((q) => Math.hypot(q[0] - at[0], q[1] - at[1]) < 1e-3);
  const neighbour = numbers.slice(k + 1).find((q) => Math.hypot(q[0] - at[0], q[1] - at[1]) > 1e-3)!;
  const v: Point = [neighbour[0] - at[0], neighbour[1] - at[1]];
  return [v[0] / Math.hypot(...v), v[1] / Math.hypot(...v)];
}
/** The unit direction in which the path's curve reaches `at` (from the control point before it). */
function approach(d: string, at: Point): Point {
  const tokens = d.match(/[MLQCZ]|-?\d*\.?\d+(?:e-?\d+)?/g)!;
  const numbers: Point[] = [];
  for (let i = 0; i < tokens.length; i += 1) {
    if (/[MLQCZ]/.test(tokens[i])) continue;
    numbers.push([Number(tokens[i]), Number(tokens[++i])]);
  }
  const k = numbers.findIndex((q) => Math.hypot(q[0] - at[0], q[1] - at[1]) < 1e-3);
  const v: Point = [at[0] - numbers[k - 1][0], at[1] - numbers[k - 1][1]];
  return [v[0] / Math.hypot(...v), v[1] / Math.hypot(...v)];
}
/** The smallest radius any cubic of an absolute M/L/C path turns through, mm. */
function tightest(d: string): number {
  const tokens = d.match(/[MLCZ]|-?\d*\.?\d+(?:e-?\d+)?/g)!;
  let [command, current, worst] = ['', [0, 0] as Point, 0];
  for (let i = 0; i < tokens.length;) {
    if (/[MLCZ]/.test(tokens[i])) command = tokens[i++];
    if (command === 'Z') continue;
    const take = (): Point => [Number(tokens[i++]), Number(tokens[i++])];
    if (command !== 'C') {
      current = take();
      continue;
    }
    const [p0, c1, c2, p3] = [current, take(), take(), take()];
    for (let k = 0; k <= 60; k += 1) {
      const t = k / 60;
      const u = 1 - t;
      const d1 = [0, 1].map(
        (j) => 3 * (u * u * (c1[j] - p0[j]) + 2 * u * t * (c2[j] - c1[j]) + t * t * (p3[j] - c2[j]))
      );
      const d2 = [0, 1].map((j) => 6 * (u * (c2[j] - 2 * c1[j] + p0[j]) + t * (p3[j] - 2 * c2[j] + c1[j])));
      worst = Math.max(worst, Math.abs(d1[0] * d2[1] - d1[1] * d2[0]) / Math.hypot(d1[0], d1[1]) ** 3);
    }
    current = p3;
  }
  return 1 / worst;
}
function inside(p: Point, poly: Point[]): boolean {
  let hit = false;
  poly.forEach((a, i) => {
    const b = poly[(i + 1) % poly.length];
    if (a[1] > p[1] !== b[1] > p[1] && p[0] < a[0] + ((p[1] - a[1]) / (b[1] - a[1])) * (b[0] - a[0])) hit = !hit;
  });
  return hit;
}

describe('club delivery drawings', () => {
  it.each(clubHands)('%s %s heel view puts the near-side hosel in front of the head', (category, hand) => {
    const element = svg(shot(category, hand), 'side');
    const outline = element.querySelector('.club-projection__outline')!;
    const shaft = element.querySelector('.club-projection__shaft')!;
    expect(outline.compareDocumentPosition(shaft) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    const neck = element.querySelector('.club-projection__neck');
    if (neck) expect(outline.compareDocumentPosition(neck) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
  });
  it.each(['iron', 'wedge'] as const)('%s address surface restores projected grooves for both hands', (category) => {
    for (const hand of ['right', 'left'] as const) {
      const element = svg(shot(category, hand), 'top');
      const lines = element.querySelectorAll('line[stroke="var(--face-art-groove)"]');
      expect(lines).toHaveLength(FACE_SHAPES[category].grooves.length);
      const scale = Math.sin((CLUB_PROFILES[category].loft * Math.PI) / 180);
      lines.forEach((line, index) => {
        const groove = FACE_SHAPES[category].grooves[index];
        expect(attr(line, 'x1')).toBeCloseTo(groove.y * scale, 9);
        expect(attr(line, 'x2')).toBeCloseTo(groove.y * scale, 9);
        expect(attr(line, 'y1')).toBe(groove.from);
        expect(attr(line, 'y2')).toBe(groove.to);
        expect(attr(line, 'stroke-width')).toBeCloseTo(0.6 * scale, 9);
      });
      expect(element.querySelector('.club-projection__topline')).not.toBeNull();
    }
  });
  it('titles the top view at impact: it draws the face angle and club path at impact', () => {
    const html = renderToString(<ClubDeliveryViews shot={shot()} sessionShots={[]} />);
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const titles = [...doc.querySelectorAll('.club-views__nav button')].map((button) => button.textContent);
    expect(titles[1]).toBe('Top at impact');
    expect(doc.querySelector('[data-view="top"]')!.getAttribute('aria-label')).toBe('Top at impact');
    expect(html).not.toMatch(/at address/i);
  });

  it('uses kiosk handedness without camera metadata and keeps a recorded shot’s hand', () => {
    const host = document.createElement('div');
    const root = createRoot(host);
    const previous = useSystemStore.getState().handed;
    try {
      useSystemStore.getState().setHanded('LH');
      const s = shot();
      s.impact_location = null;
      flushSync(() => root.render(<ClubDeliveryViews shot={s} sessionShots={[s]} />));
      expect([...host.querySelectorAll('[data-handed]')].map((el) => el.getAttribute('data-handed'))).toEqual([
        'left',
        'left',
        'left',
      ]);
      s.impact_location = { handedness: 'right' };
      flushSync(() => root.render(<ClubDeliveryViews shot={{ ...s }} sessionShots={[s]} />));
      expect([...host.querySelectorAll('[data-handed]')].map((el) => el.getAttribute('data-handed'))).toEqual([
        'right',
        'right',
        'right',
      ]);
    } finally {
      root.unmount();
      useSystemStore.getState().setHanded(previous);
    }
  });

  it.each(['iron', 'wedge'] as const)('%s address face preserves the loft-projected blade contour', (category) => {
    const source = FACE_SHAPES[category].face;
    const projected = clubProfilePaths(category).topFace;
    expect(projected.match(/[MLCZ]/g)).toEqual(source.match(/[MLCZ]/g));
    const coordinates = (d: string) => d.match(/-?\d+(?:\.\d+)?/g)!.map(Number);
    const original = coordinates(source);
    const actual = coordinates(projected);
    const scale = Math.sin((CLUB_PROFILES[category].loft * Math.PI) / 180);
    for (let i = 0; i < original.length; i += 2) {
      expect(actual[i]).toBeCloseTo(original[i + 1] * scale, 3);
      expect(actual[i + 1]).toBe(original[i]);
    }
  });

  it.each(['right', 'left'] as const)(
    '%s address and heel views put the target on the golfer’s correct side',
    (hand) => {
      for (const view of ['top', 'side'] as const) {
        const element = svg(shot('iron', hand), view);
        const mirror = element.querySelector('.club-projection__hand')!.hasAttribute('transform') ? -1 : 1;
        const ballX = mirror * attr(element.querySelector('.club-projection__ball')!, 'cx');
        expect(Math.sign(ballX)).toBe(hand === 'right' ? -1 : 1);
      }
    }
  );

  it.each(clubHands)('%s %s top arrows preserve target-relative right-positive signs', (category, hand) => {
    const s = shot(category, hand);
    for (const degrees of [-12.37, 0, 12.37]) {
      s.metrics!.face_angle = metric(degrees);
      s.metrics!.club_path = metric(degrees);
      const element = svg(s, 'top');
      const mirror = element.querySelector('.club-projection__hand')!.hasAttribute('transform') ? -1 : 1;
      for (const arrow of element.querySelectorAll('.club-projection__arrow')) {
        const x = mirror * attr(arrow, 'x2');
        const y = attr(arrow, 'y2');
        expect((Math.atan2(mirror * y, mirror * x) * 180) / Math.PI).toBeCloseTo(degrees, 9);
      }
    }
  });

  it.each(clubHands)('%s %s side arrows keep up positive and down negative', (category, hand) => {
    for (const degrees of [-6.25, 0, 6.25]) {
      const s = shot(category, hand);
      s.metrics!.dynamic_loft = metric(degrees);
      s.metrics!.attack_angle = metric(degrees);
      const element = svg(s, 'side');
      for (const arrow of element.querySelectorAll('.club-projection__arrow')) {
        expect((Math.atan2(-attr(arrow, 'y2'), Math.abs(attr(arrow, 'x2'))) * 180) / Math.PI).toBeCloseTo(degrees, 9);
      }
    }
  });

  it('resolves each arrowhead to its own marker, with no duplicate SVG IDs', () => {
    for (const view of ['top', 'side'] as const) {
      const element = svg(shot(), view);
      const ids = [...element.querySelectorAll('[id]')].map((node) => node.id);
      expect(new Set(ids).size).toBe(ids.length);
      for (const arrow of element.querySelectorAll('.club-projection__arrow')) {
        const id = arrow.getAttribute('marker-end')!.slice(5, -1);
        expect([...element.querySelectorAll('marker')].some((marker) => marker.id === id)).toBe(true);
      }
    }
  });
  /* As in the face view, the blade's top line runs into the hosel through a fillet tangent to
   * both: the neck leaves the head along the head's own edge, so no notch shows at the join. */
  it.each(['iron', 'wedge'] as const)(
    '%s top view: both neck edges leave the blade tangent to it, no kink',
    (category) => {
      const paths = clubProfilePaths(category);
      const neck = bladeNeck(category, 'top');
      // the blade's shell is the face outline stroked 4.8 mm wide, then projected by the top
      // view's affine map (x, y) -> (k y + o, x): its band is 2.4 mm in face coordinates
      const [, , k, , o] = paths.topShellTransform.match(/-?\d*\.?\d+(?:e-?\d+)?/g)!.map(Number);
      const toFace = ([x, y]: Point): Point => [y, (x - o) / k];
      const toPlan = ([x, y]: Point): Point => [k * y + o, x];
      const direction = ([x, y]: Point): Point => [y / Math.hypot(y, x / k), x / k / Math.hypot(y, x / k)];
      const face = polygon(FACE_SHAPES[category].face);
      for (const [join, tangent, curve] of [
        [neck.join, neck.tangent, arrival(neck.d, neck.join)],
        [neck.front!.join, neck.front!.tangent, approach(neck.d, neck.front!.join)],
      ] as const) {
        const [distance, along] = nearest(toFace(join), face);
        expect(distance).toBeCloseTo(BLADE_EDGE_BAND_MM, 1);
        expect(Math.abs(cross(along, direction(tangent)))).toBeLessThan(Math.sin((2 * Math.PI) / 180));
        expect(Math.abs(cross(curve, tangent))).toBeLessThan(1e-3);
      }
      // each edge sweeps into the ferrule without a tight turn that would read as a corner
      expect(tightest(neck.d)).toBeGreaterThanOrEqual(TOP_NECK_MIN_RADIUS_MM);
      // heel-ward of both joins the blade's band lies inside the neck: no tooth pokes out past it
      const body = polygon(neck.d);
      const from = Math.max(toFace(neck.join)[0], toFace(neck.front!.join)[0]) + 0.5;
      const area = face.reduce((sum, a, i) => {
        const b = face[(i + 1) % face.length];
        return sum + a[0] * b[1] - b[0] * a[1];
      }, 0);
      let checked = 0;
      face.forEach((q, i) => {
        if (q[0] < from) return;
        const [a, b] = [face[(i - 1 + face.length) % face.length], face[(i + 1) % face.length]];
        const t: Point = [b[0] - a[0], b[1] - a[1]];
        const n: Point = area > 0 ? [t[1], -t[0]] : [-t[1], t[0]];
        const m = Math.hypot(...n);
        const band = BLADE_EDGE_BAND_MM - 0.05;
        expect(inside(toPlan([q[0] + (n[0] / m) * band, q[1] + (n[1] / m) * band]), body)).toBe(true);
        checked += 1;
      });
      expect(checked).toBeGreaterThan(10);
    }
  );

  /* Seen from the heel the hosel stands in front of the lofted face: the gap between its back edge
   * and the top of the face is open on a real club. The hosel meets the head only at the neck. */
  it.each(['iron', 'wedge'] as const)('%s heel view: the hosel meets the head only at the neck', (category) => {
    const p = CLUB_PROFILES[category];
    const paths = clubProfilePaths(category);
    const neck = bladeNeck(category, 'side');
    const layout = hoselLayout(category, 'side');
    const shape = polygon(neck.d);
    const head = polygon(paths.side);
    // open between the face's top and the hosel's back edge, at and just under the top line's cap
    for (const drop of [0.5, 3, 6]) {
      const y = paths.ty + drop;
      const faceX = paths.tx + ((y - paths.ty) / (paths.by - paths.ty)) * (paths.bx - paths.tx);
      const gap: Point = [(faceX + layout.shaftX - layout.ferruleRadius) / 2, y];
      expect(inside(gap, shape)).toBe(false);
      expect(inside(gap, head)).toBe(false);
    }
    // the hosel's back edge reaches the head where it crosses the face line, low on the blade
    expect(neck.join[1]).toBeGreaterThan(paths.ty + p.faceHeight * 0.25);
    const side = (q: Point) => (q[0] - paths.tx) * (paths.by - paths.ty) - (q[1] - paths.ty) * (paths.bx - paths.tx);
    expect(Math.abs(side(neck.join))).toBeLessThan(1e-6);
  });

  /* The fairway and hybrid heel views against the population side outlines of OEM toe-side photos
   * (research/club-models/research/club-geometry/woods-3view/avg_views.json: 13 3-woods, 10 hybrids,
   * 2021+): face-to-back depth, ground-to-crown height, face height and the hosel axis behind the
   * leading edge, each within the research's +-2 mm gate. */
  it.each([
    ['fairway', { depth: 86.2, headHeight: 38.2, faceHeight: 30.6, hoselBehindLeadingEdge: 16.0 }],
    ['hybrid', { depth: 66.9, headHeight: 35.4, faceHeight: 28.8, hoselBehindLeadingEdge: 15.9 }],
  ] as const)('draws the %s heel view to the OEM side-photo population within 2 mm', (category, ref) => {
    const paths = clubProfilePaths(category);
    const outline = polygon(paths.side);
    const xs = outline.map(([x]) => x);
    const ys = outline.map(([, y]) => y);
    const layout = hoselLayout(category, 'side');
    expect(Math.abs(Math.max(...xs) - Math.min(...xs) - ref.depth)).toBeLessThanOrEqual(2);
    expect(Math.abs(Math.max(...ys) - Math.min(...ys) - ref.headHeight)).toBeLessThanOrEqual(2);
    expect(Math.abs(Math.hypot(paths.bx - paths.tx, paths.by - paths.ty) - ref.faceHeight)).toBeLessThanOrEqual(2);
    expect(Math.abs(paths.bx - layout.shaftX - ref.hoselBehindLeadingEdge)).toBeLessThanOrEqual(2);
  });

  it.each(categories)('uses bounded population dimensions for %s, with distinct projections', (category) => {
    const p = CLUB_PROFILES[category];
    const paths = clubProfilePaths(category);
    expect(paths.top).not.toBe(paths.side);
    expect(paths.top).not.toMatch(/NaN|Infinity/);
    expect(paths.side).not.toMatch(/NaN|Infinity/);
    if (p.depth) {
      expect(p.length).toBeLessThanOrEqual(USGA.woodHeelToToeMaxMm);
      expect(p.height).toBeLessThanOrEqual(USGA.woodSoleToCrownMaxMm);
    }
  });

  it.each(
    categories.flatMap((category) =>
      (['top', 'side'] as const).flatMap((view) =>
        (['right', 'left'] as const).map((hand) => [category, view, hand] as const)
      )
    )
  )('%s %s %s: arrows follow the unrounded readings and mirror with the club', (category, view, hand) => {
    const element = svg(shot(category, hand), view);
    expect(element.querySelector('.club-projection__hand')?.getAttribute('transform')).toBe(
      hand === 'right' ? 'scale(-1 1)' : null
    );
    for (const [kind, degrees] of view === 'top'
      ? ([
          ['face', -1.44],
          ['path', 3.6],
        ] as const)
      : ([
          ['face', 33.34],
          ['path', -3.24],
        ] as const)) {
      const arrow = element.querySelector(`.club-projection__arrow--${kind}`)!;
      const x = attr(arrow, 'x2'),
        y = attr(arrow, 'y2');
      expect(Math.hypot(x, y)).toBeCloseTo(86, 9);
      expect((Math.atan2(view === 'top' && hand === 'left' ? y : -y, x) * 180) / Math.PI).toBeCloseTo(degrees, 9);
      expect(attr(arrow, 'x1')).toBe(0);
      expect(attr(arrow, 'y1')).toBe(0);
      expect(arrow.parentElement).toBe(element.querySelector('.club-projection__hand'));
    }
    expect(attr(element.querySelector('.club-projection__ball')!, 'r')).toBe(21.35);
    const head = element.querySelector('.club-projection__head')!;
    expect(head.getAttribute('transform')).toBe(
      `rotate(${view === 'top' ? -1.44 * (hand === 'left' ? 1 : -1) : staticLoftLie(clubs[category]).loft - 33.34})`
    );
    const rotation = (Number(head.getAttribute('transform')!.slice(7, -1)) * Math.PI) / 180;
    const edge = clubProfilePaths(category, view === 'top' ? undefined : staticLoftLie(clubs[category]).loft);
    const [nx, ny] = view === 'top' ? [1, 0] : [edge.by - edge.ty, edge.tx - edge.bx];
    const normal = [
      nx * Math.cos(rotation) - ny * Math.sin(rotation),
      nx * Math.sin(rotation) + ny * Math.cos(rotation),
    ];
    const arrow = element.querySelector('.club-projection__arrow--face')!;
    expect(Math.atan2(normal[1], normal[0])).toBeCloseTo(Math.atan2(attr(arrow, 'y2'), attr(arrow, 'x2')), 4);
  });

  it.each([-30, 0, 12.37, 60])('uses the side face normal for a dynamic loft of %f degrees', (loft) => {
    for (const category of categories) {
      const s = shot(category);
      s.metrics!.dynamic_loft = metric(loft);
      const element = svg(s, 'side');
      const face = element
        .querySelector('.club-projection__face-edge')!
        .getAttribute('d')!
        .match(/-?\d+(?:\.\d+)?/g)!
        .map(Number);
      const rotation = Number(element.querySelector('.club-projection__head')!.getAttribute('transform')!.slice(7, -1));
      const staticNormal = (Math.atan2(face[2] - face[0], face[3] - face[1]) * 180) / Math.PI;
      expect(staticNormal - rotation).toBeCloseTo(loft, 2);
    }
  });

  it.each([null, NaN, Infinity])('does not invent arrows or numbers for %s', (value) => {
    const s = shot();
    for (const key of Object.values(VIEW_METRICS).flat()) s.metrics![key] = metric(value);
    for (const view of ['top', 'side'] as const) {
      const element = svg(s, view);
      expect(element.querySelectorAll('.club-projection__arrow')).toHaveLength(0);
      expect(element.querySelector('.club-projection__head')!.getAttribute('transform')).toBe('rotate(0)');
      const html = renderToString(<DeliveryNumbers shot={s} view={view} />);
      expect(html.match(/<span>—<\/span>/g)).toHaveLength(3);
      expect(html).not.toContain('label-badge--');
    }
    expect(faceDeliveryRotation(s, 'iron')).toBe(0);
  });

  /* With no dynamic loft the side view leans the head to the selected club's own static loft, and with
   * no dynamic lie the face view tilts to its own static lie; the shapes stay the category's. */
  it('draws a 4-iron and a 9-iron side view at their own static lofts, not a rotated 7-iron', () => {
    const lean = (club: string) => {
      const s = { ...shot(), club };
      for (const key of Object.values(VIEW_METRICS).flat()) s.metrics![key] = metric(null);
      const element = svg(s, 'side');
      const rotation = Number(element.querySelector('.club-projection__head')!.getAttribute('transform')!.slice(7, -1));
      const ball = element.querySelector('.club-projection__ball')!;
      const face = element
        .querySelector('.club-projection__face-edge')!
        .getAttribute('d')!
        .match(/-?\d+(?:\.\d+)?/g)!
        .map(Number);
      return {
        rotation,
        ball: (Math.atan2(-attr(ball, 'cy'), attr(ball, 'cx')) * 180) / Math.PI,
        face: (Math.atan2(face[2] - face[0], face[3] - face[1]) * 180) / Math.PI,
        s,
      };
    };
    const [four, seven, nine] = [lean('4-iron'), lean('7-iron'), lean('9-iron')];
    expect(staticLoftLie('4-iron').loft).toBeLessThan(staticLoftLie('7-iron').loft);
    expect(staticLoftLie('9-iron').loft).toBeGreaterThan(staticLoftLie('7-iron').loft);
    // the profile is built at the club's loft: no rotation, the face line itself at the loft
    for (const [club, drawn] of [
      ['4-iron', four],
      ['7-iron', seven],
      ['9-iron', nine],
    ] as const) {
      expect(drawn.rotation).toBe(0);
      expect(drawn.face).toBeCloseTo(staticLoftLie(club).loft, 1);
      expect(drawn.ball).toBeCloseTo(staticLoftLie(club).loft, 6);
    }
    expect(four.face).not.toBeCloseTo(nine.face, 0);
    // at address every club's face view sits the same way: a static loft or lie never tilts it
    for (const drawn of [four, seven, nine]) expect(faceDeliveryRotation(drawn.s, 'iron')).toBe(0);
    // a measured dynamic lie tilts it by its difference from the club's own static lie, seen along
    // the face normal: the same 1 deg upright tilts a 4-iron and a 9-iron by about 1 deg each
    const projected = (lie: number, loft: number) =>
      (Math.atan2(Math.sin((lie * Math.PI) / 180) * Math.cos((loft * Math.PI) / 180), Math.cos((lie * Math.PI) / 180)) *
        180) /
      Math.PI;
    for (const club of ['4-iron', '9-iron']) {
      const s = { ...shot(), club };
      for (const key of Object.values(VIEW_METRICS).flat()) s.metrics![key] = metric(null);
      const { loft, lie } = staticLoftLie(club);
      s.metrics!.dynamic_lie = metric(lie);
      expect(faceDeliveryRotation(s, 'iron')).toBeCloseTo(0, 9);
      s.metrics!.dynamic_lie = metric(lie + 1);
      expect(faceDeliveryRotation(s, 'iron')).toBeCloseTo(projected(lie, loft) - projected(lie + 1, loft), 9);
      expect(faceDeliveryRotation(s, 'iron')).toBeLessThan(-0.8);
    }
    // shot values still win: a measured dynamic loft sets the lean whatever the club
    for (const club of ['4-iron', '9-iron']) {
      const s = { ...shot(), club };
      const element = svg(s, 'side');
      expect(element.querySelector('.club-projection__head')!.getAttribute('transform')).toBe(
        `rotate(${staticLoftLie(club).loft - 33.34})`
      );
    }
  });

  /* The side view is each club at address: its sole on the ground, its face at its own loft, the
   * shaft upright; nothing tips over (a rotated category profile lifted the sole off the ground). */
  it.each(ALL_CLUBS.map((club) => club.id))('stands the %s on its sole at its own loft in the side view', (club) => {
    const category = faceCategory(club);
    const { loft } = staticLoftLie(club);
    const s = { ...shot(category), club };
    for (const key of Object.values(VIEW_METRICS).flat()) s.metrics![key] = metric(null);
    const element = svg(s, 'side');
    expect(element.querySelector('.club-projection__head')!.getAttribute('transform')).toBe('rotate(0)');
    const paths = clubProfilePaths(category, loft);
    expect(element.querySelector('.club-projection__outline')!.getAttribute('d')).toBe(paths.side);
    // the face line runs at the loft from vertical
    expect((Math.atan2(paths.bx - paths.tx, paths.by - paths.ty) * 180) / Math.PI).toBeCloseTo(loft, 6);
    // the lowest point is the sole, behind the leading edge, and the leading edge sits on it
    const outline = polygon(paths.side);
    const ground = Math.max(...outline.map(([, y]) => y));
    const lowest = outline.find(([, y]) => y === ground)!;
    expect(lowest[0]).toBeLessThan(paths.bx);
    expect(ground - paths.by).toBeGreaterThanOrEqual(0);
    expect(ground - paths.by).toBeLessThanOrEqual(6);
    // the shaft stands upright above the head
    const shaft = polygon(element.querySelector('.club-projection__shaft')!.getAttribute('d')!);
    expect(Math.abs(shaft[0][0] + shaft[3][0] - shaft[1][0] - shaft[2][0])).toBeLessThan(1e-6);
  });

  /* The hosel meets the head with one continuous outline at every loft: the neck's foot ends on the
   * head (the leading edge for a blade, the face top or crown for a wood), nothing of it pokes out
   * below the sole or past the leading edge, and the face line ends at the leading edge and the
   * face top without running into the neck. */
  it.each(ALL_CLUBS.map((club) => club.id))('joins the %s hosel to its head continuously in the heel view', (club) => {
    const category = faceCategory(club);
    const s = { ...shot(category), club };
    for (const key of Object.values(VIEW_METRICS).flat()) s.metrics![key] = metric(null);
    const element = svg(s, 'side');
    const head = polygon(element.querySelector('.club-projection__outline')!.getAttribute('d')!);
    const neck = polygon(element.querySelector('.club-projection__neck')!.getAttribute('d')!);
    const paths = clubProfilePaths(category, staticLoftLie(club).loft);
    const ground = Math.max(...head.map(([, y]) => y));
    // nothing of the neck below the sole or ahead of the leading edge near the ground
    for (const [x, y] of neck) {
      expect(y).toBeLessThanOrEqual(ground + 0.05);
      if (y > paths.by - 3) expect(x).toBeLessThanOrEqual(paths.bx + 0.6);
    }
    // the neck's lowest points sit on or inside the head
    const low = [...neck].sort((a, b) => b[1] - a[1]).slice(0, 3);
    for (const q of low) expect(inside(q, head) || nearest(q, head)[0] < 0.6).toBe(true);
    // the shaft's foot is hidden in the neck or the head: no strip of it hangs below the neck
    const shaft = polygon(element.querySelector('.club-projection__shaft')!.getAttribute('d')!);
    for (const q of [shaft[0], shaft[3]]) expect(inside(q, neck) || inside(q, head)).toBe(true);
    // the face line is not inside the neck: it ends at the face top and the leading edge
    for (let k = 1; k < 20; k += 1) {
      const q: Point = [paths.tx + ((paths.bx - paths.tx) * k) / 20, paths.ty + ((paths.by - paths.ty) * k) / 20];
      expect(inside(q, neck) && nearest(q, neck)[0] > 0.3).toBe(false);
    }
  });

  it('gives every selectable club type its own row; only an unknown name falls back to its category', () => {
    for (const club of ALL_CLUBS) expect(staticLoftLie(club.id).source).toBe('club');
    // the types that used to fall back now carry their own population values
    for (const [club, category] of [
      ['2-iron', 'iron'],
      ['5-wood', 'fairway'],
      ['7-wood', 'fairway'],
      ['5-hybrid', 'hybrid'],
      ['7-hybrid', 'hybrid'],
      ['9-hybrid', 'hybrid'],
    ] as const)
      expect(staticLoftLie(club).loft).not.toBe(CLUB_PROFILES[category].loft);
    // lofts climb through each family's numbers
    const lofts = (ids: string[]) => ids.map((id) => staticLoftLie(id).loft);
    for (const family of [
      ['2-iron', '3-iron', '4-iron', '5-iron', '6-iron', '7-iron', '8-iron', '9-iron', 'pw', 'gw', 'sw', 'lw'],
      ['driver', '3-wood', '5-wood', '7-wood'],
      ['3-hybrid', '5-hybrid', '7-hybrid', '9-hybrid'],
    ])
      lofts(family).forEach((loft, k, all) => k && expect(loft).toBeGreaterThan(all[k - 1]));
    for (const [club, category] of [
      ['mystery-club', 'iron'],
      ['', 'iron'],
      ['11-wood', 'fairway'],
    ] as const) {
      expect(staticLoftLie(club)).toEqual({
        loft: CLUB_PROFILES[category].loft,
        lie: CLUB_PROFILES[category].lie,
        source: 'category',
      });
    }
    const s = { ...shot(), club: 'mystery-club' };
    for (const key of Object.values(VIEW_METRICS).flat()) s.metrics![key] = metric(null);
    expect(svg(s, 'side').querySelector('.club-projection__head')!.getAttribute('transform')).toBe('rotate(0)');
    expect(faceDeliveryRotation(s, 'iron')).toBe(0);
  });

  it('rejects a stale numeric record marked not measured, retaining available companion readings', () => {
    const s = shot();
    s.metrics!.face_angle = metric(9, 'not_measured');
    expect(deliveryValue(s, 'face_angle')).toBeNull();
    const element = svg(s, 'top');
    expect(element.querySelector('.club-projection__arrow--face')).toBeNull();
    expect(element.querySelector('.club-projection__arrow--path')).not.toBeNull();
    expect(element.querySelector('.club-projection__head')!.getAttribute('transform')).toBe('rotate(0)');
  });

  it.each(clubHands)(
    'tilts the %s %s face assembly by projected dynamic lie without modifying pinned geometry',
    (category, hand) => {
      const before = JSON.stringify(FACE_SHAPES[category]);
      const s = shot(category, hand);
      const rotation = faceDeliveryRotation(s, category);
      const along = (lie: number, loft: number) =>
        (Math.atan(Math.tan((lie * Math.PI) / 180) * Math.cos((loft * Math.PI) / 180)) * 180) / Math.PI;
      // measured against the club's own static lie at its static loft
      const reference = along(staticLoftLie(s.club).lie, staticLoftLie(s.club).loft);
      expect(reference - rotation).toBeCloseTo(along(65, 33.34), 9);
      const doc = new DOMParser().parseFromString(
        renderToString(<ClubDeliveryViews shot={s} sessionShots={[s]} />),
        'text/html'
      );
      expect(doc.querySelector('.face-impact__delivery')!.getAttribute('transform')).toBe(
        `rotate(${rotation * (hand === 'left' ? -1 : 1)})`
      );
      expect(doc.querySelector('.face-impact__face')!.getAttribute('d')).toBe(FACE_SHAPES[category].face);
      expect(JSON.stringify(FACE_SHAPES[category])).toBe(before);
      s.metrics!.dynamic_loft = metric(null);
      expect(reference - faceDeliveryRotation(s, category)).toBeCloseTo(along(65, staticLoftLie(s.club).loft), 9);
      s.metrics!.dynamic_lie = metric(null);
      expect(faceDeliveryRotation(s, category)).toBe(0);
    }
  );

  it.each(clubHands)('shows all nine signed values and original provenance for %s %s', (category, hand) => {
    const s = shot(category, hand);
    const doc = new DOMParser().parseFromString(
      renderToString(<ClubDeliveryViews shot={s} sessionShots={[s]} />),
      'text/html'
    );
    expect([...doc.querySelectorAll('[data-view-metric]')].map((el) => el.getAttribute('data-view-metric'))).toEqual(
      Object.values(VIEW_METRICS).flat()
    );
    expect(doc.querySelector('[data-view-metric="face_to_path"] dd')!.textContent).toBe('-5.0°');
    expect(doc.querySelector('[data-view-metric="spin_loft"] dd')!.textContent).toBe('36.9°');
    expect([...doc.querySelectorAll('[data-view-metric] dd')].map((el) => el.textContent)).toEqual([
      '+4mm',
      '+9mm',
      '65.0°',
      '-5.0°',
      '-1.4°',
      '+3.6°',
      '36.9°',
      '33.3°',
      '-3.2°',
    ]);
    for (const cell of doc.querySelectorAll('[data-view-metric]')) {
      const record = s.metrics![cell.getAttribute('data-view-metric')!];
      expect(cell.querySelector('.label-badge')!.getAttribute('data-label')).toBe(record.status);
      expect(cell.closest('[transform]')).toBeNull();
    }
  });
});
