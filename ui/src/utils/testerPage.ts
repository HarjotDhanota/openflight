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
