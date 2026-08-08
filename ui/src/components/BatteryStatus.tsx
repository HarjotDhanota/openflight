import { useEffect, useState } from 'react';
import type { PowerView } from '../stores/usePowerStore';
import { socketService } from '../services/socketService';
import './BatteryStatus.css';

/**
 * Tick a server-supplied duration down locally.
 *
 * The server sends seconds remaining rather than a deadline, because its
 * deadline comes from time.monotonic() whose epoch is process-local and
 * meaningless here. Resetting on every change of `seconds` is what makes a
 * reconnect correct: get_power triggers a freshly computed value and the
 * ticker adopts it instead of continuing from its own drifted count.
 */
function useCountdown(seconds: number | null): number | null {
  const [sourceSeconds, setSourceSeconds] = useState(seconds);
  const [remaining, setRemaining] = useState(seconds);

  if (seconds !== sourceSeconds) {
    setSourceSeconds(seconds);
    setRemaining(seconds);
  }

  useEffect(() => {
    if (seconds === null) return undefined;
    const id = setInterval(() => {
      setRemaining((value) => (value === null ? null : Math.max(0, value - 1)));
    }, 1000);
    return () => clearInterval(id);
  }, [seconds]);

  return remaining;
}

/**
 * Battery level and supply health.
 *
 * Two indicators because there are two independent failure modes: an
 * exhausted pack and a sagging 5V rail end a session for different reasons.
 * Each half renders only when its reader is present, so a build with a UPS
 * and no Pi 5 PMIC shows a bar and no dot, and vice versa.
 *
 * Takes the view as a prop rather than reading the store, matching
 * ConnectionStatus and SimStatus. Beyond convention, this is what makes it
 * testable: zustand v5 backs its hook with useSyncExternalStore, whose server
 * snapshot is getInitialState(), so a store-reading component renders its
 * initial state forever under renderToString and setState has no effect.
 */
interface BatteryStatusProps {
  view: PowerView | null;
}

export function BatteryStatus({ view }: BatteryStatusProps) {
  const [expanded, setExpanded] = useState(false);
  // Hooks run before any early return, so the countdown is driven even on the
  // renders where nothing is displayed.
  const countdown = useCountdown(view?.shutdown_remaining_seconds ?? null);

  if (!view) return null;

  const hasPack = view.pack_level !== 'unknown' && view.pack_percent !== null;
  const hasRail = view.rail_level !== 'unknown';
  if (!hasPack && !hasRail) return null;

  const pending = view.pending_shutdown;

  return (
    <div className="battery-status">
      <button
        type="button"
        className="battery-status__summary"
        onClick={() => setExpanded((open) => !open)}
      >
        {hasPack && (
          <>
            <span
              className={`battery-status__bar battery-status__bar--${view.pack_level}`}
              style={{ '--fill': `${view.pack_percent}%` } as React.CSSProperties}
            />
            <span className="battery-status__percent">{Math.round(view.pack_percent!)}%</span>
          </>
        )}
        {view.source === 'external' && (
          <span className="battery-status__bolt" aria-label="On external power">
            ⚡
          </span>
        )}
        {hasRail && (
          <span
            className={`battery-status__dot battery-status__dot--${view.rail_level}`}
            aria-label={`Supply health: ${view.rail_level}`}
          />
        )}
      </button>

      {expanded && (
        <dl className="battery-status__detail">
          {hasPack && (
            <>
              <dt>Pack</dt>
              <dd>
                {view.pack_volts?.toFixed(2)} V · {Math.round(view.pack_percent!)}% ·{' '}
                {view.source === 'external' ? 'external power' : 'on battery'}
              </dd>
            </>
          )}
          {hasRail && (
            <>
              <dt>Rail</dt>
              <dd>{view.rail_volts?.toFixed(2)} V</dd>
            </>
          )}
          {view.runtime_minutes !== null && (
            <>
              <dt>Est.</dt>
              <dd>~{view.runtime_minutes} min</dd>
            </>
          )}
        </dl>
      )}

      {view.warnings.length > 0 && (
        <div className="battery-status__warning" role="status">
          {view.warnings.join(' · ')}
        </div>
      )}

      {pending && (
        <div className="battery-status__shutdown" role="alert">
          <span>
            Shutting down in {`${countdown ?? 0}s`} to protect the battery. {pending.reason}.
          </span>
          <button type="button" onClick={() => socketService.cancelShutdown(pending.id)}>
            Keep running
          </button>
        </div>
      )}
    </div>
  );
}
