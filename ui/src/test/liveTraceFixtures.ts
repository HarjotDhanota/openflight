/**
 * Shared fixtures for the jsdom tests of the stage 1b post-render acknowledgement
 * (LivePanel and the App wiring): one traced shot, its trace tag, and real-frame waits.
 */
import { act } from 'react';
import { vi } from 'vitest';
import type { Shot } from '../types/shot';
import type { TraceTag } from '../services/paintTrace';

export const TS = '2026-10-11T10:00:00Z';
export const CORE_FIELDS = [
  'ball_speed_mph',
  'launch_angle_vertical',
  'launch_angle_vertical_source',
  'launch_angle_horizontal',
  'launch_angle_horizontal_source',
  'launch_angle_horizontal_status',
];

export function makeShot(overrides: Partial<Shot> = {}): Shot {
  return {
    ball_speed_mph: 121.4,
    club_speed_mph: 88,
    smash_factor: 1.38,
    estimated_carry_yards: 210,
    carry_range: [205, 215],
    club: 'driver',
    timestamp: TS,
    peak_magnitude: 100,
    launch_angle_vertical: 13.4,
    launch_angle_horizontal: -1.2,
    launch_angle_vertical_source: 'radar',
    launch_angle_horizontal_source: 'radar',
    launch_angle_horizontal_status: 'measured',
    launch_angle_confidence: 0.8,
    angle_source: 'radar',
    club_angle_deg: 2.1,
    club_path_deg: -0.6,
    spin_axis_deg: 3.4,
    spin_rpm: 2650,
    spin_confidence: 0.9,
    spin_quality: 'high',
    spin_source: 'measured',
    spin_method: null,
    carry_spin_adjusted: 214,
    profile_id: 'james',
    profile_name: 'James',
    ...overrides,
  } as Shot;
}

export const traceTag = (
  revision: number,
  updateKind: TraceTag['update_kind'] = 'first',
  attemptId = 't-00007'
): TraceTag => ({
  attempt_id: attemptId,
  revision,
  final: revision > 1,
  update_kind: updateKind,
  core_eligible: false,
  core_fields: CORE_FIELDS,
});

/** Let the real rAF callback and the MessageChannel task after it run. */
export async function nextFrames(count = 3): Promise<void> {
  for (let i = 0; i < count; i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
  }
}

/** jsdom has no layout observers or font loading; the live grid's font fitting uses them. */
export function stubLayoutGlobals(): void {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    }
  );
  Object.defineProperty(document, 'fonts', { configurable: true, value: { ready: Promise.resolve() } });
}
