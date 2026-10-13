import type { ReactNode } from 'react';
import { METRIC_CATALOG, type MetricSpec } from '../../data/metricCatalog';
import type { MessageKey } from '../../i18n';
import { useI18n } from '../../i18n/useI18n';
import type { Shot, ShotMetric } from '../../types/shot';
import { isSwingSpeedShot } from '../../types/shot';
import type { UnitSystem } from '../../utils/units';
import { isMeasured } from '../../utils/units';
import { GROUP_TITLE, PANEL_GROUPS, formatMetricValue, type MetricPanelView } from './metricFormat';
import { LabelBadge } from '../ui/LabelBadge';
import { PanelHeader } from './PanelHeader';
import './MetricGroupPanel.css';

/** A metric the payload lacks: shown as not measured, never left off the panel. */
function missingRecord(spec: MetricSpec): ShotMetric {
  return {
    value: null,
    unit: spec.unit,
    status: 'not_measured',
    source: null,
    range: null,
    reason: 'not in this shot’s payload',
    frame: null,
    note: null,
    normalized: null,
  };
}

function MetricCell({ spec, record, unitSystem }: { spec: MetricSpec; record: ShotMetric; unitSystem: UnitSystem }) {
  const { t } = useI18n();
  const [value, unit] = formatMetricValue(record, unitSystem, spec.id);
  const measured = isMeasured(record.value) && record.status !== 'not_measured';
  const details: string[] = [];
  if (measured) {
    if (record.range) {
      const [low] = formatMetricValue({ ...record, value: record.range[0] }, unitSystem);
      const [high] = formatMetricValue({ ...record, value: record.range[1] }, unitSystem);
      details.push(t('metrics.range', { low, high }));
    }
    if (record.frame) {
      const key = `metrics.frame.${record.frame}` as MessageKey;
      details.push(t(key) === key ? record.frame : t(key));
    }
    if (record.normalized && isMeasured(record.normalized.value)) {
      details.push(
        t('metrics.normalized', {
          value: formatMetricValue({ ...record, value: record.normalized.value }, unitSystem)[0],
        })
      );
    }
    if (record.note) details.push(record.note);
  }
  const detail = details.join(' · ');
  return (
    <div
      className={`metric-cell metric-cell--${record.status}`}
      data-metric={spec.id}
      data-status={record.status}
      title={record.source ?? undefined}
    >
      <span className="metric-cell__label">{t(`metricName.${spec.id}` as MessageKey)}</span>
      <div className="metric-cell__value-row">
        <span className="metric-cell__value">{value}</span>
        {unit ? <span className="metric-cell__unit">{unit}</span> : null}
      </div>
      {measured ? (
        <div className="metric-cell__meta">
          <LabelBadge status={record.status} />
          {detail ? (
            <span className="metric-cell__detail" title={detail}>
              {detail}
            </span>
          ) : null}
        </div>
      ) : (
        <span className="metric-cell__reason" title={record.reason ?? undefined}>
          {record.reason || t('label.not_measured')}
        </span>
      )}
    </div>
  );
}

interface MetricGroupPanelProps {
  view: MetricPanelView;
  shot: Shot | null;
  profileName: string;
  clubLabel: string;
  unitSystem: UnitSystem;
  headerAction?: ReactNode;
}

/**
 * Club and Flight panels: every metric of the shot, grouped, each with its label
 * badge, and a not-measured metric with its reason (D15, D17, D38, D39).
 */
export function MetricGroupPanel({
  view,
  shot,
  profileName,
  clubLabel,
  unitSystem,
  headerAction,
}: MetricGroupPanelProps) {
  const { t } = useI18n();
  const header = (
    <PanelHeader
      title={t(`nav.${view}` as MessageKey)}
      subtitle={profileName}
      club={clubLabel}
      actions={headerAction}
    />
  );
  const metrics = shot?.metrics;
  if (!shot || !metrics) {
    const swing = shot !== null && isSwingSpeedShot(shot);
    return (
      <div className="panel">
        {header}
        <div className="panel__body panel__body--empty">
          <span className="panel__empty-title">{t('metrics.empty')}</span>
          <span className="panel__empty-detail">{swing ? t('metrics.swingOnly') : t('metrics.emptyDetail')}</span>
        </div>
      </div>
    );
  }
  return (
    <div className="panel">
      {header}
      <div className="panel__body metric-groups" data-metric-panel={view}>
        {PANEL_GROUPS[view].map((group) => (
          <section key={group} className="metric-group" aria-label={t(GROUP_TITLE[group])}>
            <h3 className="metric-group__title">{t(GROUP_TITLE[group])}</h3>
            <div className="metric-group__grid">
              {METRIC_CATALOG.filter((spec) => spec.group === group).map((spec) => {
                const record = metrics[spec.id];
                return (
                  <MetricCell
                    key={spec.id}
                    spec={spec}
                    record={record ?? missingRecord(spec)}
                    unitSystem={unitSystem}
                  />
                );
              })}
            </div>
          </section>
        ))}
        <p className="metric-groups__legend">{t('metrics.legend')}</p>
      </div>
    </div>
  );
}
