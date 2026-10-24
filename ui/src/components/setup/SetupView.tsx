import { useCallback, useEffect, useRef, useState, type MouseEvent } from 'react';
import type { SetupActionBody, SetupActionResult, SetupActionSection, SetupSnapshot } from '../../hooks/useSetup';
import { useCameraStill } from '../../hooks/useCameraStill';
import { useI18n } from '../../i18n/useI18n';
import type { MessageKey } from '../../i18n';
import type { Handed } from '../../stores/useSystemStore';
import { getServerOrigin } from '../../utils/serverOrigin';
import { PanelAction } from '../panel/PanelAction';
import { PanelHeader } from '../panel/PanelHeader';
import { SegmentedControl } from '../ui/SegmentedControl';
import { isInImage, toCamera, toScreen, type Px, type SetupViewRect } from './setupGeometry';
import { boxOutline } from './setupOverlay';
import {
  SETUP_STEPS,
  aimWords,
  boxStateWords,
  hasCamera,
  ballWords,
  boxWords,
  firstStepToDo,
  isUsable,
  netWords,
  rangeWords,
  reasonWords,
  refusalWords,
  sceneWords,
  stateWords,
  type SetupStep,
} from './setupWords';
import './SetupView.css';

const VIEW_URL = `${getServerOrigin()}/api/setup/view.jpg`;
const VIEW_REFRESH_MS = 2000;
// how often the box step asks the kiosk to look for the ball again
const LOOK_MS = 2000;

const STEP_LABELS: Record<SetupStep, MessageKey> = {
  box: 'setup.step.box',
  aim: 'setup.step.aim',
  range: 'setup.step.range',
  ready: 'setup.step.ready',
};

interface SetupViewProps {
  setup: SetupSnapshot | null;
  /** The setup could not be read from the kiosk after retries. */
  loadFailed?: boolean;
  act: (section: SetupActionSection, body: SetupActionBody) => Promise<SetupActionResult>;
  handed: Handed | null;
  onDone: () => void;
}

type TapMode = 'aim' | 'floor';

function samePx(a: Px | null | undefined, b: Px | null | undefined): boolean {
  return !!a && !!b && Math.abs(a[0] - b[0]) < 0.5 && Math.abs(a[1] - b[1]) < 0.5;
}

function stepDone(step: SetupStep, setup: SetupSnapshot): boolean {
  if (step === 'box') return isUsable(setup.box);
  if (step === 'aim') return isUsable(setup.aim) && isUsable(setup.net_range);
  if (step === 'range') return isUsable(setup.ball_range);
  return false;
}

