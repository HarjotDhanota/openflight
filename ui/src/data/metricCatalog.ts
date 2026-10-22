import catalogJson from './metricCatalog.json';
import { METRIC_LABELS } from '../types/shot';
import type { MetricLabel, Shot, ShotMetric } from '../types/shot';

/**
 * Every golf metric the kiosk reports, in the server's order
 * (openflight.shot_metrics.METRICS). The JSON is generated from the server
 * (`uv run python -m openflight.shot_metrics`), and a server test fails when the
 * two drift apart.
 */
export type MetricGroup = 'ball' | 'club' | 'flight';

export interface MetricSpec {
  id: string;
  /** English name; the screen shows the translated `metricName.<id>`. */
  label: string;
  group: MetricGroup;
  unit: string;
  /** The Part E item that builds it, when it is not built yet (E5, E6). */
  pending: string | null;
}

export const METRIC_CATALOG: readonly MetricSpec[] = catalogJson.metrics as MetricSpec[];

/** The shot's record for one metric, or null for a shot from before `metrics` existed. */
export function shotMetric(shot: Shot | null | undefined, id: string): ShotMetric | null {
  const record = shot?.metrics?.[id];
  return record ?? null;
}

/** The labels that carry a value; `not_measured` shows its reason instead of a badge. */
export type ValueLabel = Exclude<MetricLabel, 'not_measured'>;

export function isValueLabel(status: string | null | undefined): status is ValueLabel {
  return status !== 'not_measured' && (METRIC_LABELS as readonly (string | null | undefined)[]).includes(status);
}
