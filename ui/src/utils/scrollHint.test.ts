import { describe, expect, it } from 'vitest';
import { scrollHint } from './scrollHint';

describe('scrollHint', () => {
  it('shows more to the right while tabs are cut off there', () => {
    expect(scrollHint({ scrollLeft: 0, clientWidth: 500, scrollWidth: 640 })).toEqual({ left: false, right: true });
  });

  it('shows more to the left once scrolled, and nothing at the end', () => {
    expect(scrollHint({ scrollLeft: 140, clientWidth: 500, scrollWidth: 640 })).toEqual({ left: true, right: false });
  });

  it('ignores a fraction of a pixel and a bar that fits', () => {
    expect(scrollHint({ scrollLeft: 0.4, clientWidth: 500, scrollWidth: 501 })).toEqual({ left: false, right: false });
    expect(scrollHint({ scrollLeft: 0, clientWidth: 0, scrollWidth: 0 })).toEqual({ left: false, right: false });
  });
});
