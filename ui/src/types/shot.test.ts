import { describe, expect, it } from 'vitest';
import { computeStats, displayCarryYards, type Shot } from './shot';

const shot = (overrides: Partial<Shot> = {}): Shot =>
  ({
    ball_speed_mph: 150,
    club_speed_mph: 103,
    smash_factor: 1.46,
    estimated_carry_yards: 240,
    carry_spin_adjusted: 254,
    club: 'driver',
    timestamp: '2026-08-01T12:00:00Z',
    spin_rpm: 2700,
    spin_source: 'measured',
    ...overrides,
  }) as Shot;

describe('displayCarryYards', () => {
  it('prefers the figure the server actually resolved', () => {
    // carry_spin_adjusted is the one density has been applied to.
    expect(displayCarryYards(shot())).toBe(254);
  });

  it('falls back to the raw estimate when the server resolved nothing', () => {
    expect(displayCarryYards(shot({ carry_spin_adjusted: null }))).toBe(240);
  });

  it('is not fooled by a legitimate zero', () => {
    // ?? rather than ||: a 0-yard carry is a real (if bad) shot, and || would
    // silently swap it for the uncorrected estimate.
    expect(displayCarryYards(shot({ carry_spin_adjusted: 0 }))).toBe(0);
  });
});

describe('session stats', () => {
  it('average carry uses the same figure every other tab shows', () => {
    // Before this, the live view showed the density-corrected carry while the
    // shots list and the stats showed the uncorrected one, for the same shot.
    const stats = computeStats([
      shot({ estimated_carry_yards: 240, carry_spin_adjusted: 254 }),
      shot({ estimated_carry_yards: 200, carry_spin_adjusted: 214 }),
    ]);

    expect(stats.avg_carry_est).toBe(234);
  });
});
