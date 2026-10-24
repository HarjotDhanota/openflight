import { useId, useRef, useState } from 'react';
import type { MessageKey } from '../../i18n';
import { useI18n } from '../../i18n/useI18n';
import { useDragScroll } from '../../hooks/useDragScroll';
import type { Shot } from '../../types/shot';
import { useSystemStore } from '../../stores/useSystemStore';
import { LabelBadge } from '../ui/LabelBadge';
import { FaceImpactView } from './FaceImpactView';
import { FACE_SHAPES, faceCategory, shotHandedness } from './faceImpact';
import {
  CLUB_PROFILES,
  VIEW_METRICS,
  clubProfilePaths,
  deliveryValue,
  direction,
  type DeliveryView,
} from './clubDelivery';
import { formatMetricValue } from './metricFormat';
import './ClubDeliveryViews.css';

export function DeliveryNumbers({ shot, view }: { shot: Shot; view: DeliveryView }) {
  const { t } = useI18n();
  return (
    <dl className="club-view__numbers">
      {VIEW_METRICS[view].map((metric) => {
        const record = shot.metrics?.[metric];
        const value = deliveryValue(shot, metric);
        const [text, unit] = value !== null && record ? formatMetricValue(record, 'metric', metric) : ['—', ''];
        return (
          <div key={metric} data-view-metric={metric} data-status={value === null ? 'not_measured' : record?.status}>
            <dt>{t(`metricName.${metric}` as MessageKey)}</dt>
            <dd>
              <span>{text}</span>
              <small>{unit}</small>
            </dd>
            {value !== null ? (
              <LabelBadge status={record?.status} compact />
            ) : (
              <span className="club-view__missing" title={record?.reason ?? undefined}>
                {t('label.not_measured')}
              </span>
            )}
          </div>
        );
      })}
    </dl>
  );
}

