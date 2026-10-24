/** Rounding slack: a sub-pixel overflow is not more content. */
const SLACK_PX = 2;

/** Whether a horizontal scroller has content cut off on each side. */
export function scrollHint(box: { scrollLeft: number; clientWidth: number; scrollWidth: number }): {
  left: boolean;
  right: boolean;
} {
  return {
    left: box.scrollLeft > SLACK_PX,
    right: box.scrollLeft + box.clientWidth < box.scrollWidth - SLACK_PX,
  };
}
