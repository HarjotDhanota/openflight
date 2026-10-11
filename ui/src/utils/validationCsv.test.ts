import { describe, expect, it } from 'vitest';
import type { Shot } from '../types/shot';
import { getEmptyValidationEntry } from '../stores/useValidationStore';
import { buildValidationCsv, comparatorDifference, openflightSpeedMph } from './validationCsv';

const nulledShot = {
  timestamp: '2026-10-11T10:00:00Z',
  ball_speed_mph: null,
  club_speed_mph: null,
  club: 'driver',
} as unknown as Shot;

describe('validation CSV with a shot whose ball speed the server nulled', () => {
  it('has no OpenFlight speed and no difference, instead of throwing', () => {
    const entry = { ...getEmptyValidationEntry(), comparatorSpeed: '100' };

    expect(openflightSpeedMph(nulledShot)).toBeNull();
    expect(comparatorDifference(nulledShot, entry)).toBeNull();
  });

  it('exports the row with empty speed cells', () => {
    const csv = buildValidationCsv([nulledShot], {});
    const row = csv.trim().split('\n')[1].split(',');

    expect(row[5]).toBe('');
    expect(csv).not.toContain('NaN');
  });
});
