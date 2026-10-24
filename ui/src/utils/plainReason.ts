/**
 * Tester-facing words for a metric's not-measured reason (13 Oct UX pass, item 9).
 * The server's reason is written for developers and is cut off by the cell; the
 * screen shows this short form and the full reason on a tap (a `title` tooltip
 * never shows on a touch screen). The tester page (public/tester.html) keeps the
 * same table.
 */
const PLAIN_REASONS: ReadonlyArray<readonly [RegExp, string]> = [
  [/^pending E\d+|not built yet/i, 'coming later'],
  [/no camera club delivery/i, 'the camera did not see the club'],
  [/percentile over .*before impact/i, 'the radar measures speed along its line only'],
  [/is not a measured vertical launch/i, 'needs a measured launch angle'],
  [/not in this shot.s payload/i, 'not sent for this shot'],
];

/** The longest short reason, so it fits two lines of a kiosk cell. */
const SHORT_MAX = 44;

export function shortReason(reason: string | null | undefined): string {
  const text = (reason ?? '').trim();
  if (!text) return 'not measured';
  for (const [pattern, short] of PLAIN_REASONS) {
    if (pattern.test(text)) return short;
  }
  if (text.length <= SHORT_MAX) return text;
  const words = text.slice(0, SHORT_MAX).split(' ');
  words.pop(); // never a word cut in half
  return words.join(' ');
}

/** Metrics not built yet, folded into one “Coming later” row while they have no value. */
export const COMING_LATER_METRICS: ReadonlySet<string> = new Set(['club_speed_full', 'gyro']);

export function isComingLater(
  spec: { id: string; pending: string | null },
  record: { status: string; value: number | null } | null | undefined
): boolean {
  const notBuilt = COMING_LATER_METRICS.has(spec.id) || Boolean(spec.pending);
  if (!notBuilt) return false;
  return !record || record.status === 'not_measured' || typeof record.value !== 'number';
}
