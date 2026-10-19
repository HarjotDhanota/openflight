import { useLayoutEffect, useRef, type RefObject } from 'react';

/**
 * Pixels a fitted row keeps spare at its right edge. A row that fit to the last
 * pixel (within the old 1 px slop) had its unit's final letter cut by the row's
 * overflow clip, and a kiosk's own font rendering can differ by a pixel or two:
 * "mph" was clipped on three-digit speeds at 1024×600.
 */
export const FIT_MARGIN_PX = 4;
/** A caption may shrink the values for height, never below this share of their fitted size. */
const HEIGHT_FIT_FLOOR = 0.6;

export function rowOverflowsWidth(row: HTMLElement, slop = 1, margin = 0): boolean {
  if (row.scrollWidth > row.clientWidth + slop) {
    return true;
  }
  const box = row.getBoundingClientRect();
  const children = [...row.children];
  for (let index = 0; index < children.length; index += 1) {
    const child = children[index];
    if (!(child instanceof HTMLElement)) {
      continue;
    }
    const range = document.createRange();
    range.selectNodeContents(child);
    const ink = range.getBoundingClientRect();
    const childBox = child.getBoundingClientRect();
    const right = Math.max(childBox.right, ink.right || 0);
    if (right > box.right - margin + slop) {
      return true;
    }
    const next = children[index + 1];
    if (next instanceof HTMLElement && ink.right > next.getBoundingClientRect().left + slop) {
      return true;
    }
  }
  return false;
}

type CardBox = { scrollHeight: number; clientHeight: number };

function cardOf(row: HTMLElement): CardBox | null {
  const closest = (row as { closest?: (selector: string) => unknown }).closest;
  if (typeof closest !== 'function') {
    return null;
  }
  return (closest.call(row, '.metric-card') as CardBox | null) ?? null;
}

/**
 * One size for every row: the CSS ceiling, or smaller if the widest row still
 * overflows, with ``FIT_MARGIN_PX`` to spare. When a tile's caption then pushes
 * its content past the tile's height (the club path's D-plane note beside the
 * ready light at 800×400), the values shrink a little more, down to
 * ``HEIGHT_FIT_FLOOR`` of their fitted size; a caption that cannot fit even then
 * leaves the values alone.
 */
export function applySharedFitFontSize(rows: HTMLElement[], minPx = 12): void {
  if (rows.length === 0) {
    return;
  }
  for (const row of rows) {
    row.style.fontSize = '';
  }
  const maxPx = Math.min(...rows.map((row) => parseFloat(getComputedStyle(row).fontSize)));
  if (!Number.isFinite(maxPx) || maxPx <= 0 || rows.some((row) => row.clientWidth <= 0)) {
    return;
  }
  const cards = rows.map(cardOf).filter((card): card is CardBox => card !== null);

  const apply = (px: number) => {
    for (const row of rows) {
      row.style.fontSize = `${px}px`;
    }
  };
  const widthFits = (px: number) => {
    apply(px);
    return rows.every((row) => !rowOverflowsWidth(row, 0, FIT_MARGIN_PX));
  };
  const heightFits = () => cards.every((card) => card.scrollHeight <= card.clientHeight + 1);
  const bothFit = (px: number) => widthFits(px) && heightFits();
  const largest = (low: number, high: number, fits: (px: number) => boolean) => {
    let best = low;
    for (let step = 0; step < 14; step += 1) {
      const mid = (low + high) / 2;
      if (fits(mid)) {
        best = mid;
        low = mid;
      } else {
        high = mid;
      }
    }
    return best;
  };

  let size = widthFits(maxPx) ? maxPx : largest(minPx, maxPx, widthFits);
  apply(size);
  if (cards.length && !heightFits()) {
    const floor = Math.max(minPx, size * HEIGHT_FIT_FLOOR);
    if (bothFit(floor)) {
      size = bothFit(size) ? size : largest(floor, size, bothFit);
    }
  }
  if (size === maxPx) {
    for (const row of rows) {
      row.style.fontSize = '';
    }
    return;
  }
  apply(size);
}

/**
 * Fit every `.metric-card__value-row` in a grid to the same font-size so Live
 * numbers stay consistent (181 and 2,328 look the same size).
 */
export function useSharedFitFontSize(enabled: boolean, token: string): RefObject<HTMLDivElement | null> {
  const ref = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const grid = ref.current;
    if (!enabled || !grid) {
      return undefined;
    }

    let cancelled = false;
    const mutationObserver = new MutationObserver(() => {
      fit();
    });
    const fit = () => {
      if (cancelled) {
        return;
      }
      mutationObserver.disconnect();
      applySharedFitFontSize([...grid.querySelectorAll<HTMLElement>('.metric-card__value-row')]);
      if (!cancelled) {
        mutationObserver.observe(grid, { subtree: true, characterData: true, childList: true });
      }
    };

    // cqi/cqb and webfonts can change size after the first layout; refit then.
    fit();
    const frame = requestAnimationFrame(() => {
      fit();
      requestAnimationFrame(fit);
    });
    const retry = window.setTimeout(fit, 50);
    void document.fonts.ready.then(fit);
    const resizeObserver = new ResizeObserver(fit);
    resizeObserver.observe(grid);
    return () => {
      cancelled = true;
      cancelAnimationFrame(frame);
      window.clearTimeout(retry);
      resizeObserver.disconnect();
      mutationObserver.disconnect();
      for (const row of grid.querySelectorAll<HTMLElement>('.metric-card__value-row')) {
        row.style.fontSize = '';
      }
    };
  }, [enabled, token]);

  return ref;
}
