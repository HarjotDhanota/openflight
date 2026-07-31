import { describe, expect, it } from 'vitest';
import { computeStats, displayCarryYards, type Shot } from './shot';

const shot = (overrides: Partial<Shot> = {}): Shot =>
  ({
    ball_speed_mph: 150,
    club_speed_mph: 103,
    smash_factor: 1.45,
    estimated_carry_yards: 250,
    carry_range: [240, 260],
    club: 'driver',
    timestamp: '2026-07-30T12:00:00Z',
    peak_magnitude: null,
    launch_angle_vertical: null,
    launch_angle_horizontal: null,
    launch_angle_confidence: null,
    angle_source: null,
    club_angle_deg: null,
    club_path_deg: null,
    spin_axis_deg: null,
    spin_rpm: null,
    spin_confidence: null,
    spin_quality: null,
    spin_source: null,
    carry_spin_adjusted: null,
    ...overrides,
  }) as Shot;

describe('displayCarryYards', () => {
  /**
   * There are two carry numbers on a shot and they mean different things.
   * `estimated_carry_yards` is the model-neutral table estimate that goes to
   * simulators, deliberately free of local air density. `carry_spin_adjusted`
   * is what OpenFlight itself computed for the air the ball actually flew
   * through. Every screen that shows a human a carry must show the same one,
   * or the Live tab and the Shots tab disagree about the shot just hit.
   */
  it('prefers the value computed for the real conditions', () => {
    expect(displayCarryYards(shot({ carry_spin_adjusted: 262 }))).toBe(262);
  });

  it('falls back to the table estimate when nothing better exists', () => {
    expect(displayCarryYards(shot({ carry_spin_adjusted: null }))).toBe(250);
  });

  it('does not treat a zero carry as missing', () => {
    // ?? not || : a duffed shot that carried nothing is still a real number.
    expect(displayCarryYards(shot({ carry_spin_adjusted: 0 }))).toBe(0);
  });
});

describe('computeStats', () => {
  it('averages the carry the user was shown, not the simulator figure', () => {
    const stats = computeStats([
      shot({ estimated_carry_yards: 250, carry_spin_adjusted: 262 }),
      shot({ estimated_carry_yards: 250, carry_spin_adjusted: 258 }),
    ]);

    expect(stats.avg_carry_est).toBe(260);
  });

  it('still works for shots that never got a corrected carry', () => {
    const stats = computeStats([shot({ estimated_carry_yards: 240 }), shot({ estimated_carry_yards: 260 })]);

    expect(stats.avg_carry_est).toBe(250);
  });

  it('handles a session with no shots', () => {
    expect(computeStats([]).avg_carry_est).toBe(0);
  });
});
