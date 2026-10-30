import { useEffect, useState } from 'react';

interface NativeState {
  enabled?: boolean;
  phase?: string;
  phase_token?: string;
  message?: string;
  trigger_policy?: { label: string };
}

export function NativeSyncControls() {
  const [state, setState] = useState<NativeState>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    async function refresh() {
      try {
        const response = await fetch('/api/native-sync', { signal: AbortSignal.timeout(2000) });
        if (response.ok && active) setState(await response.json());
      } catch {
        // Native controls are absent on ordinary kiosk sessions.
      }
    }
    void refresh();
    const timer = setInterval(refresh, 1000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);
  if (!state.enabled) return null;
  async function next() {
    if (busy) return;
    setBusy(true);
    try {
      const response = await fetch('/api/native-sync/next', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phase_token: state.phase_token }),
        signal: AbortSignal.timeout(3000),
      });
      if (!response.ok) throw new Error((await response.json()).error || 'Could not prepare capture');
      setError('');
    } catch (failure) {
      setError(String(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="native-sync" aria-label="Native capture controls">
      <span>
        {state.trigger_policy?.label ? `${state.trigger_policy.label}: ` : ''}
        {state.message}
      </span>
      {state.phase === 'next' ? (
        <button type="button" disabled={busy} onClick={next} aria-label="Area empty; prepare next attempt">
          Prepare capture
        </button>
      ) : null}
      {error ? <span role="alert">{error}</span> : null}
    </div>
  );
}
