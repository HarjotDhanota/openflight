import { useId } from 'react';
import { useI18n } from '../../i18n/useI18n';
import type { Shot } from '../../types/shot';
import { LabelBadge } from '../ui/LabelBadge';
import { FACE_SHAPES, faceCategory, formatLength, GRID_STEP_MM, hoselParts, impactReading } from './faceImpact';
import './FaceImpactView.css';

interface FaceImpactViewProps {
  shot: Shot;
  sessionShots: readonly Shot[];
}

function gridLines(min: number, max: number, step: number): number[] {
  const lines: number[] = [];
  for (let k = Math.ceil(min / step); k * step <= max; k += 1) lines.push(Number((k * step).toFixed(3)));
  return lines;
}

/** The experimental impact on the club face seen from the target, with its 1-sigma ellipse,
 * the club's earlier shots faint, a grid on the face centre; mirrored for a left-hander. */
export function FaceImpactView({ shot, sessionShots }: FaceImpactViewProps) {
  const { t } = useI18n();
  const id = useId();
  const [clipId, faceClipId, steelId, faceId, shaftId] = ['clip', 'face-clip', 'steel', 'face', 'shaft'].map(
    (part) => `${id}${part}`
  );
  const category = faceCategory(shot.club);
  const shape = FACE_SHAPES[category];
  const hosel = hoselParts(shape.hosel);
  const reading = impactReading(shot);
  const left = reading.handedness === 'left';
  const toeSign = left ? 1 : -1;
  const { minX, maxX, minY, maxY } = shape.bounds;
  const [viewMinX, viewMaxX] = left ? [-maxX, -minX] : [minX, maxX];
  const step = GRID_STEP_MM;
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
  const gridText = `${GRID_STEP_MM} mm`;
  const grid = [
    ...gridLines(viewMinX, viewMaxX, step).map((x) => (
      <line key={`x${x}`} className={x === 0 ? 'face-impact__axis' : undefined} x1={x} x2={x} y1={minY} y2={maxY} />
    )),
    ...gridLines(minY, maxY, step).map((y) => (
      <line
        key={`y${y}`}
        className={y === 0 ? 'face-impact__axis' : undefined}
        x1={viewMinX}
        x2={viewMaxX}
        y1={y}
        y2={y}
      />
    )),
  ];
  return (
    <section
      className="face-impact"
      aria-label={t('faceImpact.title')}
      data-face={category}
      data-handed={reading.handedness}
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
          </defs>
          <g className="face-impact__grid" clipPath={`url(#${clipId})`}>
            {grid}
          </g>
          <g clipPath={`url(#${clipId})`}>
            <g className="face-impact__outline-group" transform={mirror}>
              <g className="face-impact__rim">
                <path d={hosel.shaft} />
                <path d={hosel.ferrule} />
                <path d={hosel.tube} />
                <path d={shape.head} />
              </g>
              <path className="face-impact__shaft" d={hosel.shaft} fill={`url(#${shaftId})`} />
              <path className="face-impact__hosel" d={hosel.tube} fill={`url(#${steelId})`} />
              <path className="face-impact__head" d={shape.head} fill={`url(#${steelId})`} />
              <path className="face-impact__ferrule" d={hosel.ferrule} />
              <path className="face-impact__face" d={shape.face} fill={`url(#${faceId})`} />
              {shape.grooves.map((groove) => (
                <line
                  key={`${groove.y}:${groove.from}`}
                  className="face-impact__groove"
                  x1={groove.from}
                  x2={groove.to}
                  y1={groove.y}
                  y2={groove.y}
                />
              ))}
              {shape.edges.map((d) => (
                <path key={d} className="face-impact__edge" d={d} />
              ))}
              {shape.mark ? <path className="face-impact__mark" d={shape.mark} /> : null}
            </g>
            <g className="face-impact__grid face-impact__grid--on-face" clipPath={`url(#${faceClipId})`}>
              {grid}
            </g>
          </g>
          <text
            className="face-impact__side"
            x={left ? viewMaxX - 2 : viewMinX + 2}
            y={minY + 6}
            textAnchor={left ? 'end' : 'start'}
          >
            {t('faceImpact.toe')}
          </text>
          <text
            className="face-impact__side"
            x={left ? viewMinX + 2 : viewMaxX - 2}
            y={maxY - 2}
            textAnchor={left ? 'start' : 'end'}
          >
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
            <ellipse
              className="face-impact__ellipse"
              cx={cx}
              cy={cy}
              rx={reading.offsetSigma}
              ry={reading.heightSigma}
            />
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
          {t('faceImpact.illustrative', { grid: gridText })}
          {earlier.length ? ` · ${t('faceImpact.earlier', { count: String(earlier.length) })}` : ''}
        </p>
      </div>
    </section>
  );
}
