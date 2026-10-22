import { useState, type KeyboardEvent, type ReactNode } from 'react';
import { METRIC_CATALOG, type MetricSpec } from '../../data/metricCatalog';
import type { MessageKey } from '../../i18n';
import { useI18n } from '../../i18n/useI18n';
import type { Shot, ShotMetric } from '../../types/shot';
import { isSwingSpeedShot } from '../../types/shot';
import type { UnitSystem } from '../../utils/units';
import { isMeasured } from '../../utils/units';
import { isComingLater, shortReason } from '../../utils/plainReason';
import { GROUP_TITLE, PANEL_GROUPS, formatMetricValue, type MetricPanelView } from './metricFormat';
import { LabelBadge } from '../ui/LabelBadge';
import { FaceImpactView } from './FaceImpactView';
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

/** A cell a tap opens: the whole cell is the target, so it is never under 44 px. */
function useTapToggle(enabled: boolean) {
  const [open, setOpen] = useState(false);
  if (!enabled) return { open: false, props: {} };
  const toggle = () => setOpen((value) => !value);
  return {
    open,
    props: {
      role: 'button',
      tabIndex: 0,
      'aria-expanded': open,
      onClick: toggle,
      onKeyDown: (event: KeyboardEvent) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          toggle();
        }
      },
    },
  };
}

function MetricCell({ spec, record, unitSystem }: { spec: MetricSpec; record: ShotMetric; unitSystem: UnitSystem }) {
  const { t } = useI18n();
  const [value, unit] = formatMetricValue(record, unitSystem, spec.id);
  const measured = isMeasured(record.value) && record.status !== 'not_measured';
  const reason = record.reason || t('label.not_measured');
  const short = shortReason(record.reason);
  // a developer's reason is cut off by the cell: say it plainly, the full one on a tap (9)
  const tappable = !measured && Boolean(record.reason) && short !== record.reason;
  const { open, props } = useTapToggle(tappable);
  const details: string[] = [];
  if (measured && spec.id === 'ball_range') {
    // where this shot's distance came from (13 Oct): its own radar launch point, or
    // the setup camera's coarse estimate standing in
    details.push(t(record.status === 'measured' ? 'metrics.ballRangeRadar' : 'metrics.ballRangeCamera'));
  }
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
  const classes = ['metric-cell', `metric-cell--${record.status}`];
  if (tappable) classes.push('metric-cell--tappable');
  if (open) classes.push('metric-cell--open');
  return (
    <div
      className={classes.join(' ')}
      data-metric={spec.id}
      data-status={record.status}
      title={record.source ?? undefined}
      {...props}
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
      ) : tappable ? (
        <>
          <span className="metric-cell__reason">{open ? reason : `${short} ›`}</span>
          <span className="metric-cell__reason-full" hidden>
            {reason}
          </span>
        </>
      ) : (
        <span className="metric-cell__reason">{reason}</span>
      )}
    </div>
  );
}

/** The metrics not built yet, in one row while they have no value; a tap lists why (9). */
function ComingLaterCell({ items }: { items: Array<{ spec: MetricSpec; record: ShotMetric | undefined }> }) {
  const { t } = useI18n();
  const { open, props } = useTapToggle(true);
  const name = (spec: MetricSpec) => t(`metricName.${spec.id}` as MessageKey);
  return (
    <div
      className={`metric-cell metric-cell--not_measured metric-cell--coming-later metric-cell--tappable${open ? ' metric-cell--open' : ''}`}
      data-metric="coming-later"
      data-status="not_measured"
      data-metrics={items.map(({ spec }) => spec.id).join(' ')}
      {...props}
    >
      <span className="metric-cell__label">{t('metrics.comingLater')}</span>
      <span className="metric-cell__reason">{items.map(({ spec }) => name(spec)).join(', ')}</span>
      <ul className="metric-cell__later-list" hidden={!open}>
        {items.map(({ spec, record }) => (
          <li key={spec.id}>
            {name(spec)}: {record?.reason || t('label.not_measured')}
          </li>
        ))}
      </ul>
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
  sessionShots?: readonly Shot[];
}

/**
 * Club and Flight panels: every metric of the shot, grouped, each with its label
 * badge, a not-measured metric with its reason (D15, D17, D38, D39), and the face view.
 */
export function MetricGroupPanel({
  view,
  shot,
  sessionShots = [],
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
  const later = METRIC_CATALOG.filter((spec) => isComingLater(spec, metrics[spec.id])).map((spec) => ({
    spec,
    record: metrics[spec.id],
  }));
  return (
    <div className="panel">
      {header}
      <div className="panel__body metric-groups" data-metric-panel={view}>
        {view === 'club' ? <FaceImpactView shot={shot} sessionShots={sessionShots} unitSystem={unitSystem} /> : null}
        {PANEL_GROUPS[view].map((group) => (
          <section key={group} className="metric-group" aria-label={t(GROUP_TITLE[group])}>
            <h3 className="metric-group__title">{t(GROUP_TITLE[group])}</h3>
            <div className="metric-group__grid">
              {METRIC_CATALOG.filter((spec) => spec.group === group && !isComingLater(spec, metrics[spec.id])).map(
                (spec) => {
                  const record = metrics[spec.id];
                  return (
                    <MetricCell
                      key={spec.id}
                      spec={spec}
                      record={record ?? missingRecord(spec)}
                      unitSystem={unitSystem}
                    />
                  );
                }
              )}
              {later.filter(({ spec }) => spec.group === group).length ? (
                <ComingLaterCell items={later.filter(({ spec }) => spec.group === group)} />
              ) : null}
            </div>
          </section>
        ))}
        <p className="metric-groups__legend">{t('metrics.legend')}</p>
      </div>
    </div>
  );
}
