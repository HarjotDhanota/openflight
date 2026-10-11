import { describe, expect, it } from 'vitest';
import type { Shot } from './shot';
import {
  computeStats,
  filterShotsByProfile,
  excludeShotsByProfile,
  isCarryEstimated,
  reportedCarryYards,
} from './shot';

describe('filterShotsByProfile', () => {
  const shotWith = (profileId: string | undefined): Shot => ({ profile_id: profileId, ball_speed_mph: 100 }) as Shot;

  it('keeps only shots stamped with the given profile id', () => {
    const shots = [shotWith('aaa'), shotWith('bbb'), shotWith('aaa')];

    expect(filterShotsByProfile(shots, 'aaa')).toHaveLength(2);
  });

  it('matches exactly, without folding case', () => {
    const shots = [shotWith('AAA'), shotWith('aaa')];

    expect(filterShotsByProfile(shots, 'aaa')).toEqual([shots[1]]);
  });

  it('excludes unstamped shots from every profile', () => {
    const shots = [shotWith(undefined), shotWith('')];

    expect(filterShotsByProfile(shots, 'aaa')).toEqual([]);
    expect(filterShotsByProfile(shots, 'bbb')).toEqual([]);
  });

  it('returns nothing for a blank profile id', () => {
    const shots = [shotWith('aaa'), shotWith(undefined)];

    expect(filterShotsByProfile(shots, '')).toEqual([]);
  });
});

describe('excludeShotsByProfile', () => {
  const shotWith = (profileId: string | undefined): Shot => ({ profile_id: profileId, ball_speed_mph: 100 }) as Shot;

  it('drops only the given profile and keeps unstamped shots', () => {
    const shots = [shotWith('aaa'), shotWith('bbb'), shotWith(undefined)];

    expect(excludeShotsByProfile(shots, 'aaa')).toEqual([shots[1], shots[2]]);
  });

  it('excludes nothing for a blank profile id', () => {
    const shots = [shotWith('aaa'), shotWith('bbb')];

    expect(excludeShotsByProfile(shots, '')).toEqual(shots);
  });
});

// A fusion unit (IWR or camera) reports an unmeasured carry as not measured (D17);
// nothing falls back to the ball-speed table `estimated_carry_yards`.
describe('reportedCarryYards', () => {
  const carryShot = (overrides: Partial<Shot>): Shot =>
    ({ ball_speed_mph: 150, estimated_carry_yards: 257, carry_spin_adjusted: null, ...overrides }) as Shot;

  it('is null for a fusion shot whose carry is not measured, even beside a table value', () => {
    expect(reportedCarryYards(carryShot({ carry_status: 'not_measured' }))).toBeNull();
    expect(reportedCarryYards(carryShot({ carry_status: 'not_measured', carry_spin_adjusted: 250 }))).toBeNull();
  });

  it('is the simulated carry for a measured or calculated fusion shot, never the table', () => {
    expect(reportedCarryYards(carryShot({ carry_status: 'measured', carry_spin_adjusted: 244 }))).toBe(244);
    expect(reportedCarryYards(carryShot({ carry_status: 'calculated', carry_spin_adjusted: 238 }))).toBe(238);
    expect(reportedCarryYards(carryShot({ carry_status: 'measured' }))).toBeNull();
  });

  it('keeps the labelled estimate for OPS-only and older shots', () => {
    expect(reportedCarryYards(carryShot({ carry_status: 'estimated', carry_spin_adjusted: 229 }))).toBe(229);
    expect(reportedCarryYards(carryShot({ carry_status: 'estimated' }))).toBe(257);
    expect(reportedCarryYards(carryShot({}))).toBe(257);
    expect(reportedCarryYards(carryShot({ carry_status: null }))).toBe(257);
  });
});

describe('isCarryEstimated', () => {
  const carryShot = (overrides: Partial<Shot>): Shot =>
    ({ ball_speed_mph: 150, estimated_carry_yards: 257, carry_spin_adjusted: null, ...overrides }) as Shot;

  it('marks a calculated-spin carry and a bare table carry, not a measured one', () => {
    expect(isCarryEstimated(carryShot({ carry_status: 'measured', carry_spin_adjusted: 244 }))).toBe(false);
    expect(isCarryEstimated(carryShot({ carry_status: 'calculated', carry_spin_adjusted: 238 }))).toBe(true);
    expect(isCarryEstimated(carryShot({ carry_status: 'estimated' }))).toBe(true);
    expect(isCarryEstimated(carryShot({}))).toBe(true);
    expect(isCarryEstimated(carryShot({ carry_status: 'not_measured' }))).toBe(false);
  });
});

describe('computeStats carry', () => {
  const carryShot = (overrides: Partial<Shot>): Shot =>
    ({
      ball_speed_mph: 150,
      club_speed_mph: null,
      smash_factor: null,
      estimated_carry_yards: 257,
      carry_spin_adjusted: null,
      ...overrides,
    }) as Shot;

  it('averages only the carries the kiosk reports', () => {
    const stats = computeStats([
      carryShot({ carry_status: 'measured', carry_spin_adjusted: 240 }),
      carryShot({ carry_status: 'not_measured', estimated_carry_yards: null }),
      carryShot({ carry_status: 'not_measured' }),
    ]);

    expect(stats.avg_carry_est).toBe(240);
  });

  it('has no average carry when no shot has one', () => {
    expect(computeStats([carryShot({ carry_status: 'not_measured' })]).avg_carry_est).toBeNull();
  });
});
