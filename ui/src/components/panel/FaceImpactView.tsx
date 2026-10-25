import { useId, type ReactNode } from 'react';
import { useI18n } from '../../i18n/useI18n';
import type { Shot } from '../../types/shot';
import { useSystemStore } from '../../stores/useSystemStore';
import { LabelBadge } from '../ui/LabelBadge';
import {
  FACE_SHAPES,
  faceCategory,
  formatLength,
  GROOVE_WIDTH_MM,
  hoselParts,
  impactReading,
  shotHandedness,
  type DeliveryShot,
} from './faceImpact';
import './FaceImpactView.css';
import { faceDeliveryRotation } from './clubDelivery';

interface FaceImpactViewProps {
  shot: DeliveryShot;
  sessionShots: readonly Shot[];
  metricRow?: ReactNode;
}

// Fixed illustrative contact texture; neither its outline nor its size is measured.
const contactNoise = (seed: number) => {
  const value = Math.sin(seed * 12.9898) * 43758.5453;
  return value - Math.floor(value);
};
const CONTACT_SHAPE =
  Array.from({ length: 96 }, (_, i) => {
    const angle = (i * Math.PI) / 48;
    const radius = 1 + 0.035 * Math.sin(7 * angle) + 0.025 * Math.cos(17 * angle);
    return `${i ? 'L' : 'M'} ${(10 * radius * Math.cos(angle)).toFixed(3)} ${(11 * radius * Math.sin(angle)).toFixed(3)}`;
  }).join(' ') + ' Z';
const CONTACT_DIMPLES = Array.from({ length: 7 }, (_, row) =>
  Array.from({ length: 7 }, (_, col) => {
    const seed = row * 7 + col + 1;
    return [
      (col - 3) * 3.5 + (row % 2) * 1.75 + (contactNoise(seed) - 0.5) * 0.45,
      (row - 3) * 3.2 + (contactNoise(seed + 51) - 0.5) * 0.4,
      1.15 + contactNoise(seed + 102) * 0.4,
    ];
  })
).flat();
const CONTACT_GRAIN = Array.from({ length: 380 }, (_, i) => {
  const x = contactNoise(i + 1) * 24 - 12;
  const y = contactNoise(i + 401) * 26 - 13;
  return `M ${x.toFixed(3)} ${y.toFixed(3)} l ${(0.08 + contactNoise(i + 801) * 0.32).toFixed(3)} ${(contactNoise(i + 1201) * 0.35).toFixed(3)}`;
}).join(' ');

/** The experimental impact on the club face seen from the target, with its 1-sigma ellipse,
 * the club's earlier shots faint, centre lines through the reading's face centre; mirrored for a
 * left-hander. The impact anchor sits at the unrounded mm values, the svg's units. */
