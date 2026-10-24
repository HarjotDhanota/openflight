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
  const ferruleBase = ferruleEnd + (top ? -8 : 8);
  const ferruleRadius = 4.8 + (1.6 * 8) / Math.abs(ferruleEnd - shaftY);
  const shaftRadius = 4.6 - (0.4 * (ferruleEnd - shaftY)) / (shaftEnd - shaftY);
  const crownJoin = shaftY + (top ? -10 : 10);
  const woodNeck = `M ${shaftX - ferruleRadius} ${ferruleBase} C ${shaftX - ferruleRadius} ${shaftY} ${shaftX - 7} ${crownJoin} ${shaftX - 11} ${crownJoin} L ${shaftX + 8} ${crownJoin} C ${shaftX + 6} ${crownJoin} ${shaftX + ferruleRadius} ${shaftY} ${shaftX + ferruleRadius} ${ferruleBase} Z`;
  const heelCorner = paths.top.match(/-?\d+(?:\.\d+)?/g)!.map(Number);
  const neck = top
    ? `M ${shaftX - ferruleRadius} ${ferruleBase} C ${shaftX - ferruleRadius} ${ferruleBase - 12} ${heelCorner[0] + 4} ${heelCorner[1] + 6} ${heelCorner[0] - 2} ${heelCorner[1] - 1} L ${heelCorner[0] - 5} ${heelCorner[1] - 9} L ${shaftX + 4} ${heelCorner[1] - 9} C ${shaftX + 4} ${heelCorner[1] + 2} ${shaftX + ferruleRadius} ${ferruleBase - 12} ${shaftX + ferruleRadius} ${ferruleBase} Z`
    : `M ${paths.bx - 13} ${paths.by - 3} C ${shaftX - 5} ${paths.by - 8} ${shaftX - ferruleRadius} ${shaftY + 8} ${shaftX - ferruleRadius} ${ferruleBase} L ${shaftX + ferruleRadius} ${ferruleBase} C ${shaftX + ferruleRadius} ${shaftY + 18} ${shaftX + 5} ${paths.by - 1} ${paths.bx - 4} ${paths.by} Z`;
  const coords = head.match(/-?\d+(?:\.\d+)?/g)!.map(Number);
  const headXs = coords.filter((_, i) => i % 2 === 0);
  const headYs = coords.filter((_, i) => i % 2 === 1);
  const xs = headXs.concat([shaftX - 14, shaftX + 7]);
  const ys = headYs.concat([shaftY, shaftEnd, ferruleEnd]);
  const angle = (rotation * Math.PI) / 180;
  const lightX = (left ? -0.6 : 0.6) * Math.cos(angle) - 0.8 * Math.sin(angle);
  const lightY = -(left ? -0.6 : 0.6) * Math.sin(angle) - 0.8 * Math.cos(angle);
  const centreX = (Math.min(...headXs) + Math.max(...headXs)) / 2;
  const centreY = (Math.min(...headYs) + Math.max(...headYs)) / 2;
  const lightSpan = Math.max(Math.max(...headXs) - Math.min(...headXs), Math.max(...headYs) - Math.min(...headYs));
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
  const ferrule = (
    <>
      <path
        d={`M ${shaftX - ferruleRadius} ${ferruleBase} L ${shaftX - shaftRadius} ${ferruleEnd} L ${shaftX + shaftRadius} ${ferruleEnd} L ${shaftX + ferruleRadius} ${ferruleBase} Z`}
        fill={`url(#${id}ferrule)`}
      />
      <line
        x1={shaftX - ferruleRadius}
        x2={shaftX + ferruleRadius}
        y1={ferruleBase}
        y2={ferruleBase}
        stroke="#b9c2cb"
        strokeWidth=".35"
        strokeOpacity=".35"
      />
    </>
  );
  const hosel = (
    <g className="club-projection__hosel">
      <path
        className="club-projection__shaft"
        d={`M ${shaftX - 4.6} ${shaftY} L ${shaftX - 4.2} ${shaftEnd} L ${shaftX + 4.2} ${shaftEnd} L ${shaftX + 4.6} ${shaftY} Z`}
        fill={`url(#${id}shaft)`}
      />
      {p.depth ? (
        <>
          <path className="club-projection__neck" d={woodNeck} fill={`url(#${id}crown)`} />
          <path d={woodNeck} fill={`url(#${id}ferrule)`} mask={`url(#${id}joint)`} />
        </>
      ) : !top ? (
        <>
          <path className="club-projection__neck" d={neck} fill={`url(#${id}metal)`} />
          <path d={neck} fill={`url(#${id}back)`} />
          <path d={neck} fill={`url(#${id}neck)`} mask={`url(#${id}joint)`} />
        </>
      ) : null}
      {!top || p.depth ? ferrule : null}
    </g>
  );
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
        <linearGradient
          id={`${id}metal`}
          gradientUnits="userSpaceOnUse"
          x1={centreX + (lightX * lightSpan) / 2}
          y1={centreY + (lightY * lightSpan) / 2}
          x2={centreX - (lightX * lightSpan) / 2}
          y2={centreY - (lightY * lightSpan) / 2}
        >
          <stop className="face-impact__steel-hi" offset="0" />
          <stop className="face-impact__steel-mid" offset=".55" />
          <stop className="face-impact__steel-lo" offset="1" />
        </linearGradient>
        <radialGradient
          id={`${id}crown`}
          gradientUnits="userSpaceOnUse"
          cx={centreX + lightX * lightSpan * 0.25}
          cy={centreY + lightY * lightSpan * 0.25}
          r={lightSpan * 0.85}
        >
          <stop offset="0" stopColor="#666e76" />
          <stop offset=".38" stopColor="#292e34" />
          <stop offset=".8" stopColor="#12161b" />
          <stop offset="1" stopColor="#050709" />
        </radialGradient>
        <linearGradient
          id={`${id}face`}
          gradientUnits="userSpaceOnUse"
          x1="0"
          x2={-p.faceHeight * Math.sin((p.loft * Math.PI) / 180)}
          y1="0"
          y2="0"
        >
          <stop className={lightX < 0 ? 'face-impact__face-lo' : 'face-impact__face-hi'} offset="0" stopOpacity=".9" />
          <stop className="face-impact__face-lo" offset=".4" stopOpacity=".55" />
          <stop className="face-impact__face-lo" offset="1" stopOpacity="0" />
        </linearGradient>
        <linearGradient id={`${id}back`} gradientUnits="userSpaceOnUse" x1={paths.bx - p.sole} x2={paths.bx}>
          <stop offset="0" stopColor="#101820" stopOpacity=".22" />
          <stop offset="1" stopColor="#101820" stopOpacity="0" />
        </linearGradient>
        <linearGradient id={`${id}shaft`} x1={lightX < 0 ? 0 : 1} x2={lightX < 0 ? 1 : 0}>
          <stop offset="0" stopColor={p.depth ? '#101418' : '#505860'} />
          <stop offset=".36" stopColor={p.depth ? '#717983' : '#f3f5f8'} />
          <stop offset=".55" stopColor={p.depth ? '#323940' : '#acb4be'} />
          <stop offset="1" stopColor="#151a20" />
        </linearGradient>
        <linearGradient id={`${id}ferrule`} x1={lightX < 0 ? 0 : 1} x2={lightX < 0 ? 1 : 0}>
          <stop offset="0" stopColor="#090c10" />
          <stop offset=".4" stopColor="#636b72" />
          <stop offset="1" stopColor="#090c10" />
        </linearGradient>
        <linearGradient
          id={`${id}neck`}
          gradientUnits="userSpaceOnUse"
          x1={shaftX + (lightX < 0 ? -6.4 : 6.4)}
          x2={shaftX + (lightX < 0 ? 6.4 : -6.4)}
        >
          <stop offset="0" className="face-impact__steel-lo" />
          <stop offset=".38" className="face-impact__steel-hi" />
          <stop offset=".6" className="face-impact__steel-mid" />
          <stop offset="1" className="face-impact__steel-lo" />
        </linearGradient>
        <linearGradient
          id={`${id}joint-fade`}
          gradientUnits="userSpaceOnUse"
          x1="0"
          x2="0"
          y1={p.depth ? crownJoin : top ? paths.heel - 10 : paths.by - 3}
          y2={p.depth ? ferruleBase : top ? shaftY + 4 : shaftY + 10}
        >
          <stop offset="0" stopColor="#fff" stopOpacity="0" />
          <stop offset="1" stopColor="#fff" />
        </linearGradient>
        <mask id={`${id}joint`}>
          <path d={p.depth ? woodNeck : neck} fill={`url(#${id}joint-fade)`} />
        </mask>
        <mask
          id={`${id}body`}
          maskUnits="userSpaceOnUse"
          x={Math.min(...xs) - 10}
          y={Math.min(...ys) - 10}
          width={Math.max(...xs) - Math.min(...xs) + 20}
          height={Math.max(...ys) - Math.min(...ys) + 20}
        >
          <path
            d={FACE_SHAPES[category].face}
            transform={paths.topShellTransform}
            fill="#fff"
            stroke="#fff"
            strokeWidth="4.8"
            strokeLinejoin="round"
          />
          <path d={neck} fill="#fff" />
        </mask>
        <linearGradient
          id={`${id}heel-edge-fade`}
          gradientUnits="userSpaceOnUse"
          x1="0"
          x2="0"
          y1={paths.heel - 12}
          y2={paths.heel - 3}
        >
          <stop offset="0" stopColor="#fff" />
          <stop offset="1" stopColor="#fff" stopOpacity="0" />
        </linearGradient>
        <mask id={`${id}heel-edge`}>
          <rect
            x={Math.min(...xs) - 10}
            y={Math.min(...ys) - 10}
            width={Math.max(...xs) - Math.min(...xs) + 20}
            height={Math.max(...ys) - Math.min(...ys) + 20}
            fill={`url(#${id}heel-edge-fade)`}
          />
        </mask>
        <radialGradient id={`${id}ball`} cx={left ? '30%' : '70%'} cy="25%" r="75%">
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
          {!top && !p.depth ? (
            <path
              d={`M ${paths.tx - p.topline} ${paths.ty + 7} Q ${paths.bx - p.sole - 4} ${paths.by - 14} ${paths.bx - p.sole + 3} ${paths.by - 2}`}
              fill="none"
              stroke="#131b23"
              strokeOpacity=".3"
              strokeWidth="1.2"
            />
          ) : null}
          {top ? hosel : null}
          {top && !p.depth ? (
            <rect
              className="club-projection__blade-shell"
              x={Math.min(...xs) - 10}
              y={Math.min(...ys) - 10}
              width={Math.max(...xs) - Math.min(...xs) + 20}
              height={Math.max(...ys) - Math.min(...ys) + 20}
              fill={`url(#${id}metal)`}
              mask={`url(#${id}body)`}
            />
          ) : null}
          <path
            className="club-projection__outline"
            d={head}
            fill={`url(#${id}${p.depth ? 'crown' : 'metal'})`}
            mask={top && !p.depth ? `url(#${id}heel-edge)` : undefined}
          />
          <path
            d={head}
            fill="none"
            stroke="#e5edf6"
            strokeOpacity={p.depth ? '.24' : '.6'}
            strokeWidth="1.1"
            clipPath={`url(#${id}clip)`}
            mask={top && !p.depth ? `url(#${id}heel-edge)` : undefined}
          />
          {top ? (
            <path
              className="club-projection__topline"
              d={p.depth ? head : paths.topFace}
              fill={`url(#${id}${p.depth ? 'face' : 'metal'})`}
              stroke={p.depth ? 'none' : '#edf2f5'}
              strokeOpacity=".6"
              strokeWidth="1.4"
              mask={!p.depth ? `url(#${id}heel-edge)` : undefined}
            />
          ) : (
            <path
              className="club-projection__face-edge"
              d={paths.sideFace}
              clipPath={!p.depth ? `url(#${id}clip)` : undefined}
            />
          )}
          {top && !p.depth ? (
            <path className="club-projection__neck" d={neck} fill={`url(#${id}neck)`} mask={`url(#${id}joint)`} />
          ) : null}
          {top && !p.depth ? ferrule : null}
          {!top && !p.depth ? <path d={head} fill={`url(#${id}back)`} /> : null}
          {top && !p.depth ? (
            <g clipPath={`url(#${id}face-clip)`}>
              {FACE_SHAPES[category].grooves.map((groove) => {
                const x = groove.y * Math.sin((p.loft * Math.PI) / 180);
                return (
                  <line
                    key={groove.y}
                    x1={x}
                    x2={x}
                    y1={groove.from}
                    y2={groove.to}
                    stroke="var(--face-art-groove)"
                    strokeOpacity=".65"
                    strokeWidth={0.6 * Math.sin((p.loft * Math.PI) / 180)}
                  />
                );
              })}
            </g>
          ) : null}
          {!top ? hosel : null}
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
