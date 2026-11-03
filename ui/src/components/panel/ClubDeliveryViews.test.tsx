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
} from './clubDelivery';
import { FACE_SHAPES, USGA, type FaceCategory } from './faceImpact';

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
  it.each(['iron', 'wedge'] as const)('%s top view: the neck leaves the blade tangent to its back edge', (category) => {
    const paths = clubProfilePaths(category);
    const neck = bladeNeck(category, 'top');
    const shell = polygon(paths.top);
    const [distance, along] = nearest(neck.join, shell);
    expect(distance).toBeCloseTo(BLADE_EDGE_BAND_MM, 1);
    expect(Math.abs(cross(along, neck.tangent))).toBeLessThan(Math.sin((2 * Math.PI) / 180));
    expect(Math.abs(cross(arrival(neck.d, neck.join), neck.tangent))).toBeLessThan(1e-3);
    // toe-ward of the face's top-heel corner, and the neck covers the shell's band from there round
    // that corner: no tooth of the blade pokes out past the fillet
    const heelCorner = paths.top.match(/-?\d+(?:\.\d+)?/g)!.map(Number);
    expect(neck.join[1]).toBeLessThan(heelCorner[1]);
    const body = polygon(neck.d);
    const band = BLADE_EDGE_BAND_MM - 0.05;
    for (let y = neck.join[1] + 0.5; y < heelCorner[1]; y += 0.5) {
      const xs = shell.flatMap((a, k) => {
        const b = shell[(k + 1) % shell.length];
        return (a[1] - y) * (b[1] - y) <= 0 && a[1] !== b[1]
          ? [a[0] + ((y - a[1]) / (b[1] - a[1])) * (b[0] - a[0])]
          : [];
      });
      const [, edge] = nearest([Math.min(...xs), y], shell);
      const outward: Point = edge[1] > 0 ? [-edge[1], edge[0]] : [edge[1], -edge[0]];
      expect(inside([Math.min(...xs) + outward[0] * band, y + outward[1] * band], body)).toBe(true);
    }
    for (let a = 90; a <= 270; a += 10) {
      const t = (a * Math.PI) / 180;
      expect(inside([heelCorner[0] + band * Math.cos(t), heelCorner[1] + band * Math.sin(t)], body)).toBe(true);
    }
  });

  it.each(['iron', 'wedge'] as const)(
    '%s heel view: the hosel runs into the top line and fills the crotch',
    (category) => {
      const p = CLUB_PROFILES[category];
      const paths = clubProfilePaths(category);
      const neck = bladeNeck(category, 'side');
      const layout = hoselLayout(category, 'side');
      // the top of the top line's rounded cap, met horizontally
      expect(neck.join[0]).toBeCloseTo(paths.tx - p.topline / 2, 6);
      expect(neck.join[1]).toBeCloseTo(paths.ty - 1, 6);
      expect(Math.abs(neck.tangent[1])).toBeLessThan(1e-9);
      expect(Math.abs(cross(arrival(neck.d, neck.join), neck.tangent))).toBeLessThan(1e-3);
      // metal between the face's top and the hosel's back edge at the top line's height
      const shape = polygon(neck.d);
      expect(inside([(paths.tx + layout.shaftX - layout.ferruleRadius) / 2, paths.ty - 0.5], shape)).toBe(true);
    }
  );

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
      `rotate(${view === 'top' ? -1.44 * (hand === 'left' ? 1 : -1) : CLUB_PROFILES[category].loft - 33.34})`
    );
    const rotation = (Number(head.getAttribute('transform')!.slice(7, -1)) * Math.PI) / 180;
    const edge = clubProfilePaths(category);
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
      const projected = (Math.atan(Math.tan((65 * Math.PI) / 180) * Math.cos((33.34 * Math.PI) / 180)) * 180) / Math.PI;
      expect(FACE_SHAPES[category].hosel.lieDeg - rotation).toBeCloseTo(projected, 9);
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
      const fallback =
        (Math.atan(Math.tan((65 * Math.PI) / 180) * Math.cos((CLUB_PROFILES[category].loft * Math.PI) / 180)) * 180) /
        Math.PI;
      expect(FACE_SHAPES[category].hosel.lieDeg - faceDeliveryRotation(s, category)).toBeCloseTo(fallback, 9);
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
