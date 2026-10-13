import { describe, expect, it } from 'vitest';
import { parseTargetLine, targetLineYawText } from './targetLine';

describe('parseTargetLine', () => {
  it('reads a set target line from GET /api/target-line', () => {
    expect(parseTargetLine({ status: 'set', yaw_deg: 2.14, total_uncertainty_deg: 0.8, source: 'aim_point' })).toEqual({
      status: 'set',
      yawDeg: 2.14,
      uncertaintyDeg: 0.8,
    });
  });

  it('reads a target line that is not set', () => {
    expect(parseTargetLine({ status: 'not_set', yaw_deg: null, total_uncertainty_deg: null, source: null })).toEqual({
      status: 'not_set',
      yawDeg: null,
      uncertaintyDeg: null,
    });
  });

  it('ignores anything else', () => {
    expect(parseTargetLine(null)).toBeNull();
    expect(parseTargetLine({ error: 'not found' })).toBeNull();
    expect(parseTargetLine({ status: 'set', yaw_deg: 'x' })).toEqual({
      status: 'set',
      yawDeg: null,
      uncertaintyDeg: null,
    });
  });
});

describe('targetLineYawText', () => {
  it('signs the yaw, + right', () => {
    expect(targetLineYawText(2.14)).toBe('+2.1°');
    expect(targetLineYawText(-0.96)).toBe('-1.0°');
    expect(targetLineYawText(0)).toBe('0.0°');
  });
});
