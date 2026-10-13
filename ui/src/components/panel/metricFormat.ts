import type { MetricGroup } from '../../data/metricCatalog';
import type { MessageKey } from '../../i18n';
import { getHtmlLang } from '../../i18n';
import type { ShotMetric } from '../../types/shot';
import type { UnitSystem } from '../../utils/units';
import {
  NO_VALUE,
  convertDistanceFromYards,
  convertSpeedFromMph,
  getDistanceUnit,
  getSpeedUnit,
  isMeasured,
} from '../../utils/units';

export type MetricPanelView = 'club' | 'flight';

/** Which catalog groups each panel shows; together they show every metric once. */
export const PANEL_GROUPS: Record<MetricPanelView, readonly MetricGroup[]> = {
  club: ['club'],
  flight: ['ball', 'flight'],
};

export const GROUP_TITLE: Record<MetricGroup, MessageKey> = {
  ball: 'metrics.groupBall',
  club: 'metrics.groupClub',
  flight: 'metrics.groupFlight',
};

/** Horizontal metrics are signed (+ right of the target line). */
const SIGNED = new Set([
  'launch_horizontal',
  'spin_axis',
  'club_path',
  'attack_angle',
  'face_angle',
  'face_to_path',
  'd_plane_tilt',
  'swing_direction',
  'carry_side',
  'total_side',
  'curve',
  'low_point_distance',
  'low_point_height',
  'low_point_side',
]);

function signed(text: string, value: number, id: string | undefined): string {
  return id && SIGNED.has(id) && value >= 0 ? `+${text}` : text;
}

/** One value as [number, unit] in the chosen unit system; a missing value is [—, '']. */
export function formatMetricValue(record: ShotMetric, unitSystem: UnitSystem, id?: string): [string, string] {
  const value = record.value;
  if (!isMeasured(value)) return [NO_VALUE, ''];
  switch (record.unit) {
    case 'mph':
      return [convertSpeedFromMph(value, unitSystem).toFixed(1), getSpeedUnit(unitSystem)];
    case 'yd':
      return [signed(convertDistanceFromYards(value, unitSystem).toFixed(0), value, id), getDistanceUnit(unitSystem)];
    case 'deg':
      return [signed(value.toFixed(1), value, id), '°'];
    case 'rpm':
      return [value.toLocaleString(getHtmlLang(), { maximumFractionDigits: 0 }), 'rpm'];
    case 's':
      return [value.toFixed(2), 's'];
    case 'mm':
      return [signed(value.toFixed(0), value, id), 'mm'];
    case 'm':
      // the ball's distance (13 Oct): to the centimetre
      return [value.toFixed(2), 'm'];
    case '':
      return [value.toFixed(2), ''];
    default:
      return [value.toFixed(1), record.unit];
  }
}
