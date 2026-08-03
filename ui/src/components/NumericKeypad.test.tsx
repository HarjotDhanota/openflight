import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { applyKeypadKey } from '../utils/keypad';
import { NumericKeypad } from './NumericKeypad';

describe('applyKeypadKey', () => {
  it('appends digits', () => {
    expect(applyKeypadKey('7', '2')).toBe('72');
  });

  it('starts from empty', () => {
    expect(applyKeypadKey('', '9')).toBe('9');
  });

  it('adds a decimal point', () => {
    expect(applyKeypadKey('29', '.')).toBe('29.');
  });

  it('refuses a second decimal point', () => {
    expect(applyKeypadKey('29.9', '.')).toBe('29.9');
  });

  it('leads a bare decimal point with a zero', () => {
    // ".5" parses fine in JS but reads like a typo on a 7" panel.
    expect(applyKeypadKey('', '.')).toBe('0.');
  });

  it('replaces a lone leading zero rather than making "05"', () => {
    expect(applyKeypadKey('0', '5')).toBe('5');
  });

  it('keeps the zero when a decimal follows it', () => {
    expect(applyKeypadKey('0.', '5')).toBe('0.5');
  });

  it('toggles sign on', () => {
    // Below freezing is a real temperature, and the field stores Celsius.
    expect(applyKeypadKey('4', 'sign')).toBe('-4');
  });

  it('toggles sign off again', () => {
    expect(applyKeypadKey('-4', 'sign')).toBe('4');
  });

  it('can go negative from empty', () => {
    expect(applyKeypadKey('', 'sign')).toBe('-');
  });

  it('backspaces one character', () => {
    expect(applyKeypadKey('291', 'backspace')).toBe('29');
  });

  it('backspacing an empty entry is a no-op rather than an error', () => {
    expect(applyKeypadKey('', 'backspace')).toBe('');
  });

  it('clears everything', () => {
    expect(applyKeypadKey('1013.25', 'clear')).toBe('');
  });

  it('ignores keys it does not recognise', () => {
    expect(applyKeypadKey('42', 'q')).toBe('42');
  });

  it('caps the length so a stuck touch cannot overflow the display', () => {
    const long = '1'.repeat(10);
    expect(applyKeypadKey(long, '1')).toBe(long);
  });
});

describe('NumericKeypad', () => {
  const render = (props: Partial<Parameters<typeof NumericKeypad>[0]> = {}) =>
    renderToString(
      <NumericKeypad label="Pressure (hPa)" initial="1013" onCommit={() => {}} onCancel={() => {}} {...props} />
    );

  it('names the field being edited', () => {
    expect(render()).toContain('Pressure (hPa)');
  });

  it('shows the value being edited', () => {
    expect(render()).toContain('1013');
  });

  it('offers every digit', () => {
    const html = render();

    for (const digit of ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9']) {
      expect(html).toContain(`aria-label="${digit}"`);
    }
  });

  it('offers decimal, sign, backspace, clear and done', () => {
    const html = render();

    expect(html).toContain('aria-label="Decimal point"');
    expect(html).toContain('aria-label="Toggle negative"');
    expect(html).toContain('aria-label="Backspace"');
    expect(html).toContain('Clear');
    expect(html).toContain('Done');
  });

  it('offers a way out without committing', () => {
    expect(render()).toContain('Cancel');
  });

  it('shows a placeholder when the field started empty', () => {
    expect(render({ initial: '' })).toContain('keypad__entry--empty');
  });
});
