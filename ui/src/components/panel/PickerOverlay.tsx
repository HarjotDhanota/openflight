import { useState, type CSSProperties } from 'react';
import { clubGroupLabel } from '../../i18n';
import { useI18n } from '../../i18n/useI18n';
import type { Handed, HandedSource } from '../../stores/useSystemStore';
import { PanelAction } from './PanelAction';
import { initialPickerSection, pickerGridRows, type PickerSection } from './pickerSections';

interface PickerOverlayProps {
  title: string;
  selectedId: string;
  sections: ReadonlyArray<PickerSection>;
  onSelect: (id: string) => void;
  onClose: () => void;
  /** Word-length labels (training implements) use a slightly smaller type size. */
  wide?: boolean;
  /** The golfer's hand, one tap from the clubs; absent for training implements. */
  hand?: {
    handed: Handed | null;
    source: HandedSource | null;
    onChange: (handed: Handed) => void;
  };
}

const HANDS: ReadonlyArray<[Handed, 'app.rightHanded' | 'app.leftHanded']> = [
  ['RH', 'app.rightHanded'],
  ['LH', 'app.leftHanded'],
];

/** Right or Left in one tap. A connected simulator's hand wins, so the choice locks while it does. */
function HandChoice({ handed, source, onChange }: NonNullable<PickerOverlayProps['hand']>) {
  const { t } = useI18n();
  const simulator = source === 'simulator';
  return (
    <div className="picker-overlay__hand" role="group" aria-label={t('picker.hand')}>
      {HANDS.map(([hand, label]) => {
        // right-handed until the kiosk says otherwise
        const active = (handed ?? 'RH') === hand;
        return (
          <PanelAction
            key={hand}
            variant={active ? 'primary' : 'secondary'}
            aria-pressed={active}
            disabled={simulator}
            onClick={() => {
              if (!active) onChange(hand);
            }}
          >
            {t(label)}
          </PanelAction>
        );
      })}
      {simulator ? <span className="picker-overlay__hand-note">{t('picker.handSimulator')}</span> : null}
    </div>
  );
}

/**
 * Full-screen picker from design doc 6a (`clubsOpen6`): a titled sheet of
 * hairline-bordered option buttons grouped by tab. Four columns span the
 * overlay; row height is capped so irons stay on screen.
 */
export function PickerOverlay({
  title,
  selectedId,
  sections,
  onSelect,
  onClose,
  wide = false,
  hand,
}: PickerOverlayProps) {
  const { t } = useI18n();
  const [sectionName, setSectionName] = useState(() => initialPickerSection(sections, selectedId));
  const activeSection = sections.find((section) => section.name === sectionName) ?? sections[0];
  const options = activeSection?.options ?? [];
  const rows = pickerGridRows(sections);

  return (
    <div
      className={`picker-overlay${wide ? ' picker-overlay--wide' : ''}`}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div className="picker-overlay__header">
        <span className="picker-overlay__title">{title}</span>
        {hand ? <HandChoice {...hand} /> : null}
        <button
          type="button"
          className="picker-overlay__close"
          onClick={onClose}
          aria-label={t('picker.close', { title })}
        >
          ✕
        </button>
      </div>
      {sections.length > 1 ? (
        <div className="picker-overlay__tabs" role="group" aria-label={t('picker.groups')}>
          {sections.map((section) => {
            const active = section.name === sectionName;
            return (
              <PanelAction
                key={section.name}
                variant={active ? 'primary' : 'secondary'}
                aria-pressed={active}
                onClick={() => setSectionName(section.name)}
              >
                {clubGroupLabel(section.name)}
              </PanelAction>
            );
          })}
        </div>
      ) : null}
      <div className="picker-overlay__body">
        <div className="picker-overlay__grid" style={{ '--picker-rows': String(rows) } as CSSProperties}>
          {options.map((option) => (
            <button
              key={option.id}
              type="button"
              className={`picker-overlay__option${option.id === selectedId ? ' picker-overlay__option--selected' : ''}`}
              aria-pressed={option.id === selectedId}
              onClick={() => onSelect(option.id)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