export function ClubProjection({ shot, view }: { shot: Shot; view: 'top' | 'side' }) {
  const id = useId();
  const { t } = useI18n();
  const category = faceCategory(shot.club);
  const p = CLUB_PROFILES[category];
  const paths = clubProfilePaths(category);
  const handed = useSystemStore((state) => state.handed);
  const left = shotHandedness(shot, handed) === 'left';
  const top = view === 'top';
  const face = deliveryValue(shot, top ? 'face_angle' : 'dynamic_loft');
  const path = deliveryValue(shot, top ? 'club_path' : 'attack_angle');
  const rotation = top ? (face ?? 0) * (left ? 1 : -1) : face === null ? 0 : p.loft - face;
  const [ballX, ballY] = direction(top ? 0 : (face ?? p.loft), 119);
  const head = top ? paths.top : paths.side;
  const shaftX = !p.depth ? paths.bx - p.progression : -p.progression;
  const shaftY = top ? paths.heel + (p.depth ? -8 : 7) : p.depth ? -p.height + p.faceHeight / 2 + 5 : -24;
  const shaftEnd = top ? shaftY + 24 : -64;
  const ferruleEnd = top ? shaftY + 17 : shaftY - 20;
  const coords = head.match(/-?\d+(?:\.\d+)?/g)!.map(Number);
  const xs = coords.filter((_, i) => i % 2 === 0).concat([shaftX - 14, shaftX + 7]);
  const ys = coords.filter((_, i) => i % 2 === 1).concat([shaftY, shaftEnd, ferruleEnd]);
  const angle = (rotation * Math.PI) / 180;
  const bounds = [Math.min(...xs) - 3, Math.max(...xs) + 3].flatMap((x) =>
    [Math.min(...ys) - 3, Math.max(...ys) + 3].map((y) => [
      x * Math.cos(angle) - y * Math.sin(angle),
      x * Math.sin(angle) + y * Math.cos(angle),
    ])
  );
  bounds.push([ballX - 21.335, ballY - 21.335], [ballX + 21.335, ballY + 21.335]);
  for (const value of [face, path]) {
    if (value === null) continue;
    const [x, y] = direction(top && left ? -value : value);
    bounds.push([x - 7, y - 7], [x + 7, y + 7]);
  }
  const bx = bounds.map(([x]) => x * (left ? 1 : -1));
  const by = bounds.map(([, y]) => y);
  const frame = [
    Math.min(...bx) - 10,
    Math.min(...by) - 10,
    Math.max(...bx) - Math.min(...bx) + 20,
    Math.max(...by) - Math.min(...by) + 20,
  ];
  return (
    <svg
      className="club-projection"
      data-view={view}
      data-face={category}
      data-finish={FACE_SHAPES[category].finish}
      data-handed={left ? 'left' : 'right'}
      viewBox={frame.join(' ')}
      role="img"
      aria-label={t(top ? 'clubViews.top' : 'clubViews.side')}
    >
      <defs>
        <linearGradient id={`${id}metal`} x1="0" y1="0" x2="1" y2=".7">
          <stop className="face-impact__steel-hi" offset="0" />
          <stop className="face-impact__steel-mid" offset=".3" />
          <stop className="face-impact__steel-lo" offset=".49" />
          <stop className="face-impact__steel-hi" offset=".56" />
          <stop className="face-impact__steel-mid" offset=".72" />
          <stop className="face-impact__steel-lo" offset="1" />
        </linearGradient>
        <radialGradient id={`${id}crown`} cx="30%" cy="24%" r="76%">
          <stop offset="0" stopColor="#666e76" />
          <stop offset=".38" stopColor="#292e34" />
          <stop offset=".8" stopColor="#12161b" />
          <stop offset="1" stopColor="#050709" />
        </radialGradient>
        <linearGradient id={`${id}face`} x1="0" x2="1" y1="0" y2=".4">
          <stop className="face-impact__face-hi" offset="0" />
          <stop className="face-impact__face-lo" offset="1" />
        </linearGradient>
        <linearGradient id={`${id}shaft`}>
          <stop offset="0" stopColor={p.depth ? '#101418' : '#505860'} />
          <stop offset=".36" stopColor={p.depth ? '#717983' : '#f3f5f8'} />
          <stop offset=".55" stopColor={p.depth ? '#323940' : '#acb4be'} />
          <stop offset="1" stopColor="#151a20" />
        </linearGradient>
        <linearGradient id={`${id}ferrule`}>
          <stop offset="0" stopColor="#090c10" />
          <stop offset=".4" stopColor="#636b72" />
          <stop offset="1" stopColor="#090c10" />
        </linearGradient>
        <linearGradient id={`${id}neck`} gradientUnits="userSpaceOnUse" x1={shaftX - 6.4} x2={shaftX + 6.4}>
          <stop offset="0" stopColor="#59616b" />
          <stop offset=".38" stopColor="#e5ebf0" />
          <stop offset=".6" stopColor="#adb5be" />
          <stop offset="1" stopColor="#626c76" />
        </linearGradient>
        <radialGradient id={`${id}ball`} cx="30%" cy="25%" r="75%">
          <stop offset="0" stopColor="#fff" />
          <stop offset=".55" stopColor="#e5e9ec" />
          <stop offset="1" stopColor="#87909b" />
        </radialGradient>
        <clipPath id={`${id}clip`}>
          <path d={head} />
        </clipPath>
        <clipPath id={`${id}face-clip`}>
          <path d={paths.topFace} />
        </clipPath>
        {['face', 'path'].map((name) => (
          <marker
            key={name}
            id={`${id}arrow-${name}`}
            markerWidth="7"
            markerHeight="7"
            refX="6"
            refY="3.5"
            orient="auto"
            markerUnits="userSpaceOnUse"
          >
            <path d="M 0 0 L 7 3.5 L 0 7 Z" className={`club-projection__arrowhead--${name}`} />
          </marker>
        ))}
      </defs>
      <g className="club-projection__hand" transform={left ? undefined : 'scale(-1 1)'}>
        <line className="club-projection__target" x1="-137" x2="146" y1="0" y2="0" />
        <g className="club-projection__head" transform={`rotate(${rotation})`}>
          {top && !p.depth ? (
            <path
              className="club-projection__neck"
              d={`M ${shaftX - 6.4} ${shaftY} C ${shaftX - 6.4} ${shaftY - 12} ${shaftX - 14} ${paths.heel - 6} ${shaftX - 14} ${paths.heel - 14} L ${shaftX + 3} ${paths.heel - 14} C ${shaftX + 6.4} ${paths.heel - 4} ${shaftX + 6.4} ${shaftY - 8} ${shaftX + 6.4} ${shaftY} Z`}
              fill={`url(#${id}neck)`}
            />
          ) : null}
          {!top && !p.depth ? (
            <path
              d={`M ${paths.tx - p.topline} ${paths.ty + 7} Q ${paths.bx - p.sole - 4} ${paths.by - 14} ${paths.bx - p.sole + 3} ${paths.by - 2}`}
              fill="none"
              stroke="#131b23"
              strokeOpacity=".3"
              strokeWidth="1.2"
            />
          ) : null}
          {!top && !p.depth ? (
            <path
              className="club-projection__neck"
              d={`M ${paths.bx - 13} ${paths.by - 3} C ${shaftX - 5} ${paths.by - 8} ${shaftX - 6.4} ${shaftY + 8} ${shaftX - 6.4} ${shaftY} L ${shaftX + 6.4} ${shaftY} C ${shaftX + 6.4} ${shaftY + 18} ${shaftX + 5} ${paths.by - 1} ${paths.bx - 4} ${paths.by} Z`}
              fill={`url(#${id}neck)`}
            />
          ) : null}
          <path
            className="club-projection__shaft"
            d={`M ${shaftX - 4.6} ${shaftY} L ${shaftX - 4.2} ${shaftEnd} L ${shaftX + 4.2} ${shaftEnd} L ${shaftX + 4.6} ${shaftY} Z`}
            fill={`url(#${id}shaft)`}
          />
          <path
            d={`M ${shaftX - 6.4} ${shaftY} L ${shaftX - 4.8} ${ferruleEnd} L ${shaftX + 4.8} ${ferruleEnd} L ${shaftX + 6.4} ${shaftY} Z`}
            fill={`url(#${id}${p.depth ? 'ferrule' : 'neck'})`}
          />
          <path
            d={`M ${shaftX - 4.8} ${ferruleEnd} l 0 ${top ? -6 : 6} l 9.6 0 l 0 ${top ? 6 : -6} Z`}
            fill={`url(#${id}ferrule)`}
          />
          <line
            x1={shaftX - 5.2}
            x2={shaftX + 5.2}
            y1={ferruleEnd + (top ? -8 : 8)}
            y2={ferruleEnd + (top ? -8 : 8)}
            stroke="#b9c2cb"
            strokeWidth=".65"
            strokeOpacity=".65"
          />
          {top && !p.depth ? (
            <path
              className="club-projection__blade-shell"
              d={FACE_SHAPES[category].face}
              transform={paths.topShellTransform}
              fill={`url(#${id}metal)`}
              stroke={`url(#${id}metal)`}
              strokeWidth="4.8"
              strokeLinejoin="round"
            />
          ) : null}
          <path className="club-projection__outline" d={head} fill={`url(#${id}${p.depth ? 'crown' : 'metal'})`} />
          <path
            d={head}
            fill="none"
            stroke="#e5edf6"
            strokeOpacity={p.depth ? '.24' : '.6'}
            strokeWidth="1.1"
            clipPath={`url(#${id}clip)`}
          />
          {top ? (
            <path
              className="club-projection__topline"
              d={paths.topFace}
              fill={`url(#${id}${p.depth ? 'face' : 'metal'})`}
              stroke={p.depth ? 'none' : '#edf2f5'}
              strokeOpacity=".6"
              strokeWidth="1.4"
            />
          ) : (
            <path className="club-projection__face-edge" d={paths.sideFace} />
          )}
        </g>
        <circle className="club-projection__ball" cx={ballX} cy={ballY} r="21.335" fill={`url(#${id}ball)`} />
        {(
          [
            ['face', face],
            ['path', path],
          ] as const
        ).map(([kind, value]) => {
          if (value === null) return null;
          const [x, y] = direction(top && left ? -value : value);
          return (
            <line
              key={kind}
              className={`club-projection__arrow club-projection__arrow--${kind}`}
              data-angle={value}
              x1="0"
              y1="0"
              x2={x}
              y2={y}
              markerEnd={`url(#${id}arrow-${kind})`}
            />
          );
        })}
        <circle cx="0" cy="0" r="1.5" fill="var(--color-text)" />
      </g>
    </svg>
  );
}

