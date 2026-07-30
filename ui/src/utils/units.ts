export type UnitSystem = 'imperial' | 'metric';

const MPH_TO_KMH = 1.60934;
const YARDS_TO_METERS = 0.9144;
const IMPERIAL_SPEED_UNIT = 'mph';
const METRIC_SPEED_UNIT = 'km/h';
const IMPERIAL_DISTANCE_UNIT = 'yds';
const METRIC_DISTANCE_UNIT = 'm';

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

export function formatSpeed(speedMph: number, unitSystem: UnitSystem, digits = 1): string {
  return convertSpeedFromMph(speedMph, unitSystem).toFixed(digits);
}

export function formatDistance(distanceYards: number, unitSystem: UnitSystem, digits = 0): string {
  return convertDistanceFromYards(distanceYards, unitSystem).toFixed(digits);
}

export function getSpeedUnit(unitSystem: UnitSystem): string {
  return unitSystem === 'metric' ? METRIC_SPEED_UNIT : IMPERIAL_SPEED_UNIT;
}

export function getDistanceUnit(unitSystem: UnitSystem): string {
  return unitSystem === 'metric' ? METRIC_DISTANCE_UNIT : IMPERIAL_DISTANCE_UNIT;
}

export function formatCarryRange(carryRange: [number, number], unitSystem: UnitSystem): string {
  const min = formatDistance(carryRange[0], unitSystem, 0);
  const max = formatDistance(carryRange[1], unitSystem, 0);
  return `${min}-${max} ${getDistanceUnit(unitSystem)}`;
}

/* --- Environmental units ---------------------------------------------------
 * Weather values are held in SI internally (Celsius, hectopascals, metres)
 * because that is what the density model consumes and what the BME280 reports.
 * These convert for display and back again for entry, so the wire format never
 * depends on what the user happens to be looking at.
 */

const IMPERIAL_TEMP_UNIT = '°F';
const METRIC_TEMP_UNIT = '°C';
const IMPERIAL_PRESSURE_UNIT = 'inHg';
const METRIC_PRESSURE_UNIT = 'hPa';
const IMPERIAL_ELEVATION_UNIT = 'ft';
const METRIC_ELEVATION_UNIT = 'm';
const HPA_TO_INHG = 0.02952998;
const METERS_TO_FEET = 3.28084;

export function convertTempFromC(tempC: number, unitSystem: UnitSystem): number {
  return unitSystem === 'imperial' ? (tempC * 9) / 5 + 32 : tempC;
}

export function convertTempToC(value: number, unitSystem: UnitSystem): number {
  return unitSystem === 'imperial' ? ((value - 32) * 5) / 9 : value;
}

export function convertPressureFromHpa(pressureHpa: number, unitSystem: UnitSystem): number {
  return unitSystem === 'imperial' ? pressureHpa * HPA_TO_INHG : pressureHpa;
}

export function convertPressureToHpa(value: number, unitSystem: UnitSystem): number {
  return unitSystem === 'imperial' ? value / HPA_TO_INHG : value;
}

export function convertElevationFromMeters(elevationM: number, unitSystem: UnitSystem): number {
  return unitSystem === 'imperial' ? elevationM * METERS_TO_FEET : elevationM;
}

export function convertElevationToMeters(value: number, unitSystem: UnitSystem): number {
  return unitSystem === 'imperial' ? value / METERS_TO_FEET : value;
}

export function getTempUnit(unitSystem: UnitSystem): string {
  return unitSystem === 'imperial' ? IMPERIAL_TEMP_UNIT : METRIC_TEMP_UNIT;
}

export function getPressureUnit(unitSystem: UnitSystem): string {
  return unitSystem === 'imperial' ? IMPERIAL_PRESSURE_UNIT : METRIC_PRESSURE_UNIT;
}

export function getElevationUnit(unitSystem: UnitSystem): string {
  return unitSystem === 'imperial' ? IMPERIAL_ELEVATION_UNIT : METRIC_ELEVATION_UNIT;
}

/** Decimal places that make sense per unit. inHg needs 2, hPa needs 0. */
export function getPressureDigits(unitSystem: UnitSystem): number {
  return unitSystem === 'imperial' ? 2 : 0;
}

/**
 * Move a stored SI value by one step of the unit the user is looking at.
 *
 * The kiosk is touch-only with no on-screen keyboard, so +/- buttons are the
 * primary way conditions get entered on the panel. Stepping happens in display
 * units and converts back, so "one step" is a round number to the user — a
 * degree Fahrenheit — rather than a round number in Celsius that shows up as
 * 71.6 °F. Rounding to the field's own precision each time stops repeated
 * steps accumulating float drift.
 *
 * @param currentSi Current stored value, or null when the field is empty.
 * @param fallbackSi Where to start stepping from when the field is empty.
 *   Never 0 for pressure — 0 hPa is not a pressure any atmosphere has.
 * @param deltaDisplay Step size, in the unit being displayed.
 * @param digits Decimal places the field displays.
 */
export function stepInDisplayUnits(
  currentSi: number | null,
  fallbackSi: number,
  deltaDisplay: number,
  digits: number,
  toDisplay: (value: number) => number,
  fromDisplay: (value: number) => number
): number {
  const start = currentSi ?? fallbackSi;
  const stepped = Number((toDisplay(start) + deltaDisplay).toFixed(digits));
  return fromDisplay(stepped);
}
