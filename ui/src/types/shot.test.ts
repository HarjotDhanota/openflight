import { describe, expect, it } from 'vitest';
import type { Shot } from './shot';
import {
  computeStats,
  computeSwingSpeedStats,
  excludeShotsByProfile,
  filterShotsByProfile,
  getSwingSpeedMph,
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
