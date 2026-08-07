import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { Shot } from '../types/shot';
import { ShotDisplay } from './ShotDisplay';
import { ShotList } from './ShotList';

const shot = (overrides: Partial<Shot> = {}): Shot =>
  ({
    ball_speed_mph: 165,
    club_speed_mph: 113,
    smash_factor: 1.46,
    estimated_carry_yards: 251,
    carry_range: [240, 262],
    club: 'driver',
    timestamp: '2026-07-30T12:00:00Z',
    peak_magnitude: null,
    launch_angle_vertical: 12.5,
    launch_angle_horizontal: null,
    launch_angle_confidence: 0.8,
    angle_source: 'radar',
    club_angle_deg: null,
    club_path_deg: null,
    spin_axis_deg: null,
    spin_rpm: 2600,
    spin_confidence: 0.9,
    spin_quality: 'high',
    spin_source: 'measured',
    carry_spin_adjusted: null,
    ...overrides,
  }) as Shot;

describe('ShotList', () => {
  it('shows the carry computed for the real conditions', () => {
    const html = renderToString(<ShotList shots={[shot({ carry_spin_adjusted: 262 })]} />);

    expect(html).toContain('262');
  });

  it('does not show the uncorrected simulator figure', () => {
    // `estimated_carry_yards` is kept free of local air density for the sim
    // handoff. Showing it here made the Shots tab contradict the Live tab.
    const html = renderToString(<ShotList shots={[shot({ estimated_carry_yards: 251, carry_spin_adjusted: 262 })]} />);

    expect(html).not.toContain('251');
  });

  it('falls back to the estimate when no corrected carry exists', () => {
    const html = renderToString(<ShotList shots={[shot({ carry_spin_adjusted: null })]} />);

    expect(html).toContain('251');
  });

  it('agrees with the Live tab about the shot just hit', () => {
    // The bug this pins: the same shot read 262 on Live and 251 on Shots.
    const hit = shot({ estimated_carry_yards: 251, carry_spin_adjusted: 262 });

    const live = renderToString(<ShotDisplay shot={hit} />);
    const list = renderToString(<ShotList shots={[hit]} />);

    expect(live).toContain('262');
    expect(list).toContain('262');
  });
});
