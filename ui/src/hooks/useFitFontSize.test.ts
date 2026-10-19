import { afterEach, describe, expect, it, vi } from 'vitest';
import { applySharedFitFontSize, FIT_MARGIN_PX, rowOverflowsWidth } from './useFitFontSize';

function fakeRow(contentWidthAt16: number, boxWidth: number): HTMLElement {
  const row = {
    style: { fontSize: '16px' },
    children: [] as unknown as HTMLCollection,
    get clientWidth() {
      return boxWidth;
    },
    get scrollWidth() {
      const px = parseFloat(this.style.fontSize) || 16;
      return (contentWidthAt16 * px) / 16;
    },
    getBoundingClientRect() {
      return { left: 0, right: boxWidth, top: 0, bottom: 20, width: boxWidth, height: 20, x: 0, y: 0, toJSON() {} };
    },
  };
  return row as unknown as HTMLElement;
}

describe('applySharedFitFontSize', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('uses one size for every row, set by the widest value', () => {
    const short = fakeRow(50, 100);
    const wide = fakeRow(120, 100);
    vi.stubGlobal('getComputedStyle', (element: Element) => ({
      fontSize: (element as HTMLElement).style.fontSize || '16px',
    }));

    applySharedFitFontSize([short, wide]);

    expect(short.style.fontSize).toBe(wide.style.fontSize);
    expect(parseFloat(wide.style.fontSize)).toBeLessThan(16);
    expect(rowOverflowsWidth(wide)).toBe(false);
  });

  it('keeps the CSS size when every row already fits', () => {
    const a = fakeRow(40, 100);
    const b = fakeRow(60, 100);
    vi.stubGlobal('getComputedStyle', (element: Element) => ({
      fontSize: (element as HTMLElement).style.fontSize || '16px',
    }));

    applySharedFitFontSize([a, b]);

    expect(a.style.fontSize).toBe('');
    expect(b.style.fontSize).toBe('');
  });
});

/** A value row with a unit whose ink scales with the font, inside a card of fixed height. */
function rowWithUnit(contentWidthAt16: number, boxWidth: number, card?: { overflowAt16: number; height: number }) {
  class FakeElement {}
  vi.stubGlobal('HTMLElement', FakeElement);
  vi.stubGlobal('document', {
    createRange: () => {
      let node: { getBoundingClientRect: () => DOMRect } | null = null;
      return {
        selectNodeContents(target: typeof node) {
          node = target;
        },
        getBoundingClientRect() {
          return node!.getBoundingClientRect();
        },
      };
    },
  });
  const row = fakeRow(contentWidthAt16, boxWidth) as unknown as {
    style: { fontSize: string };
    children: unknown[];
    closest?: () => unknown;
  };
  const px = () => parseFloat(row.style.fontSize) || 16;
  const unit = Object.assign(new FakeElement(), {
    getBoundingClientRect: () => ({ left: 0, right: (contentWidthAt16 * px()) / 16 }) as DOMRect,
  });
  row.children = [unit];
  if (card) {
    // the card's content grows with the value's font: a long caption can push it past the tile
    row.closest = () => ({
      get scrollHeight() {
        return card.height + Math.max(0, card.overflowAt16 - (16 - px()) * 1.0);
      },
      clientHeight: card.height,
    });
  }
  return row as unknown as HTMLElement;
}

describe('applySharedFitFontSize keeps units and captions whole', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('leaves a few pixels spare so the last letter of a unit is never clipped', () => {
    vi.stubGlobal('getComputedStyle', (element: Element) => ({
      fontSize: (element as HTMLElement).style.fontSize || '16px',
    }));
    const row = rowWithUnit(120, 100);

    applySharedFitFontSize([row]);

    const px = parseFloat(row.style.fontSize);
    expect((120 * px) / 16).toBeLessThanOrEqual(100 - FIT_MARGIN_PX + 1e-6);
  });

  it('shrinks the values a little when a caption pushes a tile past its height', () => {
    vi.stubGlobal('getComputedStyle', (element: Element) => ({
      fontSize: (element as HTMLElement).style.fontSize || '16px',
    }));
    const row = rowWithUnit(40, 100, { overflowAt16: 3, height: 90 });

    applySharedFitFontSize([row]);

    const px = parseFloat(row.style.fontSize);
    expect(px).toBeLessThanOrEqual(14 + 1e-6); // 3 px over, within the 1 px slop
    expect(px).toBeGreaterThan(12);
  });

  it('never shrinks the values to nothing for a caption that cannot fit anyway', () => {
    vi.stubGlobal('getComputedStyle', (element: Element) => ({
      fontSize: (element as HTMLElement).style.fontSize || '16px',
    }));
    const row = rowWithUnit(40, 100, { overflowAt16: 50, height: 90 });

    applySharedFitFontSize([row]);

    expect(row.style.fontSize).toBe('');
  });
});
