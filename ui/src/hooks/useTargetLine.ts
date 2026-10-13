import { useEffect, useState } from 'react';
import { getServerOrigin } from '../utils/serverOrigin';
import { parseTargetLine, type TargetLine } from '../utils/targetLine';

const POLL_MS = 5000;

/**
 * Polls the kiosk's GET /api/target-line. Null until it answers, and null again
 * when it cannot (an older server without the route), so nothing is drawn then.
 */
export function useTargetLine(enabled = true): TargetLine | null {
  const [line, setLine] = useState<TargetLine | null>(null);
  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    let controller: AbortController | null = null;
    const read = async () => {
      controller?.abort();
      controller = new AbortController();
      try {
        const response = await fetch(`${getServerOrigin()}/api/target-line`, {
          cache: 'no-store',
          signal: controller.signal,
        });
        const body = response.ok ? await response.json() : null;
        if (!cancelled) setLine(parseTargetLine(body));
      } catch {
        if (!cancelled) setLine(null);
      }
    };
    void read();
    const timer = setInterval(read, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
      controller?.abort();
    };
  }, [enabled]);
  return line;
}
