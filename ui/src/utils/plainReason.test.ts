import { describe, expect, it } from 'vitest';
import { shortReason } from './plainReason';

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
