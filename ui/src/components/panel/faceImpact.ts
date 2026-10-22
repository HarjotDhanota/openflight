import type { Shot, ShotMetric } from '../../types/shot';
import type { UnitSystem } from '../../utils/units';
import { isMeasured } from '../../utils/units';

export type FaceCategory = 'driver' | 'fairway' | 'hybrid' | 'iron' | 'wedge';

/** Our own illustrative drawings, toe left as seen from the target (right-handed), mm around
 * the face centre (outline midpoint + 8 mm toe-ward). Iron length within Mizuno's published
 * 7-iron head lengths (74-87 mm), woods within the USGA 127 x 71 mm limit. */
export interface FaceShape {
  widthMm: number;
  heightMm: number;
  centreHeightMm: number;
  outline: string;
  hosel: string;
  grooves: readonly number[];
  grooveSpan: readonly [number, number];
  bounds: { minX: number; maxX: number; minY: number; maxY: number };
}

export const FACE_SHAPES: Record<FaceCategory, FaceShape> = {
  driver: {
    widthMm: 110,
    heightMm: 58,
    centreHeightMm: 30,
    outline:
      'M 52 30 L -36 30 C -44 30 -47 24 -47 14 L -47 -10 C -47 -22 -40 -28 -26 -29 C 0 -31 30 -30 46 -26 ' +
      'C 58 -23 63 -15 63 -4 L 63 16 C 63 25 59 30 52 30 Z',
    hosel: 'M 50 -25 L 55 -39 L 62 -39 L 62.6 -12',
    grooves: [],
    grooveSpan: [0, 0],
    bounds: { minX: -54, maxX: 72, minY: -44, maxY: 36 },
  },
  fairway: {
    widthMm: 90,
    heightMm: 38,
    centreHeightMm: 17,
    outline:
      'M 46 17 L -28 17 C -34 17 -37 12 -37 4 L -37 -8 C -37 -17 -31 -21 -22 -21 L 40 -20 ' +
      'C 48 -20 53 -15 53 -8 L 53 6 C 53 13 51 17 46 17 Z',
    hosel: 'M 42 -20 L 47 -35 L 54 -35 L 53 -12',
    grooves: [11, 5, -1, -7],
    grooveSpan: [-24, 36],
    bounds: { minX: -44, maxX: 64, minY: -40, maxY: 23 },
  },
  hybrid: {
    widthMm: 78,
    heightMm: 38,
    centreHeightMm: 20,
    outline:
      'M 40 20 L -24 20 C -29 20 -31 15 -31 8 L -31 -6 C -31 -14 -26 -18 -18 -18 L 36 -17 ' +
      'C 43 -17 47 -12 47 -5 L 47 10 C 47 16 45 20 40 20 Z',
    hosel: 'M 38 -17 L 42 -33 L 49 -33 L 47.4 -9',
    grooves: [14, 8, 2, -4, -10],
    grooveSpan: [-20, 32],
    bounds: { minX: -38, maxX: 58, minY: -38, maxY: 26 },
  },
  iron: {
    widthMm: 81,
    heightMm: 48,
    centreHeightMm: 23,
    outline:
      'M 44 23 L -24 23 C -30 23 -33 18 -33 10 L -32 -14 C -31 -22 -27 -26 -21 -25 L 38 -9 ' +
      'L 45 -33 L 51 -33 L 50 -6 C 50 10 49 20 44 23 Z',
    hosel: '',
    grooves: [17, 11, 5, -1, -7],
    grooveSpan: [-24, 30],
    bounds: { minX: -40, maxX: 60, minY: -40, maxY: 29 },
  },
  wedge: {
    widthMm: 82,
    heightMm: 57,
    centreHeightMm: 24,
    outline:
      'M 44 24 L -24 24 C -31 24 -34 18 -34 8 L -33 -20 C -32 -30 -27 -34 -20 -33 L 38 -11 ' +
      'L 45 -35 L 51 -35 L 50 -7 C 50 10 49 21 44 24 Z',
    hosel: '',
    grooves: [18, 12, 6, 0, -6],
    grooveSpan: [-25, 30],
    bounds: { minX: -41, maxX: 60, minY: -42, maxY: 30 },
  },
};

export function faceCategory(club: string | null | undefined): FaceCategory {
  const name = (club ?? '').trim().toLowerCase();
  if (name === 'driver') return 'driver';
  if (name.endsWith('-wood')) return 'fairway';
  if (name.endsWith('-hybrid')) return 'hybrid';
  if (['pw', 'gw', 'sw', 'lw'].includes(name)) return 'wedge';
  return 'iron';
}

export function gridStepMm(unitSystem: UnitSystem): number {
  return unitSystem === 'imperial' ? 25.4 / 4 : 10;
}

export interface ImpactReading {
  offset: number | null;
  offsetSigma: number | null;
  offsetReason: string | null;
  height: number | null;
  heightSigma: number | null;
  heightReason: string | null;
  handedness: 'right' | 'left';
}

function axis(record: ShotMetric | undefined): [number | null, number | null, string | null] {
  if (!record || !isMeasured(record.value) || record.status === 'not_measured') {
    return [null, null, record?.reason || 'not in this shot’s payload'];
  }
  const sigma = record.range ? Math.abs(record.range[1] - record.range[0]) / 2 : null;
  return [record.value, sigma, null];
}

export function impactReading(shot: Shot): ImpactReading {
  const [offset, offsetSigma, offsetReason] = axis(shot.metrics?.impact_offset ?? undefined);
  const [height, heightSigma, heightReason] = axis(shot.metrics?.impact_height ?? undefined);
  return {
    offset,
    offsetSigma,
    offsetReason,
    height,
    heightSigma,
    heightReason,
    handedness: shot.impact_location?.handedness === 'left' ? 'left' : 'right',
  };
}

export function formatLength(mm: number, unitSystem: UnitSystem): string {
  return unitSystem === 'imperial' ? `${(mm / 25.4).toFixed(2)} in` : `${Math.round(mm)} mm`;
}
