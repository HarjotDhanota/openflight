/**
 * Bring the tester page back: the window that opened this one, or this window goes
 * there (the unit's own screen, where the scoreboard is a separate full-screen window).
 */
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
