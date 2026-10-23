import { useId } from 'react';
import { useI18n } from '../../i18n/useI18n';
import type { Shot } from '../../types/shot';
import { LabelBadge } from '../ui/LabelBadge';
import { FACE_SHAPES, faceCategory, formatLength, GROOVE_WIDTH_MM, hoselParts, impactReading } from './faceImpact';
import './FaceImpactView.css';

interface FaceImpactViewProps {
  shot: Shot;
  sessionShots: readonly Shot[];
}

/** The experimental impact on the club face seen from the target, with its 1-sigma ellipse,
 * the club's earlier shots faint, centre lines through the reading's face centre; mirrored for a
 * left-hander. The dot sits at the unrounded mm values, the svg's units. */
export function FaceImpactView({ shot, sessionShots }: FaceImpactViewProps) {
  const { t } = useI18n();
  const id = useId();
  const [clipId, faceClipId, steelId, faceId, shaftId, fadeId] = [
    'clip',
    'face-clip',
    'steel',
    'face',
    'shaft',
    'fade',
  ].map((part) => `${id}${part}`);
  const category = faceCategory(shot.club);
  const shape = FACE_SHAPES[category];
  const hosel = hoselParts(shape.hosel);
  const reading = impactReading(shot);
  const left = reading.handedness === 'left';
  const toeSign = left ? 1 : -1;
  const { minX, maxX, minY, maxY } = shape.bounds;
  const [viewMinX, viewMaxX] = left ? [-maxX, -minX] : [minX, maxX];
  const mirror = left ? 'scale(-1 1)' : undefined;
  const earlier = sessionShots
    .filter((other) => other.timestamp !== shot.timestamp && other.club === shot.club)
    .map(impactReading)
    .filter((other) => other.offset !== null);
  const read = reading.offset !== null;
  const cx = read ? toeSign * (reading.offset as number) : 0;
  const cy = reading.height !== null ? -reading.height : 0;
  const words = (value: number, plus: string, minus: string) =>
    Math.abs(value) < 0.5 ? t('faceImpact.centre') : value > 0 ? plus : minus;
  const offsetText = read
    ? `${words(reading.offset as number, t('faceImpact.toe'), t('faceImpact.heel'))} ${formatLength(Math.abs(reading.offset as number))}`
    : null;
  const heightText =
    reading.height !== null
      ? `${words(reading.height, t('faceImpact.high'), t('faceImpact.low'))} ${formatLength(Math.abs(reading.height))}`
      : null;
  const sigma = (value: number | null) => (value !== null ? ` ± ${formatLength(value)}` : '');
  const [toeEnd, heelEnd] = left ? [-shape.ends[0], -shape.ends[1]] : shape.ends;
  return (
    <section
      className="face-impact"
      aria-label={t('faceImpact.title')}
      data-face={category}
      data-handed={reading.handedness}
      data-finish={shape.finish}
    >
      <div className="face-impact__figure">
        <svg
          className="face-impact__svg"
          viewBox={`${viewMinX} ${minY} ${viewMaxX - viewMinX} ${maxY - minY}`}
          role="img"
          aria-label={
            read
              ? `${t('faceImpact.title')}: ${offsetText}${heightText ? `, ${heightText}` : ''}`
              : t('faceImpact.title')
          }
          preserveAspectRatio="xMidYMid meet"
        >
          <defs>
            <clipPath id={clipId}>
              <rect x={viewMinX} y={minY} width={viewMaxX - viewMinX} height={maxY - minY} />
            </clipPath>
            <clipPath id={faceClipId}>
              <path d={shape.face} transform={mirror} />
            </clipPath>
            <linearGradient id={steelId} gradientUnits="userSpaceOnUse" x1={minX} y1={minY} x2={maxX} y2={maxY}>
              <stop className="face-impact__steel-hi" offset="0" />
              <stop className="face-impact__steel-mid" offset="0.55" />
              <stop className="face-impact__steel-lo" offset="1" />
            </linearGradient>
            <linearGradient id={faceId} gradientUnits="userSpaceOnUse" x1="0" y1={minY} x2="0" y2={maxY}>
              <stop className="face-impact__face-hi" offset="0" />
              <stop className="face-impact__face-lo" offset="1" />
            </linearGradient>
            <linearGradient
              id={shaftId}
              gradientUnits="userSpaceOnUse"
              x1={hosel.shaftLine[0][0]}
              y1={hosel.shaftLine[0][1]}
              x2={hosel.shaftLine[1][0]}
              y2={hosel.shaftLine[1][1]}
            >
              <stop className="face-impact__shaft-near" offset="0" />
              <stop className="face-impact__shaft-far" offset="1" />
            </linearGradient>
            {shape.faceFade ? (
              <>
                <linearGradient
                  id={`${fadeId}-ramp`}
                  gradientUnits="userSpaceOnUse"
                  x1={shape.faceFade[0]}
                  y1="0"
                  x2={shape.faceFade[1]}
                  y2="0"
                >
                  <stop offset="0" stopColor="#fff" />
                  <stop offset="1" stopColor="#fff" stopOpacity="0" />
                </linearGradient>
                <mask id={fadeId} maskUnits="userSpaceOnUse" x={minX} y={minY} width={maxX - minX} height={maxY - minY}>
                  <rect x={minX} y={minY} width={maxX - minX} height={maxY - minY} fill={`url(#${fadeId}-ramp)`} />
                </mask>
              </>
            ) : null}
            {shape.edges.map(({ fade }, k) =>
              fade ? (
                <linearGradient
                  key={k}
                  id={`${fadeId}-edge${k}`}
                  gradientUnits="userSpaceOnUse"
                  x1={fade[0][0]}
                  y1={fade[0][1]}
                  x2={fade[1][0]}
                  y2={fade[1][1]}
                >
                  <stop className="face-impact__highlight-stop" offset="0" />
                  <stop className="face-impact__highlight-stop face-impact__highlight-stop--end" offset="1" />
                </linearGradient>
              ) : null
            )}
          </defs>
          <g clipPath={`url(#${clipId})`}>
            <g className="face-impact__outline-group" transform={mirror}>
              <g className="face-impact__rim">
                <path d={hosel.shaft} />
                <path d={hosel.ferrule} />
                <path d={shape.silhouette} />
              </g>
              <path className="face-impact__shaft" d={hosel.shaft} fill={`url(#${shaftId})`} />
              <path className="face-impact__head" d={shape.head} fill={`url(#${steelId})`} />
              <path className="face-impact__hosel" d={hosel.tube} fill={`url(#${steelId})`} />
              {hosel.collar ? <path className="face-impact__collar" d={hosel.collar} /> : null}
              {hosel.rings.map((d) => (
                <path key={d} className="face-impact__ring" d={d} />
              ))}
              <path className="face-impact__ferrule" d={hosel.ferrule} />
              <path
                className="face-impact__face"
                d={shape.face}
                fill={`url(#${faceId})`}
                mask={shape.faceFade ? `url(#${fadeId})` : undefined}
              />
              {shape.scoring ? <path className="face-impact__scoring" d={shape.scoring} /> : null}
            </g>
            <g className="face-impact__centre-lines" clipPath={`url(#${faceClipId})`}>
              <line className="face-impact__centre-line" x1={viewMinX} x2={viewMaxX} y1={0} y2={0} />
              <line className="face-impact__centre-line" x1={0} x2={0} y1={minY} y2={maxY} />
            </g>
            <g className="face-impact__face-marks" transform={mirror}>
              {shape.grooves.map((groove) => (
                <line
                  key={`${groove.y}:${groove.from}`}
                  className="face-impact__groove"
                  strokeWidth={GROOVE_WIDTH_MM}
                  x1={groove.from}
                  x2={groove.to}
                  y1={groove.y}
                  y2={groove.y}
                />
              ))}
              {shape.edges.map(({ d, fade }, k) => (
                <path
                  key={d}
                  className="face-impact__edge"
                  d={d}
                  style={fade ? { stroke: `url(#${fadeId}-edge${k})` } : undefined}
                />
              ))}
              {shape.mark ? <path className="face-impact__mark" d={shape.mark} /> : null}
            </g>
          </g>
          <text className="face-impact__side" x={toeEnd} y={1.5} textAnchor={left ? 'start' : 'end'}>
            {t('faceImpact.toe')}
          </text>
          <text className="face-impact__side" x={heelEnd} y={1.5} textAnchor={left ? 'end' : 'start'}>
            {t('faceImpact.heel')}
          </text>
          {earlier.map((other, index) => (
            <circle
              key={index}
              className="face-impact__earlier"
              cx={toeSign * (other.offset as number)}
              cy={other.height !== null ? -other.height : 0}
              r={1.8}
            />
          ))}
          {read && reading.offsetSigma !== null && reading.heightSigma !== null && reading.height !== null ? (
            <g>
              <ellipse
                className="face-impact__halo"
                cx={cx}
                cy={cy}
                rx={reading.offsetSigma}
                ry={reading.heightSigma}
              />
              <ellipse
                className="face-impact__ellipse"
                cx={cx}
                cy={cy}
                rx={reading.offsetSigma}
                ry={reading.heightSigma}
              />
            </g>
          ) : null}
          {read && reading.height === null && reading.offsetSigma !== null ? (
            <line
              className="face-impact__bar"
              x1={cx - reading.offsetSigma}
              x2={cx + reading.offsetSigma}
              y1={0}
              y2={0}
            />
          ) : null}
          {read ? (
            <circle
              className={`face-impact__dot${reading.height === null ? ' face-impact__dot--offset-only' : ''}`}
              cx={cx}
              cy={cy}
              r={2.6}
            />
          ) : null}
        </svg>
      </div>
      <div className="face-impact__readout">
        <h3 className="face-impact__title">
          <span>{t('faceImpact.title')}</span>
          <LabelBadge status="experimental" compact />
        </h3>
        {read ? (
          <dl className="face-impact__numbers">
            <div>
              <dt>{offsetText}</dt>
              <dd>{sigma(reading.offsetSigma)}</dd>
            </div>
            {heightText ? (
              <div>
                <dt>{heightText}</dt>
                <dd>{sigma(reading.heightSigma)}</dd>
              </div>
            ) : null}
          </dl>
        ) : (
          <p className="face-impact__reason">{t('metrics.notMeasured', { reason: reading.offsetReason ?? '' })}</p>
        )}
        {read && reading.height === null ? (
          <p className="face-impact__reason">
            {t('faceImpact.heightNotMeasured', { reason: reading.heightReason ?? '' })}
          </p>
        ) : null}
        <p className="face-impact__caption">
          {t('faceImpact.illustrative')}
          {earlier.length ? ` · ${t('faceImpact.earlier', { count: String(earlier.length) })}` : ''}
        </p>
      </div>
    </section>
  );
}
