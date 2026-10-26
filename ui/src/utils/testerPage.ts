/** The tester page back: the window that opened this one, else this window goes there. */
export function openTesterPage(url: string | null | undefined, hash = '', target: Window = window): boolean {
  const opener = target.opener as Window | null;
  if (opener && !opener.closed) {
    opener.focus();
    return true;
  }
  if (!url) return false;
  target.location.assign(`${url}${hash}`);
  return true;
}

/**
 * The tester page showed this kiosk in its own window (it navigates here with
 * `?from=tester`): on the unit's screen with `start-tester.sh --on-screen`, one window
 * switches between the two. A kiosk in its own window (the tester page on a phone)
 * has no such address.
 */
export function openedFromTester(target: Window = window): boolean {
  return new URLSearchParams(target.location.search).get('from') === 'tester';
}

/** Free play stopped: the window the tester page lent goes back to it; any other stays. */
export function returnToTesterPage(url: string | null | undefined, target: Window = window): boolean {
  if (!url || !openedFromTester(target)) return false;
  target.location.assign(url);
  return true;
}

/** The kiosk's GET /api/tester-page (src/openflight/tester_link.py). */
export interface TesterPageAnswer {
  available: boolean;
  url: string | null;
  reason: string | null;
  /** nothing will change for this kiosk (no tester started it): stop asking */
  final: boolean;
}

/** A new answer replaces the last; no answer at all (the kiosk is going) keeps it. */
export function nextTesterPage(
  previous: TesterPageAnswer | null,
  answer: TesterPageAnswer | null
): TesterPageAnswer | null {
  return answer ?? previous;
}
