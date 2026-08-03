/** Longest entry any conditions field needs: "-1013.25" is 8 characters. */
const MAX_LENGTH = 10;

/**
 * Apply one keypress to the numeric keypad's entry buffer.
 *
 * Lives apart from the component so the editing rules can be tested directly —
 * this repo's UI tests render to a string and have no jsdom, so there is no way
 * to press a button in a test.
 *
 * @param key A digit, ".", or one of "sign" | "backspace" | "clear".
 *   Anything else is ignored, so a stray physical keystroke is harmless.
 */
export function applyKeypadKey(current: string, key: string): string {
  if (key === 'clear') return '';
  if (key === 'backspace') return current.slice(0, -1);
  if (key === 'sign') return current.startsWith('-') ? current.slice(1) : `-${current}`;

  if (key === '.') {
    if (current.includes('.')) return current;
    // A bare ".5" parses fine but reads like a typo at arm's length.
    return current === '' || current === '-' ? `${current}0.` : `${current}.`;
  }

  if (!/^[0-9]$/.test(key)) return current;
  if (current.length >= MAX_LENGTH) return current;
  // "05" is never what anyone meant.
  if (current === '0') return key;
  if (current === '-0') return `-${key}`;
  return current + key;
}
