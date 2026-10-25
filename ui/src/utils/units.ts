export type UnitSystem = 'imperial' | 'metric';

const MPH_TO_KMH = 1.60934;
const YARDS_TO_METERS = 0.9144;
const IMPERIAL_SPEED_UNIT = 'mph';
const METRIC_SPEED_UNIT = 'km/h';
const IMPERIAL_DISTANCE_UNIT = 'yds';
const METRIC_DISTANCE_UNIT = 'm';

/** Placeholder for a metric the shot has no value for. */
export const NO_VALUE = '—';

/** The one missing-value rule: a metric is shown only when it is a finite number. */
export function isMeasured(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

/** The unit to show beside a value: none when the value is missing. */
export function unitIfMeasured(value: unknown, unit: string): string | undefined {
  return isMeasured(value) ? unit : undefined;
}

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

const HPA_TO_INHG = 0.02953;
const FEET_TO_METERS = 0.3048;

export function formatTemperature(temperatureC: number, unitSystem: UnitSystem): string {
  return unitSystem === 'metric' ? `${temperatureC.toFixed(1)} °C` : `${(temperatureC * 1.8 + 32).toFixed(1)} °F`;
}

/** inHg to 0.01 and hPa to 1 are about the same step. */
export function formatPressure(pressureHpa: number, unitSystem: UnitSystem): string {
  return unitSystem === 'metric' ? `${pressureHpa.toFixed(0)} hPa` : `${(pressureHpa * HPA_TO_INHG).toFixed(2)} inHg`;
}

export function formatAltitude(altitudeFt: number, unitSystem: UnitSystem): string {
  const value = unitSystem === 'metric' ? altitudeFt * FEET_TO_METERS : altitudeFt;
  return `${Math.round(value).toLocaleString('en-US')} ${unitSystem === 'metric' ? 'm' : 'ft'}`;
}