const VIEWS: DeliveryView[] = ['face', 'top', 'side'];
export function ClubDeliveryViews({ shot, sessionShots }: { shot: Shot; sessionShots: readonly Shot[] }) {
  const { t } = useI18n();
  const scrollRef = useRef<HTMLDivElement>(null);
  const drag = useDragScroll(scrollRef, 'x');
  const [active, setActive] = useState(0);
  const title = (view: DeliveryView) =>
    t(view === 'face' ? 'faceImpact.title' : view === 'top' ? 'clubViews.top' : 'clubViews.side');
  const select = (index: number) => {
    const scroller = scrollRef.current;
    if (!scroller) return;
    scroller.scrollTo({
      left: (scroller.children[index] as HTMLElement).offsetLeft - (scroller.children[0] as HTMLElement).offsetLeft,
      behavior: 'instant',
    });
    setActive(index);
  };
  const snap = () => {
    const scroller = scrollRef.current;
    if (!scroller) return;
    const step = (scroller.children[1] as HTMLElement).offsetLeft - (scroller.children[0] as HTMLElement).offsetLeft;
    select(Math.max(0, Math.min(2, Math.round(scroller.scrollLeft / step))));
  };
  return (
    <section className="club-views" aria-label={t('clubViews.title')}>
      <div className="club-views__nav" aria-label={t('clubViews.title')}>
        {VIEWS.map((view, index) => (
          <button type="button" key={view} aria-pressed={active === index} onClick={() => select(index)}>
            {title(view)}
          </button>
        ))}
      </div>
      <div
        className="club-views__scroller"
        ref={scrollRef}
        {...drag}
        onPointerUp={(event) => {
          drag.onPointerUp(event);
          snap();
        }}
        onPointerCancel={(event) => {
          drag.onPointerCancel(event);
          snap();
        }}
      >
        {VIEWS.map((view) => (
          <div className="club-view" key={view} data-club-view={view}>
            {view === 'face' ? (
              <FaceImpactView
                shot={shot}
                sessionShots={sessionShots}
                metricRow={<DeliveryNumbers shot={shot} view={view} />}
              />
            ) : (
              <>
                <div className="club-view__figure">
                  <ClubProjection shot={shot} view={view} />
                </div>
                <h3 className="club-view__title">{title(view)}</h3>
                <DeliveryNumbers shot={shot} view={view} />
                <p className="club-view__legend">
                  <span className="club-view__key--face">
                    {t(view === 'top' ? 'metricName.face_angle' : 'metricName.dynamic_loft')}
                  </span>
                  <span className="club-view__key--path">
                    {t(view === 'top' ? 'metricName.club_path' : 'metricName.attack_angle')}
                  </span>
                </p>
              </>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
