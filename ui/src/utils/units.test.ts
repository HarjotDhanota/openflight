import { describe, expect, it } from 'vitest';
import {
  convertDistanceFromYards,
  convertSpeedFromMph,
  formatCarryRange,
  formatDistance,
  stepInDisplayUnits,
  formatSpeed,
  getDistanceUnit,
  getSpeedUnit,
} from './units';

describe('units helpers', () => {
  it('preserves imperial speed values', () => {
    expect(convertSpeedFromMph(150, 'imperial')).toBe(150);
    expect(formatSpeed(150, 'imperial', 1)).toBe('150.0');
    expect(getSpeedUnit('imperial')).toBe('mph');
  });

  it('converts mph to km/h with stable rounding', () => {
    expect(convertSpeedFromMph(100, 'metric')).toBeCloseTo(160.934, 3);
    expect(formatSpeed(100, 'metric', 1)).toBe('160.9');
    expect(getSpeedUnit('metric')).toBe('km/h');
  });

  it('preserves imperial distance values', () => {
    expect(convertDistanceFromYards(250, 'imperial')).toBe(250);
    expect(formatDistance(250, 'imperial', 0)).toBe('250');
    expect(getDistanceUnit('imperial')).toBe('yds');
  });

  it('converts yards to meters with stable rounding', () => {
    expect(convertDistanceFromYards(100, 'metric')).toBeCloseTo(91.44, 2);
    expect(formatDistance(100, 'metric', 0)).toBe('91');
    expect(getDistanceUnit('metric')).toBe('m');
  });

  it('formats carry ranges in the selected unit system', () => {
    expect(formatCarryRange([200, 220], 'imperial')).toBe('200-220 yds');
    expect(formatCarryRange([200, 220], 'metric')).toBe('183-201 m');
  });
});

describe('stepInDisplayUnits', () => {
  // The kiosk has no on-screen keyboard, so +/- buttons are the primary way
  // values get entered on the panel. The arithmetic happens in DISPLAY units
  // and converts back to SI, so a step is a round number to the user rather
  // than a round number in Celsius that lands on 71.6 F.
  const toF = (c: number) => (c * 9) / 5 + 32;
  const fromF = (f: number) => ((f - 32) * 5) / 9;

  it('steps by whole display units, not SI units', () => {
    const next = stepInDisplayUnits(20, 0, 1, 1, toF, fromF);

    // 20 C is 68 F; one step up is 69 F, which is 20.56 C -- not 21 C.
    expect(toF(next)).toBeCloseTo(69, 6);
  });

  it('steps down', () => {
    const next = stepInDisplayUnits(20, 0, -1, 1, toF, fromF);

    expect(toF(next)).toBeCloseTo(67, 6);
  });

  it('is a no-op round trip at zero delta', () => {
    expect(stepInDisplayUnits(20, 0, 0, 1, toF, fromF)).toBeCloseTo(20, 6);
  });

  it('starts from the fallback when the field is blank', () => {
    // Stepping an empty pressure box must not start from 0 hPa, which is not a
    // pressure any atmosphere has and would be rejected downstream.
    const next = stepInDisplayUnits(null, 1013.25, 1, 0, identity, identity);

    expect(next).toBeCloseTo(1014, 6);
  });

  it('rounds to the field precision so repeated steps do not drift', () => {
    let v: number | null = 20;
    for (let i = 0; i < 10; i++) v = stepInDisplayUnits(v, 0, 1, 1, toF, fromF);

    expect(toF(v)).toBeCloseTo(78, 6);
  });

  it('keeps identity conversions exact', () => {
    expect(stepInDisplayUnits(50, 0, 5, 0, identity, identity)).toBe(55);
  });
});

const identity = (v: number) => v;
