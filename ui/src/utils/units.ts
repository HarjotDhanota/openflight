export type UnitSystem = 'imperial' | 'metric';

const MPH_TO_KMH = 1.60934;
const YARDS_TO_METERS = 0.9144;
const IMPERIAL_SPEED_UNIT = 'mph';
const METRIC_SPEED_UNIT = 'km/h';
const IMPERIAL_DISTANCE_UNIT = 'yds';
const METRIC_DISTANCE_UNIT = 'm';

/** Placeholder for a metric the shot has no value for (the server sends a non-finite one as null). */
export const NO_VALUE = '—';

/**
 * The one missing-value rule: a metric is shown only when it is a finite number.
 * null (the server's not-measured), undefined, NaN and infinities are all missing.
 */
export function isMeasured(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

/** The unit to show beside a value: none when the value is missing (never "— mph"). */
export function unitIfMeasured(value: unknown, unit: string): string | undefined {
  return isMeasured(value) ? unit : undefined;
}

/** The first measured value, or null when none is. */
export function firstMeasured(...values: unknown[]): number | null {
  const found = values.find(isMeasured);
  return found === undefined ? null : found;
}

export function convertSpeedFromMph(speedMph: number, unitSystem: UnitSystem): number {
  if (unitSystem === 'metric') {
    return speedMph * MPH_TO_KMH;
  }

  return speedMph;
}

export function convertDistanceFromYards(distanceYards: number, unitSystem: UnitSystem): number {
  if (unitSystem === 'metric') {
    return distanceYards * YARDS_TO_METERS;
  }

  return distanceYards;
}

export function formatSpeed(speedMph: number | null | undefined, unitSystem: UnitSystem, digits = 1): string {
  if (!isMeasured(speedMph)) return NO_VALUE;
  return convertSpeedFromMph(speedMph, unitSystem).toFixed(digits);
}

export function formatDistance(distanceYards: number | null | undefined, unitSystem: UnitSystem, digits = 0): string {
  if (!isMeasured(distanceYards)) return NO_VALUE;
  return convertDistanceFromYards(distanceYards, unitSystem).toFixed(digits);
}

export function getSpeedUnit(unitSystem: UnitSystem): string {
  return unitSystem === 'metric' ? METRIC_SPEED_UNIT : IMPERIAL_SPEED_UNIT;
}

export function getDistanceUnit(unitSystem: UnitSystem): string {
  return unitSystem === 'metric' ? METRIC_DISTANCE_UNIT : IMPERIAL_DISTANCE_UNIT;
}

export function getUnitsLabel(unitSystem: UnitSystem): string {
  return `${getSpeedUnit(unitSystem)} / ${getDistanceUnit(unitSystem)}`;
}

export function formatCarryRange(carryRange: [number, number], unitSystem: UnitSystem): string {
  const min = formatDistance(carryRange[0], unitSystem, 0);
  const max = formatDistance(carryRange[1], unitSystem, 0);
  return `${min}-${max} ${getDistanceUnit(unitSystem)}`;
}
