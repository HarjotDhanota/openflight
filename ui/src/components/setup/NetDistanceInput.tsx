import { useState } from 'react';
import { useI18n } from '../../i18n/useI18n';
import { PanelAction } from '../panel/PanelAction';

interface Props {
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
}

export function NetDistanceInput({ value, onChange, disabled }: Props) {
  const { t } = useI18n();
  const [editing, setEditing] = useState(false);
  return (
    <div className="setup-net">
      <label htmlFor="ball-to-net">{t('setup.net.label')}</label>
      <div className="setup-net__row">
        <input
          id="ball-to-net"
          inputMode="decimal"
          value={value}
          placeholder="11"
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
        />
        <PanelAction variant="secondary" disabled={disabled} onClick={() => setEditing(true)}>
          {t('setup.net.keypad')}
        </PanelAction>
      </div>
      <span className="setup-net__help">{t('setup.net.help')}</span>
      {editing ? (
        <div className="setup-net__backdrop">
          <div
            className="setup-net__dialog"
            role="dialog"
            aria-modal="true"
            aria-label={t('setup.net.label')}
            onKeyDown={(event) => {
              if (event.key === 'Escape') setEditing(false);
            }}
          >
            <label htmlFor="net-distance-editor">{t('setup.net.label')}</label>
            <input
              id="net-distance-editor"
              aria-label={t('setup.net.feet')}
              inputMode="none"
              autoFocus
              value={value}
              placeholder="11"
              onChange={(event) => onChange(event.target.value)}
            />
            <div className="setup-net__keys">
              {['1', '2', '3', '4', '5', '6', '7', '8', '9', '.', '0'].map((key) => (
                <button type="button" key={key} onClick={() => onChange(value + key)}>
                  {key}
                </button>
              ))}
              <button type="button" aria-label={t('keyboard.backspace')} onClick={() => onChange(value.slice(0, -1))}>
                ⌫
              </button>
            </div>
            <div className="setup-net__row">
              <PanelAction variant="secondary" onClick={() => onChange('')}>
                {t('setup.net.clear')}
              </PanelAction>
              <PanelAction onClick={() => setEditing(false)}>{t('setup.done')}</PanelAction>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
