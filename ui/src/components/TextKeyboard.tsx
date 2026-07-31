import { useEffect, useState } from 'react';
import './NumericKeypad.css';
import './TextKeyboard.css';

const ROWS = [
  ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
  ['q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p'],
  ['a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l'],
  ['z', 'x', 'c', 'v', 'b', 'n', 'm', '-'],
];

/**
 * On-screen keyboard for the location search.
 *
 * The kiosk panel is touch-only and Raspberry Pi OS Chromium ships without an
 * on-screen keyboard, so this is the only way to type a place name there. The
 * digits row is not decoration: Open-Meteo's geocoding accepts postal codes,
 * which is often the faster way in.
 *
 * Live rather than modal, unlike the numeric keypad -- results update under
 * the field as you type, so hiding them behind an overlay would defeat it.
 * A physical keyboard works too, for anyone on a phone or desktop.
 */
export function TextKeyboard({
  value,
  onChange,
  onDone,
}: {
  value: string;
  onChange: (value: string) => void;
  onDone: () => void;
}) {
  const [shift, setShift] = useState(false);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Enter') return onDone();
      if (event.key === 'Backspace') return onChange(value.slice(0, -1));
      if (event.key.length === 1) onChange(value + event.key);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [value, onChange, onDone]);

  const press = (key: string) => {
    onChange(value + (shift ? key.toUpperCase() : key));
    setShift(false);
  };

  return (
    <div className="text-keyboard">
      {ROWS.map((row, index) => (
        <div className="text-keyboard__row" key={index}>
          {index === ROWS.length - 1 && (
            <button
              type="button"
              className={`keypad__key text-keyboard__key text-keyboard__key--mod ${
                shift ? 'text-keyboard__key--on' : ''
              }`}
              aria-label="Shift"
              aria-pressed={shift}
              onClick={() => setShift((on) => !on)}
            >
              ⇧
            </button>
          )}
          {row.map((key) => (
            <button
              key={key}
              type="button"
              className="keypad__key text-keyboard__key"
              aria-label={key}
              onClick={() => press(key)}
            >
              {shift ? key.toUpperCase() : key}
            </button>
          ))}
          {index === ROWS.length - 1 && (
            <button
              type="button"
              className="keypad__key text-keyboard__key text-keyboard__key--mod"
              aria-label="Backspace"
              onClick={() => onChange(value.slice(0, -1))}
            >
              ⌫
            </button>
          )}
        </div>
      ))}
      <div className="text-keyboard__row">
        <button
          type="button"
          className="keypad__key text-keyboard__key text-keyboard__key--space"
          aria-label="Space"
          onClick={() => press(' ')}
        >
          space
        </button>
        <button
          type="button"
          className="keypad__key text-keyboard__key text-keyboard__key--mod"
          aria-label="Clear search"
          onClick={() => onChange('')}
        >
          clear
        </button>
        <button type="button" className="keypad__key text-keyboard__key text-keyboard__key--done" onClick={onDone}>
          Done
        </button>
      </div>
    </div>
  );
}
