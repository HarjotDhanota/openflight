import { FACE_SHAPES, type DeliveryShot, type FaceCategory } from './faceImpact';

export type DeliveryView = 'face' | 'top' | 'side';
export const VIEW_METRICS = {
  face: ['impact_height', 'impact_offset', 'dynamic_lie'],
  top: ['face_to_path', 'face_angle', 'club_path'],
  side: ['spin_loft', 'dynamic_loft', 'attack_angle'],
} as const;

export interface ClubProfile {
  length: number;
  toe: number;
  depth: number;
  height: number;
  faceHeight: number;
  loft: number;
  lie: number;
  progression: number;
  topline: number;
  sole: number;
  bounce: number;
}

// Population aggregates and provisional dimensions: docs/camera/club-delivery-views.md.
export const CLUB_PROFILES: Record<FaceCategory, ClubProfile> = {
  driver: {
    length: 117,
    toe: 66.39,
    depth: 114.5,
    height: 62,
    faceHeight: 50.3,
    loft: 10.4,
    lie: 57.4,
    progression: 16.2,
    topline: 0,
    sole: 0,
    bounce: 0,
  },
  fairway: {
    length: 105,
    toe: 56.1,
    depth: 87.9,
    height: 40.6,
    faceHeight: 30,
    loft: 14.9,
    lie: 56.6,
    progression: 15.4,
    topline: 0,
    sole: 0,
    bounce: 0,
  },
  hybrid: {
    length: 101,
    toe: 52.29,
    depth: 68.5,
    height: 37.5,
    faceHeight: 28.2,
    loft: 18.8,
    lie: 57.7,
    progression: 15.3,
    topline: 0,
    sole: 0,
    bounce: 0,
  },
  iron: {
    length: 79.4,
    toe: 44.24,
    depth: 0,
    height: 49.2,
    faceHeight: 44.4,
    loft: 31.4,
    lie: 62.3,
    progression: 3.03,
    topline: 5.83,
    sole: 20.6,
    bounce: 6.09,
  },
  wedge: {
    length: 80,
    toe: 45,
    depth: 0,
    height: 56.6,
    faceHeight: 51.8,
    loft: 56.3,
    lie: 64,
    progression: 4.55,
    topline: 5.83,
    sole: 23.84,
    bounce: 10.44,
  },
};

export function deliveryValue(shot: DeliveryShot, metric: string): number | null {
  const record = shot.metrics?.[metric];
  return record && record.status !== 'not_measured' && typeof record.value === 'number' && Number.isFinite(record.value)
    ? record.value
    : null;
}

export function direction(degrees: number, length = 86): [number, number] {
  const angle = (degrees * Math.PI) / 180;
  return [length * Math.cos(angle), -length * Math.sin(angle)];
}

export function faceDeliveryRotation(shot: DeliveryShot, category: FaceCategory): number {
  const lie = deliveryValue(shot, 'dynamic_lie');
  if (lie === null) return 0;
  const loft = deliveryValue(shot, 'dynamic_loft') ?? CLUB_PROFILES[category].loft;
  const radians = Math.PI / 180;
  const projected = Math.atan2(Math.sin(lie * radians) * Math.cos(loft * radians), Math.cos(lie * radians)) / radians;
  return FACE_SHAPES[category].hosel.lieDeg - projected;
}

const n = (value: number) => Number(value.toFixed(3));
const point = (x: number, y: number) => `${n(x)} ${n(y)}`;

// The source paths use absolute M/L/C pairs; an affine projection preserves their fair curves.
function bladePlan(path: string, scale: number, offset = 0): string {
  return path.replace(/(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)/g, (_, x: string, y: string) =>
    point(Number(y) * scale + offset, Number(x))
  );
}

export function clubProfilePaths(category: FaceCategory) {
  const p = CLUB_PROFILES[category];
  const wood = p.depth > 0;
  const toe = p.toe;
  const heel = p.length - toe;
  const radians = (p.loft * Math.PI) / 180;
  const projection = p.faceHeight * Math.sin(radians);
  const depth = wood ? p.depth : projection + p.topline;
  const back = p.topline * Math.cos(radians);
  const top = wood
    ? `M 0 0 C 0 ${-toe * 0.65} -5 ${-toe} ${-depth * 0.36} ${-toe} C ${-depth * 0.74} ${-toe} ${-depth} ${-toe * 0.66} ${-depth} 0 C ${-depth} ${heel * 0.72} ${-depth * 0.65} ${heel} ${-depth * 0.34} ${heel} C -6 ${heel} 0 ${heel * 0.56} 0 0 Z`
    : bladePlan(FACE_SHAPES[category].face, Math.sin(radians) + back / p.faceHeight, -back / 2);
  const topFace = wood
    ? `M ${-depth * 0.36} ${-toe} C -5 ${-toe} 0 ${-toe * 0.65} 0 0 C 0 ${heel * 0.56} -6 ${heel} ${-depth * 0.34} ${heel} C ${-6 - projection * 0.3} ${heel} ${-projection} ${heel * 0.56} ${-projection} 0 C ${-projection} ${-toe * 0.65} ${-5 - projection * 0.3} ${-toe} ${-depth * 0.36} ${-toe} Z`
    : bladePlan(FACE_SHAPES[category].face, Math.sin(radians));
  const topFaceJoin = wood
    ? `M ${-depth * 0.36} ${-toe} C ${-5 - projection * 0.3} ${-toe} ${-projection} ${-toe * 0.65} ${-projection} 0 C ${-projection} ${heel * 0.56} ${-6 - projection * 0.3} ${heel} ${-depth * 0.34} ${heel}`
    : '';
  const [tx, ty] = [(-Math.sin(radians) * p.faceHeight) / 2, (-Math.cos(radians) * p.faceHeight) / 2];
  const [bx, by] = [-tx, -ty];
  const crown = -p.height + p.faceHeight / 2;
  const soleY = by + (wood ? 4 : p.sole * Math.tan((p.bounce * Math.PI) / 180));
  const side = wood
    ? `M ${point(tx, ty)} C ${point(tx - 5, crown)} ${point(-depth * 0.2, crown)} ${point(-depth * 0.4, crown + 3)} C ${point(-depth * 0.7, crown + 7)} ${point(-depth, 0)} ${point(-depth, by - 5)} C ${point(-depth, soleY)} ${point(-depth * 0.3, soleY)} ${point(bx - 4, by + 2)} Q ${point(bx + 1, by + 1)} ${point(bx, by - 2)} L ${point(tx, ty)} Z`
    : `M ${point(tx, ty)} Q ${point(tx - p.topline / 2, ty - 2)} ${point(tx - p.topline, ty)} C ${point(tx - p.topline, ty + p.faceHeight * 0.3)} ${point(bx - p.sole - 3, by - 14)} ${point(bx - p.sole, soleY - 3)} Q ${point(bx - p.sole / 2, soleY + 1)} ${point(bx - 2, by + 1)} Q ${point(bx + 1, by)} ${point(bx, by - 2)} L ${point(tx, ty)} Z`;
  const topShellTransform = `matrix(0 1 ${Math.sin(radians) + back / p.faceHeight} 0 ${-back / 2} 0)`;
  return {
    top,
    topFace,
    topFaceJoin,
    topShellTransform,
    side,
    sideFace: `M ${point(tx, ty)} L ${point(bx, by)}`,
    depth,
    heel,
    tx,
    ty,
    bx,
    by,
  };
}
