import { useId } from 'react';
import { useI18n } from '../../i18n/useI18n';
import type { Shot } from '../../types/shot';
import { LabelBadge } from '../ui/LabelBadge';
import { FACE_SHAPES, faceCategory, formatLength, GRID_STEP_MM, impactReading } from './faceImpact';
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
  const clipId = useId();
  const category = faceCategory(shot.club);
  const shape = FACE_SHAPES[category];
  const reading = impactReading(shot);
  const left = reading.handedness === 'left';
  const toeSign = left ? 1 : -1;
  const { minX, maxX, minY, maxY } = shape.bounds;
  const [viewMinX, viewMaxX] = left ? [-maxX, -minX] : [minX, maxX];
  const step = GRID_STEP_MM;
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
          </defs>
          <g className="face-impact__grid" clipPath={`url(#${clipId})`}>
            {gridLines(viewMinX, viewMaxX, step).map((x) => (
              <line
                key={`x${x}`}
                className={x === 0 ? 'face-impact__axis' : undefined}
                x1={x}
                x2={x}
                y1={minY}
                y2={maxY}
              />
            ))}
            {gridLines(minY, maxY, step).map((y) => (
              <line
                key={`y${y}`}
                className={y === 0 ? 'face-impact__axis' : undefined}
                x1={viewMinX}
                x2={viewMaxX}
                y1={y}
                y2={y}
              />
            ))}
          </g>
          <g className="face-impact__outline-group" transform={left ? 'scale(-1 1)' : undefined}>
            <path className="face-impact__outline" d={shape.outline} />
            {shape.hosel ? <path className="face-impact__hosel" d={shape.hosel} /> : null}
            {shape.grooves.map((y) => (
              <line
                key={y}
                className="face-impact__groove"
                x1={shape.grooveSpan[0]}
                x2={shape.grooveSpan[1]}
                y1={y}
                y2={y}
              />
            ))}
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