/** The kiosk Setup tab: ball in the box, aim, ball range, then the ready check. */
export function SetupView({ setup, loadFailed = false, act, handed, onDone }: SetupViewProps) {
  const { t } = useI18n();
  const [stepIndex, setStepIndex] = useState(() => firstStepToDo(setup));
  // open on the first step to do once the setup arrives (adjust state when an input changes)
  const [seeded, setSeeded] = useState(setup !== null);
  if (!seeded && setup) {
    setSeeded(true);
    setStepIndex(firstStepToDo(setup));
  }
  const step = SETUP_STEPS[stepIndex];
  // without a camera no step can be done: nothing is attempted
  const cameraOk = setup !== null && hasCamera(setup);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [lookRefusal, setLookRefusal] = useState<string | null>(null);
  const [tapMode, setTapMode] = useState<TapMode>('aim');
  const [aimTap, setAimTap] = useState<Px | null>(null);
  // null: the saved floor line stands; [] cleared; one or two taps
  const [floorTaps, setFloorTaps] = useState<Px[] | null>(null);

  const goTo = (index: number) => {
    setStepIndex(index);
    setRefusal(null);
    setAimTap(null);
    setFloorTaps(null);
    setTapMode('aim');
  };

  const run = useCallback(
    async (section: SetupActionSection, body: SetupActionBody) => {
      setBusy(true);
      setRefusal(null);
      const result = await act(section, body);
      setBusy(false);
      if (!result.ok) setRefusal(refusalWords(result.reason));
      return result;
    },
    [act]
  );

  // the box step keeps looking, so the found / outside / not-seen state follows the ball
  useEffect(() => {
    if (step !== 'box' || !cameraOk) return undefined;
    let cancelled = false;
    let looking = false;
    const look = async () => {
      if (looking) return;
      looking = true;
      const result = await act('box', { action: 'show' });
      looking = false;
      if (!cancelled) setLookRefusal(result.ok ? null : refusalWords(result.reason));
    };
    void look();
    const timer = setInterval(look, LOOK_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [step, act, cameraOk]);

  useEffect(() => {
    if (step === 'aim' && cameraOk) void act('aim', { action: 'show' });
  }, [step, act, cameraOk]);

  // the range is measured on arrival, with the ball already in the box
  const measuredOnArrival = useRef(false);
  useEffect(() => {
    if (step !== 'range') {
      measuredOnArrival.current = false;
      return;
    }
    if (measuredOnArrival.current || !setup || !hasCamera(setup)) return;
    measuredOnArrival.current = true;
    if (isUsable(setup.box) && setup.ball_range.state !== 'set') {
      // started after this render, as a tap would: the measurement's busy flag is not
      // part of the render that arrived on the step
      void Promise.resolve().then(() => run('range', { action: 'measure' }));
    }
  }, [step, setup, run]);

  const still = useCameraStill(VIEW_URL, VIEW_REFRESH_MS);
  const stageRef = useRef<HTMLDivElement>(null);
  const [stageSize, setStageSize] = useState<{ width: number; height: number } | null>(null);
  const [imageSize, setImageSize] = useState<Px | null>(null);
  const hasSetup = setup !== null;
  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return undefined;
    const measure = () => {
      const { width, height } = stage.getBoundingClientRect();
      setStageSize((previous) =>
        previous && previous.width === width && previous.height === height ? previous : { width, height }
      );
    };
    measure();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(stage);
    window.addEventListener('resize', measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [step, hasSetup]);

  if (!setup || !hasCamera(setup)) {
    const line = setup ? t('setup.needsCamera') : loadFailed ? t('setup.unreachable') : t('setup.loading');
    return (
      <div className="panel setup-view">
        <PanelHeader title={t('nav.setup')} />
        <div className="panel__body panel__body--empty">
          <p className="setup-view__only-line">{line}</p>
        </div>
      </div>
    );
  }

  const imagePx = imageSize ?? setup.box.image_size_px ?? null;
  const view: SetupViewRect | null =
    stageSize && imagePx && stageSize.width > 0 && stageSize.height > 0
      ? { width: stageSize.width, height: stageSize.height, imageWidth: imagePx[0], imageHeight: imagePx[1] }
      : null;
  const screen = (px: Px) => (view ? toScreen(px, view, handed) : px);

  const liveBox = setup.live.box;
  const boxReady = isUsable(setup.box);
  const savedAim = setup.aim.state === 'set' ? (setup.aim.aim_px ?? null) : null;
  const aimPoint = aimTap ?? savedAim;
  const savedFloor = setup.aim.state === 'set' ? (setup.aim.floor_line ?? null) : null;
  const floorLine: Px[] = floorTaps ?? savedFloor ?? [];
  // a saved floor line the service measured nothing from is drawn as not used
  const floorRejected = floorTaps === null && savedFloor !== null && setup.net_range.source !== 'camera_floor_line';
  const ball: Px | null = step === 'box' ? (liveBox?.ball_px ?? null) : (setup.box.ball_px ?? liveBox?.ball_px ?? null);
  const ballDiameter = step === 'box' ? liveBox?.diameter_px : setup.box.diameter_px;
  const outline = boxOutline(setup, { live: true });

  const onStageClick = (event: MouseEvent<HTMLDivElement>) => {
    if (step !== 'aim' || !view || !boxReady) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const camera = toCamera([event.clientX - rect.left, event.clientY - rect.top], view, handed);
    if (!isInImage(camera, view)) return;
    const px: Px = [Math.round(camera[0] * 10) / 10, Math.round(camera[1] * 10) / 10];
    if (tapMode === 'aim') {
      setAimTap(px);
    } else {
      setFloorTaps((taps) => (taps && taps.length === 1 ? [taps[0], px] : [px]));
    }
  };

  const confirmAim = async () => {
    if (!aimPoint) return;
    const body: SetupActionBody = { action: 'confirm', aim_px: aimPoint };
    if (floorLine.length === 2) body.floor_line_px = [floorLine[0], floorLine[1]];
    const result = await run('aim', body);
    if (result.ok) {
      setAimTap(null);
      setFloorTaps(null);
      setTapMode('aim');
    }
  };

  const skipAim = async () => {
    const result = await run('aim', { action: 'skip' });
    if (result.ok) {
      setAimTap(null);
      setFloorTaps(null);
    }
  };

  const overlay = view ? (
    <svg
      className="setup-view__overlay"
      viewBox={`0 0 ${view.width} ${view.height}`}
      width={view.width}
      height={view.height}
      aria-hidden="true"
    >
      {outline ? (
        <polygon className="setup-view__box-outline" points={outline.map((px) => screen(px).join(',')).join(' ')} />
      ) : null}
      {step !== 'ready' && ball ? (
        <circle
          className="setup-view__ball"
          data-state={step === 'box' ? (liveBox?.state ?? 'waiting') : 'confirmed'}
          cx={screen(ball)[0]}
          cy={screen(ball)[1]}
          r={Math.max(
            ((ballDiameter ?? 0) / 2) * Math.min(view.width / view.imageWidth, view.height / view.imageHeight),
            7
          )}
        />
      ) : null}
      {step === 'aim' && floorLine.length === 2 ? (
        <line
          className={`setup-view__floor-line${floorRejected ? ' setup-view__floor-line--rejected' : ''}`}
          x1={screen(floorLine[0])[0]}
          y1={screen(floorLine[0])[1]}
          x2={screen(floorLine[1])[0]}
          y2={screen(floorLine[1])[1]}
        />
      ) : null}
      {step === 'aim'
        ? floorLine.map((px, index) => (
            <circle key={index} className="setup-view__floor-point" cx={screen(px)[0]} cy={screen(px)[1]} r={5} />
          ))
        : null}
      {step === 'aim' && aimPoint && setup.box.ball_px ? (
        <line
          className={`setup-view__aim-line${samePx(aimPoint, savedAim) && floorTaps === null ? ' setup-view__aim-line--saved' : ''}`}
          x1={screen(setup.box.ball_px)[0]}
          y1={screen(setup.box.ball_px)[1]}
          x2={screen(aimPoint)[0]}
          y2={screen(aimPoint)[1]}
        />
      ) : null}
      {step === 'aim' && aimPoint ? (
        <g className="setup-view__aim-point" transform={`translate(${screen(aimPoint).join(' ')})`}>
          <circle r={9} />
          <line x1={-14} y1={0} x2={14} y2={0} />
          <line x1={0} y1={-14} x2={0} y2={14} />
        </g>
      ) : null}
    </svg>
  ) : null;

  const stage =
    step === 'ready' ? null : (
      <div
        ref={stageRef}
        className={`setup-view__stage${step === 'aim' ? ' setup-view__stage--tappable' : ''}`}
        onClick={onStageClick}
        role="group"
        aria-label={t('setup.view')}
      >
        {still.src ? (
          <img
            src={still.src}
            alt=""
            className={`setup-view__image${handed === 'LH' ? ' setup-view__image--mirrored' : ''}`}
            onLoad={(event) => {
              const { naturalWidth, naturalHeight } = event.currentTarget;
              if (naturalWidth > 0 && naturalHeight > 0) {
                setImageSize((previous) =>
                  previous && previous[0] === naturalWidth && previous[1] === naturalHeight
                    ? previous
                    : [naturalWidth, naturalHeight]
                );
              }
            }}
          />
        ) : (
          <p className="setup-view__no-image">
            {still.state === 'unavailable' ? t('setup.camera.unavailable') : t('setup.camera.waiting')}
          </p>
        )}
        {still.src ? overlay : null}
      </div>
    );

  const next =
    stepIndex < SETUP_STEPS.length - 1 ? (
      <PanelAction variant="secondary" onClick={() => goTo(stepIndex + 1)}>
        {t('setup.next')}
      </PanelAction>
    ) : (
      <PanelAction onClick={onDone}>{t('setup.done')}</PanelAction>
    );
  const back =
    stepIndex > 0 ? (
      <PanelAction variant="secondary" onClick={() => goTo(stepIndex - 1)}>
        {t('setup.back')}
      </PanelAction>
    ) : null;
  const notice = refusal ?? (step === 'box' ? lookRefusal : null);
  const unapplied = (section: string) =>
    setup.unapplied[section] ? (
      <p className="setup-view__warning">{t('setup.notApplied', { reason: setup.unapplied[section] })}</p>
    ) : null;

  let side;
  if (step === 'box') {
    side = (
      <>
        <p className="setup-view__instruction">{t('setup.box.instruction')}</p>
        <p className="setup-view__look" data-state={liveBox?.state ?? 'waiting'}>
          {ballWords(liveBox?.state)}
        </p>
        <div className="setup-view__saved">
          <span className={`setup-view__badge setup-view__badge--${setup.box.state}`}>{boxStateWords(setup.box)}</span>
          {boxWords(setup.box) ? <span>{boxWords(setup.box)}</span> : null}
        </div>
        {unapplied('box')}
        <div className="setup-view__actions">
          <PanelAction
            disabled={busy || liveBox?.state !== 'found'}
            onClick={() => void run('box', { action: 'confirm' })}
          >
            {t('setup.confirm')}
          </PanelAction>
        </div>
      </>
    );
  } else if (step === 'aim') {
    side = (
      <>
        {boxReady ? (
          <>
            <SegmentedControl
              ariaLabel={t('setup.aim.mode')}
              value={tapMode}
              onChange={setTapMode}
              options={[
                { id: 'aim', label: t('setup.aim.modeAim') },
                { id: 'floor', label: t('setup.aim.modeFloor') },
              ]}
            />
            <p className="setup-view__instruction">
              {tapMode === 'aim' ? t('setup.aim.instruction') : t('setup.aim.floorInstruction')}
            </p>
            {tapMode === 'floor' && floorLine.length > 0 ? (
              <button type="button" className="setup-view__text-button" onClick={() => setFloorTaps([])}>
                {t('setup.aim.clearFloor')}
              </button>
            ) : null}
          </>
        ) : (
          <p className="setup-view__instruction">{t('setup.needsBox')}</p>
        )}
        <div className="setup-view__saved">
          <span className={`setup-view__badge setup-view__badge--${setup.aim.state}`}>{stateWords(setup.aim)}</span>
          {aimWords(setup.aim) ? <span>{aimWords(setup.aim)}</span> : null}
          {isUsable(setup.net_range) ? (
            <span className="setup-view__saved-line">
              {t('setup.row.net')}: {netWords(setup.net_range)}
            </span>
          ) : null}
          {savedFloor && setup.aim.floor_line_used === false ? (
            <span className="setup-view__saved-line">{t('setup.aim.floorNotUsed')}</span>
          ) : null}
        </div>
        {unapplied('aim')}
        {unapplied('net_range')}
        <div className="setup-view__actions">
          <PanelAction
            disabled={busy || !boxReady || !aimPoint || floorLine.length === 1}
            onClick={() => void confirmAim()}
          >
            {t('setup.confirm')}
          </PanelAction>
          <PanelAction variant="secondary" disabled={busy || !boxReady} onClick={() => void skipAim()}>
            {t('setup.skip')}
          </PanelAction>
        </div>
      </>
    );
  } else if (step === 'range') {
    side = (
      <>
        <p className="setup-view__instruction">{boxReady ? t('setup.range.instruction') : t('setup.needsBox')}</p>
        <p className="setup-view__range" data-state={setup.ball_range.state}>
          {busy
            ? t('setup.measuring')
            : setup.ball_range.state === 'set'
              ? rangeWords(setup.ball_range)
              : reasonWords(setup.ball_range.reason) || stateWords(setup.ball_range)}
        </p>
        {unapplied('ball_range')}
        <div className="setup-view__actions">
          <PanelAction
            variant="secondary"
            disabled={busy || !boxReady}
            onClick={() => void run('range', { action: 'measure' })}
          >
            {t('setup.measure')}
          </PanelAction>
        </div>
      </>
    );
  } else {
    const sceneShown = setup.scene.state === 'set' ? setup.scene : { state: 'default' };
    const rows: Array<{
      section: keyof SetupSnapshot & string;
      label: MessageKey;
      badge: string;
      words: string;
      state?: string;
    }> = [
      { section: 'box', label: 'setup.row.box', badge: boxStateWords(setup.box), words: boxWords(setup.box) },
      { section: 'aim', label: 'setup.row.aim', badge: stateWords(setup.aim), words: aimWords(setup.aim) },
      {
        section: 'ball_range',
        label: 'setup.row.range',
        badge: stateWords(setup.ball_range),
        words: rangeWords(setup.ball_range),
      },
      {
        section: 'net_range',
        label: 'setup.row.net',
        badge: stateWords(setup.net_range),
        words: netWords(setup.net_range),
      },
      {
        section: 'scene',
        label: 'setup.row.scene',
        // the kiosk runs on the enclosure's lens height until the radar solves one
        badge: stateWords(sceneShown),
        state: sceneShown.state,
        words: sceneWords(setup.scene),
      },
    ];
    const ready = [setup.box, setup.aim, setup.ball_range, setup.net_range].every(isUsable);
    side = (
      <>
        <p className={`setup-view__summary${ready ? ' setup-view__summary--ready' : ''}`}>
          {ready ? t('setup.ready.all') : t('setup.ready.gaps')}
        </p>
        <ul className="setup-view__checks">
          {rows.map((row) => {
            const state = row.state ?? (setup[row.section] as { state: string }).state;
            return (
              <li key={row.section} className="setup-view__check" data-section={row.section} data-state={state}>
                <span className="setup-view__check-label">{t(row.label)}</span>
                <span className={`setup-view__badge setup-view__badge--${state}`}>{row.badge}</span>
                <span className="setup-view__check-words">
                  {row.words}
                  {setup.unapplied[row.section] ? (
                    <em className="setup-view__warning">
                      {' '}
                      {t('setup.notApplied', { reason: setup.unapplied[row.section] })}
                    </em>
                  ) : null}
                </span>
              </li>
            );
          })}
        </ul>
      </>
    );
  }

  return (
    <div className="panel setup-view" data-step={step} data-handed={handed === 'LH' ? 'left' : 'right'}>
      <PanelHeader
        title={t('nav.setup')}
        subtitle={t('setup.stepOf', { step: stepIndex + 1, count: SETUP_STEPS.length })}
        actions={
          <>
            {back}
            {next}
          </>
        }
      />
      <div className="panel__body setup-view__body">
        <nav className="setup-view__steps" aria-label={t('setup.steps')}>
          {SETUP_STEPS.map((id, index) => (
            <button
              key={id}
              type="button"
              className={`setup-view__step${index === stepIndex ? ' setup-view__step--active' : ''}${stepDone(id, setup) ? ' setup-view__step--done' : ''}`}
              aria-current={index === stepIndex ? 'step' : undefined}
              onClick={() => goTo(index)}
            >
              <span className="setup-view__step-number">{index + 1}</span>
              <span>{t(STEP_LABELS[id])}</span>
            </button>
          ))}
        </nav>
        <div className={`setup-view__content${stage ? '' : ' setup-view__content--wide'}`}>
          {stage}
          <aside className="setup-view__side">
            <h2 className="setup-view__title">{t(STEP_LABELS[step])}</h2>
            {side}
            {notice ? (
              <p className="setup-view__refusal" role="alert">
                {notice}
              </p>
            ) : null}
          </aside>
        </div>
      </div>
    </div>
  );
}
