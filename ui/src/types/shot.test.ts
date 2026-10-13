import { describe, expect, it } from 'vitest';
import type { Shot } from './shot';
import {
  computeStats,
  computeSwingSpeedStats,
  excludeShotsByProfile,
  filterShotsByProfile,
  getSwingSpeedMph,
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

  it('marks a carry flown on model geometry (D39) as modelled, never measured', () => {
    expect(isCarryEstimated(carryShot({ carry_status: 'model_geometry', carry_spin_adjusted: 160 }))).toBe(true);
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

// The one missing-value rule inside the carry rule: a non-finite carry is no carry.
describe('reportedCarryYards with a non-finite carry', () => {
  const carryShot = (overrides: Partial<Shot>): Shot =>
    ({ ball_speed_mph: 150, estimated_carry_yards: 257, carry_spin_adjusted: null, ...overrides }) as Shot;

  it('a fusion shot with a non-finite simulated carry has no carry and no estimate mark', () => {
    for (const bad of [Number.NaN, Number.POSITIVE_INFINITY]) {
      const shot = carryShot({ carry_status: 'measured', carry_spin_adjusted: bad, estimated_carry_yards: null });
      expect(reportedCarryYards(shot)).toBeNull();
      expect(isCarryEstimated(shot)).toBe(false);
    }
  });

  it('an OPS-only shot with a non-finite committed carry keeps its labelled table estimate', () => {
    const shot = carryShot({ carry_status: 'estimated', carry_spin_adjusted: Number.NaN });
    expect(reportedCarryYards(shot)).toBe(257);
    expect(isCarryEstimated(shot)).toBe(true);
  });

  it('a non-finite table carry is not reported either', () => {
    expect(reportedCarryYards(carryShot({ carry_status: 'estimated', estimated_carry_yards: Number.NaN }))).toBeNull();
  });

  it('session stats leave a non-finite carry out of the average', () => {
    const stats = computeStats([
      carryShot({ carry_status: 'measured', carry_spin_adjusted: 240 }),
      carryShot({ carry_status: 'measured', carry_spin_adjusted: Number.NaN }),
    ]);
    expect(stats.avg_carry_est).toBe(240);
  });
});

describe('a shot whose core metrics the server nulled', () => {
  const shot = (ball: number | null, carry: number | null, extra: Partial<Shot> = {}): Shot =>
    ({
      ball_speed_mph: ball,
      estimated_carry_yards: carry,
      club_speed_mph: null,
      smash_factor: null,
      ...extra,
    }) as Shot;

  it('session stats use only the measured values and never show NaN', () => {
    const stats = computeStats([shot(100, 200), shot(null, null), shot(120, 240)]);

    expect(stats.shot_count).toBe(3);
    expect(stats.avg_ball_speed).toBe(110);
    expect(stats.max_ball_speed).toBe(120);
    expect(stats.min_ball_speed).toBe(100);
    expect(stats.avg_carry_est).toBe(220);
  });

  it('session stats with no measured ball speed or carry say so with null', () => {
    const stats = computeStats([shot(null, null)]);

    expect(stats.shot_count).toBe(1);
    expect(stats.avg_ball_speed).toBeNull();
    expect(stats.max_ball_speed).toBeNull();
    expect(stats.min_ball_speed).toBeNull();
    expect(stats.avg_carry_est).toBeNull();
  });

  it('an empty session has no measured stats: null, never 0 (B-M9)', () => {
    const stats = computeStats([]);

    expect(stats.shot_count).toBe(0);
    expect(stats.avg_ball_speed).toBeNull();
    expect(stats.max_ball_speed).toBeNull();
    expect(stats.min_ball_speed).toBeNull();
    expect(stats.avg_carry_est).toBeNull();
    expect(stats.avg_club_speed).toBeNull();
    expect(stats.avg_smash_factor).toBeNull();
    expect(stats.std_dev).toBeNull();
  });

  it('std_dev needs two measured ball speeds; with fewer it is null, never 0 (B-M9)', () => {
    expect(computeStats([shot(100, 200)]).std_dev).toBeNull();
    expect(computeStats([shot(100, 200), shot(null, null)]).std_dev).toBeNull();
    expect(computeStats([shot(100, 200), shot(120, 240)]).std_dev).toBeCloseTo(14.142135623730951, 12);
  });

  it('a swing with neither club nor ball speed has no swing speed and is left out of swing stats', () => {
    const swing = (club: number | null, ball: number | null) =>
      shot(ball, null, { mode: 'swing-speed', club: 'Swing Speed', club_speed_mph: club });

    expect(getSwingSpeedMph(swing(null, null))).toBeNull();
    expect(computeSwingSpeedStats([swing(90, null), swing(null, null), swing(80, null)])).toEqual({
      count: 2,
      last_speed_mph: 80,
      best_speed_mph: 90,
      avg_speed_mph: 85,
    });
  });
});
