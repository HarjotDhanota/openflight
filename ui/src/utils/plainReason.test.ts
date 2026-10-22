import { describe, expect, it } from 'vitest';
import { COMING_LATER_METRICS, isComingLater, shortReason } from './plainReason';

describe('shortReason', () => {
  it.each([
    ['pending E5: impact location is not built yet (needs the 6 mm / 8 mm lens)', 'coming later'],
    ['no camera club delivery on this shot', 'the camera did not see the club'],
    [
      'the OPS club speed is a percentile over 4-18 ms before impact, not the full speed',
      'the radar measures speed along its line only',
    ],
    ["vertical launch source 'mock' is not a measured vertical launch", 'needs a measured launch angle'],
    ['not in this shot’s payload', 'not sent for this shot'],
  ])('says %j plainly', (reason, short) => {
    expect(shortReason(reason)).toBe(short);
  });

  it('keeps a short reason as it is', () => {
    expect(shortReason('no spin camera')).toBe('no spin camera');
  });

  it('cuts a long unknown reason at a word, never mid-word, with no ellipsis', () => {
    const reason = 'the trajectory fit did not converge within the allowed iterations for this shot';
    const short = shortReason(reason);
    expect(short.length).toBeLessThanOrEqual(44);
    expect(reason.startsWith(short)).toBe(true);
    expect(reason[short.length]).toBe(' ');
    expect(short).not.toContain('…');
  });

  it('has a word for a missing reason', () => {
    expect(shortReason(null)).toBe('not measured');
    expect(shortReason('  ')).toBe('not measured');
  });
});

describe('isComingLater', () => {
  it('folds the not-built metrics while they have no value', () => {
    expect([...COMING_LATER_METRICS].sort()).toEqual(
      ['club_speed_full', 'dynamic_lie', 'gyro'].sort()
    );
    expect(isComingLater({ id: 'gyro', pending: null }, { status: 'not_measured', value: null })).toBe(true);
    expect(isComingLater({ id: 'dynamic_lie', pending: 'E6' }, undefined)).toBe(true);
    expect(isComingLater({ id: 'impact_offset', pending: null }, { status: 'not_measured', value: null })).toBe(
      false
    );
    expect(isComingLater({ id: 'some_new', pending: 'E9' }, { status: 'not_measured', value: null })).toBe(true);
  });

  it('shows one once it has a value', () => {
    expect(isComingLater({ id: 'gyro', pending: null }, { status: 'calculated', value: 2 })).toBe(false);
    expect(isComingLater({ id: 'carry', pending: null }, { status: 'not_measured', value: null })).toBe(false);
  });
});
