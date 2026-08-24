import { describe, expect, it } from 'vitest';
import { criteriaRoleLabel, overlayPath, rejectionTotal } from './model';

describe('Studio presentation model', () => {
  it('never presents the strobe as a buildable winner', () => {
    expect(criteriaRoleLabel('primary')).toBe('PRIMARY — PHASE A');
    expect(criteriaRoleLabel('comparison_only')).toBe('COMPARISON ONLY');
  });

  it('projects pixel coordinates into an SVG path without changing geometry', () => {
    expect(overlayPath([[1, 2], [3, 4], [5, 6]], true)).toBe('M 1 2 L 3 4 L 5 6 Z');
    expect(overlayPath([], false)).toBe('');
  });

  it('totals rejection categories for availability display', () => {
    expect(rejectionTotal({ fit_residual: 3, club_not_visible: 2 })).toBe(5);
  });
});