export function FaceImpactView({ shot, sessionShots, metricRow }: FaceImpactViewProps) {
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
  const blade = category === 'iron' || category === 'wedge';
  const hosel = hoselParts(shape.hosel);
  const lie = (shape.hosel.lieDeg * Math.PI) / 180;
  const jointStart = blade ? shape.hosel.tube[0][0] : 0;
  const tubeWidth = Math.max(...shape.hosel.tube.map(([, width]) => width));
  const crossTube = (side: number) => ({
    x: shape.hosel.ground[0] + (side * tubeWidth * Math.sin(lie)) / 2,
    y: shape.hosel.ground[1] + (side * tubeWidth * Math.cos(lie)) / 2,
  });
  const handed = useSystemStore((state) => state.handed);
  const reading = { ...impactReading(shot), handedness: shotHandedness(shot, handed) };
  const left = reading.handedness === 'left';
  const toeSign = left ? 1 : -1;
  const { minX, maxX, minY, maxY } = shape.bounds;
  const [viewMinX, viewMaxX] = left ? [-maxX, -minX] : [minX, maxX];
  const mirror = left ? 'scale(-1 1)' : undefined;
  const rotation = faceDeliveryRotation(shot, category) * (left ? -1 : 1);
  const angle = (rotation * Math.PI) / 180;
  const lightX = (left ? -1 : 1) * (-0.6 * Math.cos(angle) - 0.8 * Math.sin(angle));
  const lightY = 0.6 * Math.sin(angle) - 0.8 * Math.cos(angle);
  const litTubeSide = lightX * Math.sin(lie) + lightY * Math.cos(lie) < 0 ? -1 : 1;
  const lightSpan = Math.max(maxX - minX, maxY - minY);
  const lightCentreX = (minX + maxX) / 2;
  const lightCentreY = (minY + maxY) / 2;
  const corners = [viewMinX, viewMaxX].flatMap((x) =>
    [minY, maxY].map((y) => [x * Math.cos(angle) - y * Math.sin(angle), x * Math.sin(angle) + y * Math.cos(angle)])
  );
  const frameLeft = Math.min(...corners.map(([x]) => x));
  const frameTop = Math.min(...corners.map(([, y]) => y));
  const frameWidth = Math.max(...corners.map(([x]) => x)) - frameLeft;
  const frameHeight = Math.max(...corners.map(([, y]) => y)) - frameTop;
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
          viewBox={`${frameLeft} ${frameTop} ${frameWidth} ${frameHeight}`}
          role="img"
          aria-label={
            read
              ? `${t('faceImpact.title')}: ${offsetText}${heightText ? `, ${heightText}` : ''}`
              : t('faceImpact.title')
          }
          preserveAspectRatio="xMidYMid meet"
        >
          <defs>
            <radialGradient id={`${id}contact-dimple`}>
              <stop offset=".7" stopColor="#000" />
              <stop offset="1" stopColor="#000" stopOpacity="0" />
            </radialGradient>
            <mask id={`${id}contact-mask`} maskUnits="userSpaceOnUse" x="-12" y="-13" width="24" height="26">
              <path d={CONTACT_SHAPE} fill="#fff" />
              {CONTACT_DIMPLES.map(([x, y, radius], i) => (
                <circle
                  key={i}
                  cx={x}
                  cy={y}
                  r={radius}
                  fill={`url(#${id}contact-dimple)`}
                  opacity={i % 4 === 0 ? '.6' : '.95'}
                />
              ))}
              <path d={CONTACT_GRAIN} fill="none" stroke="#000" strokeWidth=".16" strokeLinecap="round" opacity=".65" />
            </mask>
            <clipPath id={clipId}>
              <rect x={viewMinX} y={minY} width={viewMaxX - viewMinX} height={maxY - minY} />
            </clipPath>
            <clipPath id={faceClipId}>
              <path d={shape.face} transform={mirror} />
            </clipPath>
            <linearGradient
              id={steelId}
              gradientUnits="userSpaceOnUse"
              x1={lightCentreX + (lightX * lightSpan) / 2}
              y1={lightCentreY + (lightY * lightSpan) / 2}
              x2={lightCentreX - (lightX * lightSpan) / 2}
              y2={lightCentreY - (lightY * lightSpan) / 2}
            >
              <stop className="face-impact__steel-hi" offset="0" />
              <stop className="face-impact__steel-mid" offset="0.28" />
              <stop className="face-impact__steel-lo" offset="0.46" />
              <stop className="face-impact__steel-hi" offset="0.54" />
              <stop className="face-impact__steel-mid" offset="0.68" />
              <stop className="face-impact__steel-lo" offset="1" />
            </linearGradient>
            <linearGradient
              id={`${id}tube`}
              gradientUnits="userSpaceOnUse"
              x1={crossTube(litTubeSide).x}
              y1={crossTube(litTubeSide).y}
              x2={crossTube(-litTubeSide).x}
              y2={crossTube(-litTubeSide).y}
            >
              <stop className="face-impact__steel-lo" offset="0" />
              <stop className="face-impact__steel-mid" offset="0.2" />
              <stop className="face-impact__steel-hi" offset="0.38" />
              <stop className="face-impact__steel-mid" offset="0.57" />
              <stop className="face-impact__steel-lo" offset="0.86" />
              <stop className="face-impact__steel-mid" offset="1" />
            </linearGradient>
            <linearGradient
              id={`${id}ferrule`}
              gradientUnits="userSpaceOnUse"
              x1={crossTube(litTubeSide).x}
              y1={crossTube(litTubeSide).y}
              x2={crossTube(-litTubeSide).x}
              y2={crossTube(-litTubeSide).y}
            >
              <stop offset="0" stopColor="#101214" />
              <stop offset="0.36" stopColor="#62676c" />
              <stop offset="0.52" stopColor="#292d31" />
              <stop offset="0.88" stopColor="#0c0e10" />
              <stop offset="1" stopColor="#363a3e" />
            </linearGradient>
            <radialGradient
              id={`${id}reflection`}
              cx={`${50 + lightX * 30}%`}
              cy={`${50 + lightY * 40}%`}
              r="85%"
              gradientTransform="translate(0 .08) scale(1 .85)"
            >
              <stop offset="0" stopColor="#fff" stopOpacity="0.27" />
              <stop offset="0.46" stopColor="#fff" stopOpacity="0" />
              <stop offset="1" stopColor="#080b10" stopOpacity="0.26" />
            </radialGradient>
            <linearGradient id={`${id}radius`} x1={lightX < 0 ? 0 : 1} y1="0" x2={lightX < 0 ? 0.18 : 0.82} y2="1">
              <stop offset="0" stopColor="#fff" stopOpacity="0.55" />
              <stop offset="0.42" stopColor="#fff" stopOpacity="0.04" />
              <stop offset="0.75" stopColor="#080b10" stopOpacity="0.45" />
              <stop offset="1" stopColor="#fff" stopOpacity="0.7" />
            </linearGradient>
            <clipPath id={`${id}head-clip`}>
              <path d={blade ? shape.silhouette : shape.head} />
            </clipPath>
            <linearGradient
              id={`${id}joint-fade`}
              gradientUnits="userSpaceOnUse"
              x1={shape.hosel.ground[0] + jointStart * Math.cos(lie)}
              y1={shape.hosel.ground[1] - jointStart * Math.sin(lie)}
              x2={hosel.shaftLine[0][0]}
              y2={hosel.shaftLine[0][1]}
            >
              <stop offset={blade ? '0' : '0.35'} stopColor="#fff" stopOpacity="0" />
              <stop offset={blade ? '0.65' : '0.7'} stopColor="#fff" />
            </linearGradient>
            <mask
              id={`${id}joint`}
              maskUnits="userSpaceOnUse"
              x={minX}
              y={minY}
              width={maxX - minX}
              height={maxY - minY}
            >
              <path d={hosel.tube} fill={`url(#${id}joint-fade)`} />
            </mask>
            <pattern id={`${id}grain`} width="1.7" height="0.7" patternUnits="userSpaceOnUse">
              <path d="M 0 .15 H 1.7 M .3 .48 H 1.2" stroke="#fff" strokeWidth="0.07" opacity="0.08" />
            </pattern>
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
          <g className="face-impact__delivery" transform={`rotate(${rotation})`}>
            <g clipPath={`url(#${clipId})`}>
              <g className="face-impact__outline-group" transform={mirror}>
                <g className="face-impact__rim">
                  <path d={hosel.shaft} />
                  <path d={hosel.ferrule} />
                  <path d={shape.silhouette} />
                </g>
                <path className="face-impact__shaft" d={hosel.shaft} fill={`url(#${shaftId})`} />
                <path className="face-impact__head" d={shape.head} fill={`url(#${steelId})`} />
                {blade ? <path className="face-impact__hosel" d={hosel.tube} fill={`url(#${steelId})`} /> : null}
                <path
                  className="face-impact__head-reflection"
                  d={blade ? shape.silhouette : shape.head}
                  fill={`url(#${id}reflection)`}
                />
                <path
                  className="face-impact__leading-radius"
                  d={blade ? shape.silhouette : shape.head}
                  fill="none"
                  stroke={`url(#${id}radius)`}
                  strokeWidth="1.5"
                  clipPath={`url(#${id}head-clip)`}
                />
                {!blade ? <path className="face-impact__hosel" d={hosel.tube} fill={`url(#${steelId})`} /> : null}
                <path d={hosel.tube} fill={`url(#${id}tube)`} mask={`url(#${id}joint)`} />
                {hosel.collar ? (
                  <path className="face-impact__collar" d={hosel.collar} fill={`url(#${id}tube)`} />
                ) : null}
                {hosel.rings.map((d) => (
                  <path key={d} className="face-impact__ring" d={d} />
                ))}
                <path className="face-impact__ferrule" d={hosel.ferrule} fill={`url(#${id}ferrule)`} />
                <path
                  className="face-impact__face"
                  d={shape.face}
                  fill={`url(#${faceId})`}
                  mask={shape.faceFade ? `url(#${fadeId})` : undefined}
                />
                <g mask={shape.faceFade ? `url(#${fadeId})` : undefined}>
                  <path className="face-impact__face-reflection" d={shape.face} fill={`url(#${id}reflection)`} />
                  <path className="face-impact__grain" d={shape.face} fill={`url(#${id}grain)`} />
                </g>
                {shape.scoring ? <path className="face-impact__scoring" d={shape.scoring} /> : null}
              </g>
              <g className="face-impact__centre-lines" clipPath={`url(#${faceClipId})`}>
                <line className="face-impact__centre-line" x1={viewMinX} x2={viewMaxX} y1={0} y2={0} />
                <line className="face-impact__centre-line" x1={0} x2={0} y1={minY} y2={maxY} />
              </g>
              {read && reading.height !== null ? (
                <g className="face-impact__contact" clipPath={`url(#${faceClipId})`}>
                  <title>{t('faceImpact.illustrativeMark')}</title>
                  <g className="face-impact__imprint" transform={`translate(${cx} ${cy}) scale(${left ? -1 : 1} 1)`}>
                    <g mask={`url(#${id}contact-mask)`}>
                      <path className="face-impact__contact-shape" d={CONTACT_SHAPE} />
                      <path className="face-impact__contact-grain" d={CONTACT_GRAIN} transform="translate(.2 -.25)" />
                    </g>
                  </g>
                </g>
              ) : null}
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
                r={reading.height === null ? 2.6 : 0.75}
              />
            ) : null}
          </g>
        </svg>
      </div>
      <div className="face-impact__readout">
        <h3 className="face-impact__title">
          <span>{t('faceImpact.title')}</span>
          <LabelBadge status="experimental" compact />
        </h3>
        {metricRow}
        {read && metricRow ? (
          <p className="face-impact__summary">
            {offsetText}
            {sigma(reading.offsetSigma)}
            {heightText ? ` · ${heightText}${sigma(reading.heightSigma)}` : ''}
          </p>
        ) : read ? (
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
          {t(read && reading.height !== null ? 'faceImpact.illustrativeMark' : 'faceImpact.illustrative')}
          {earlier.length ? ` · ${t('faceImpact.earlier', { count: String(earlier.length) })}` : ''}
        </p>
      </div>
    </section>
  );
}
