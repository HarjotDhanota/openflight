import { useEffect, useState } from 'react';
import { applyKeypadKey } from '../utils/keypad';
import './NumericKeypad.css';

const DIGIT_LABELS: Record<string, string> = {
  '.': 'Decimal point',
  sign: 'Toggle negative',
  backspace: 'Backspace',
};

const ROWS = [
  ['7', '8', '9'],
  ['4', '5', '6'],
  ['1', '2', '3'],
  ['sign', '0', '.'],
];

const GLYPHS: Record<string, string> = { sign: '±', backspace: '⌫' };

/**
 * Full-screen numeric keypad for entering a single field.
 *
 * The kiosk is touch-only and Raspberry Pi OS Chromium ships without an
 * on-screen keyboard, so without this there is no way to type a value on the
 * panel at all. The +/- steppers remain for nudging; this is for entering a
 * number outright, which stepping is far too slow for.
 *
 * A physical keyboard works too while it is open, so a desk setup is not made
 * worse by a control designed for the panel.
 */
export function NumericKeypad({
  label,
  initial,
  onCommit,
  onCancel,
}: {
  label: string;
  initial: string;
  onCommit: (value: string) => void;
  onCancel: () => void;
}) {
  const [entry, setEntry] = useState(initial);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Enter') return onCommit(entry);
      if (event.key === 'Escape') return onCancel();
      const key = event.key === 'Backspace' ? 'backspace' : event.key === '-' ? 'sign' : event.key;
      const next = applyKeypadKey(entry, key);
      if (next !== entry) {
        event.preventDefault();
        setEntry(next);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [entry, onCommit, onCancel]);

  const press = (key: string) => setEntry((current) => applyKeypadKey(current, key));

  return (
    <div className="keypad" role="dialog" aria-modal="true" aria-label={label}>
      <div className="keypad__panel">
        <div className="keypad__header">
          <span className="keypad__label">{label}</span>
          <span className={`keypad__entry ${entry === '' ? 'keypad__entry--empty' : ''}`}>
            {entry === '' ? 'not set' : entry}
          </span>
        </div>

        <div className="keypad__grid">
          {ROWS.flat().map((key) => (
            <button
              key={key}
              type="button"
              className="keypad__key"
              aria-label={DIGIT_LABELS[key] ?? key}
              onClick={() => press(key)}
            >
              {GLYPHS[key] ?? key}
            </button>
          ))}
          <button
            type="button"
            className="keypad__key keypad__key--wide"
            aria-label={DIGIT_LABELS.backspace}
            onClick={() => press('backspace')}
          >
            {GLYPHS.backspace}
          </button>
        </div>

        <div className="keypad__actions">
          <button type="button" className="keypad__action" onClick={() => press('clear')}>
            Clear
          </button>
          <button type="button" className="keypad__action" onClick={onCancel}>
            Cancel
          </button>
          <button type="button" className="keypad__action keypad__action--primary" onClick={() => onCommit(entry)}>
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
