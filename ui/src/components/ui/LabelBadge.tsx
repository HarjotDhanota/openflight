import { isValueLabel } from '../../data/metricCatalog';
import type { MetricLabel } from '../../types/shot';
import { t, type MessageKey } from '../../i18n';
import './LabelBadge.css';

interface LabelBadgeProps {
  status: MetricLabel | null | undefined;
  /** Short text (MEAS / CALC / MODEL / EST) for a tight tile; the full name is its title. */
  compact?: boolean;
}

/**
 * One badge per label, each visibly different (D38, D39):
 * measured is a plain outline, calculated a solid accent outline, model geometry
 * a dashed accent outline, and estimated a filled accent block with the ≈ mark.
 * Not measured has no badge: the metric shows why instead.
 */
export function LabelBadge({ status, compact = false }: LabelBadgeProps) {
  if (!isValueLabel(status)) return null;
  const name = t(`label.${status}` as MessageKey);
  const text = compact ? t(`labelShort.${status}` as MessageKey) : name;
  return (
    <span
      className={`label-badge label-badge--${status}${compact ? ' label-badge--compact' : ''}`}
      title={name}
      data-label={status}
    >
      {status === 'estimated' ? (
        <svg className="label-badge__mark" viewBox="0 0 16 10" aria-hidden="true">
          <path d="M1 3.1c2.2-1.6 4.4 1.6 6.6 0s4.4-1.6 6.6 0" />
          <path d="M1 7.4c2.2-1.6 4.4 1.6 6.6 0s4.4-1.6 6.6 0" />
        </svg>
      ) : null}
      <span className="label-badge__text" aria-hidden={compact ? 'true' : undefined}>
        {text}
      </span>
      {compact ? <span className="label-badge__sr">{name}</span> : null}
    </span>
  );
}
