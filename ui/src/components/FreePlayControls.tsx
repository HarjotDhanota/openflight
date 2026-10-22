import { useEffect, useState } from 'react';
import { create } from 'zustand';
import { PanelAction } from './panel/PanelAction';
import { openTesterPage } from '../utils/testerPage';
import './FreePlayControls.css';

// free play's exposure ladder, as the kiosk reports it (free_play_ladder.progress_from_state)
interface ExposureStudy {
  phase: string;
  text: string;
  changing?: boolean;
}

interface FreePlayState {
  enabled: boolean;
  phase?: string;
  geometry_unverified?: boolean;
  bundle?: { name: string; path?: string } | null;
  error?: string | null;
  exposure_study?: ExposureStudy | null;
  paused?: { cause: string } | null;
  tester_url?: string | null;
}

const useFreePlayStore = create<{ state: FreePlayState | null }>(() => ({ state: null }));
const setFreePlay = (update: (previous: FreePlayState | null) => FreePlayState | null) =>
  useFreePlayStore.setState((store) => ({ state: update(store.state) }));

// decision 3 (19 Oct): the tester sends the package from the tester page, never from here
const SEND_FROM = 'send it from the tester page (step 8)';

export function FreePlayStatus({
  state,
  busy = false,
  onAction,
}: {
  state: FreePlayState | null;
  busy?: boolean;
  onAction: (action: 'stop' | 'close') => void;
}) {
  if (!state?.enabled) return null;
  const ready = state.phase === 'ready';
  const waiting = busy || state.phase === 'stopping' || state.phase === 'packaging';
  // once acquisition stops the ready light no longer describes anything: the
  // controls take its band, so "Package ready" and the file name read in full
  const finished = ['stopping', 'packaging', 'ready'].includes(state.phase ?? '');
  return (
    <div className={finished ? 'free-play free-play--finished' : 'free-play'} aria-label="Free play">
      <div className="free-play__status" role="status" aria-live="polite">
        <strong className={ready ? 'free-play__ready' : undefined}>
          {ready
            ? state.bundle?.name
              ? `Package ready: ${state.bundle.name} · ${SEND_FROM}`
              : `Package ready · ${SEND_FROM}`
            : state.phase === 'recording'
              ? state.paused
                ? 'Free play: paused'
                : 'Free play: recording'
              : waiting
                ? 'Stopping and packaging…'
                : 'Free play needs attention'}
        </strong>
        {state.geometry_unverified && !finished ? <span>Geometry unverified</span> : null}
        {/* the exposure study runs itself: a progress line, never a prompt */}
        {state.exposure_study?.text && !finished ? (
          <span className="free-play__study">{state.exposure_study.text}</span>
        ) : null}
        {state.error ? <span role="alert">{state.error}</span> : null}
      </div>
      <button
        type="button"
        className={ready ? undefined : 'free-play__stop'}
        disabled={waiting}
        onClick={() => onAction(ready ? 'close' : 'stop')}
      >
        {busy ? 'Please wait…' : ready ? 'Done' : state.phase === 'error' ? 'Try again' : 'Stop & package'}
      </button>
    </div>
  );
}

/** Setup and the face photo, in the Live header beside Change club. */
export function FreePlayActionsView({
  state,
  busy = false,
  onSetup,
  onPhoto,
  onResume,
}: {
  state: FreePlayState | null;
  busy?: boolean;
  onSetup: () => void;
  onPhoto: () => void;
  onResume: () => void;
}) {
  if (!state?.enabled) return null;
  const recording = state.phase === 'recording';
  return (
    <>
      <PanelAction variant="secondary" onClick={onSetup}>
        Setup
      </PanelAction>
      {recording && state.paused ? (
        <PanelAction variant="secondary" disabled={busy} onClick={onResume}>
          Resume
        </PanelAction>
      ) : recording ? (
        <PanelAction variant="secondary" disabled={busy} aria-label="Photograph the face" onClick={onPhoto}>
          Face photo
        </PanelAction>
      ) : null}
    </>
  );
}

async function postFreePlay(path: string, body: object, failure: string): Promise<FreePlayState> {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(10000),
  });
  const answer = await response.json();
  if (!response.ok) throw new Error(answer.error || failure);
  return answer;
}

export function FreePlayActions() {
  const state = useFreePlayStore((store) => store.state);
  const [busy, setBusy] = useState(false);
  async function pause(action: 'pause' | 'resume') {
    if (busy) return;
    setBusy(true);
    try {
      const answer = await postFreePlay(
        `/api/free-play/${action}`,
        action === 'pause' ? { cause: 'photo' } : {},
        'The kiosk did not answer'
      );
      setFreePlay((previous) => (previous ? { ...previous, paused: answer.paused ?? null, error: null } : previous));
      if (action === 'pause') openTesterPage(state?.tester_url, '#free-play-photo');
    } catch (error) {
      setFreePlay((previous) => (previous ? { ...previous, error: String(error) } : previous));
    } finally {
      setBusy(false);
    }
  }
  return (
    <FreePlayActionsView
      state={state}
      busy={busy}
      onSetup={() => openTesterPage(state?.tester_url)}
      onPhoto={() => void pause('pause')}
      onResume={() => void pause('resume')}
    />
  );
}

export function FreePlayControls() {
  const state = useFreePlayStore((store) => store.state);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController;
    async function poll() {
      controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 5000);
      try {
        const response = await fetch('/api/free-play', { signal: controller.signal, cache: 'no-store' });
        const body: FreePlayState = await response.json();
        if (!active) return;
        if (!response.ok) throw new Error(body.error || 'Session status unavailable');
        setFreePlay(() => body);
        if (!body.enabled) return;
      } catch (error) {
        if (active) setFreePlay((previous) => (previous ? { ...previous, error: String(error) } : null));
      } finally {
        clearTimeout(timeout);
      }
      if (active) timer = setTimeout(() => void poll(), 1500);
    }
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
      controller?.abort();
    };
  }, []);
  async function action(action: 'stop' | 'close') {
    if (busy) return;
    setBusy(true);
    try {
      const body = await postFreePlay('/api/free-play', { action }, 'Could not finish the session');
      if (action === 'stop') setFreePlay(() => body);
    } catch (error) {
      setFreePlay((previous) => (previous ? { ...previous, error: String(error) } : null));
    } finally {
      setBusy(false);
    }
  }
  return <FreePlayStatus state={state} busy={busy} onAction={(choice) => void action(choice)} />;
}
