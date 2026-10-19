import { useEffect, useState } from 'react';
import './FreePlayControls.css';

interface FreePlayState {
  enabled: boolean;
  phase?: string;
  geometry_unverified?: boolean;
  bundle?: { name: string; path?: string } | null;
  error?: string | null;
}

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
              ? `Package ready: ${state.bundle.name}`
              : 'Package ready'
            : state.phase === 'recording'
              ? 'Free play: recording'
              : waiting
                ? 'Stopping and packaging…'
                : 'Free play needs attention'}
        </strong>
        {state.geometry_unverified && !finished ? <span>Geometry unverified</span> : null}
        {ready && state.bundle?.path ? (
          <span className="free-play__path" title={state.bundle.path}>
            {state.bundle.path}
          </span>
        ) : null}
        {state.error ? <span role="alert">{state.error}</span> : null}
      </div>
      <button type="button" disabled={waiting} onClick={() => onAction(ready ? 'close' : 'stop')}>
        {busy ? 'Please wait…' : ready ? 'Done' : state.phase === 'error' ? 'Try again' : 'Stop & package'}
      </button>
    </div>
  );
}

export function FreePlayControls() {
  const [state, setState] = useState<FreePlayState | null>(null);
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
        setState(body);
        if (!body.enabled) return;
      } catch (error) {
        if (active) setState((previous) => (previous ? { ...previous, error: String(error) } : null));
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
      const response = await fetch('/api/free-play', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
        signal: AbortSignal.timeout(5000),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || 'Could not finish the session');
      if (action === 'stop') setState(body);
    } catch (error) {
      setState((previous) => (previous ? { ...previous, error: String(error) } : null));
    } finally {
      setBusy(false);
    }
  }
  return <FreePlayStatus state={state} busy={busy} onAction={(choice) => void action(choice)} />;
}
