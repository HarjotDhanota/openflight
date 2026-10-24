/** Short tester-facing words for a not-measured reason; public/tester.html keeps the same table. */
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
  words.pop();
  return words.join(' ');
}

/** Metrics not built yet, folded into one “Coming later” row while they have no value. */
export const COMING_LATER_METRICS: ReadonlySet<string> = new Set([
  'club_speed_full',
  'gyro',
  'dynamic_lie',
]);

export function isComingLater(
  spec: { id: string; pending: string | null },
  record: { status: string; value: number | null } | null | undefined
): boolean {
  const notBuilt = COMING_LATER_METRICS.has(spec.id) || Boolean(spec.pending);
  if (!notBuilt) return false;
  return !record || record.status === 'not_measured' || typeof record.value !== 'number';
}
