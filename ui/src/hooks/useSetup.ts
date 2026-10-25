import { useCallback, useEffect, useState } from 'react';
import { socketService } from '../services/socketService';
import { getServerOrigin } from '../utils/serverOrigin';

type Px = [number, number];

/** One section of the setup record; `state` is not_set, confirmed, set, skipped, default or stale. */
export interface SetupSection {
  state: string;
  reason?: string | null;
  source?: string;
  box_px?: number[];
  /** The confirmed box's outline, in camera pixels; older records have none. */
  outline_px?: Px[];
  ball_px?: Px | null;
  diameter_px?: number | null;
  aim_px?: Px;
  floor_line?: [Px, Px] | null;
  yaw_deg?: number;
  uncertainty_deg?: number | null;
  floor_line_used?: boolean;
  range_m?: number;
  uncertainty_m?: number | null;
  value_m?: number;
  lens_height_m?: number | null;
}

/** The server's latest look: the ball against the box, and where a new aim starts. */
export interface SetupLive {
  box?: {
    state: string;
    message?: string;
    ball_px: Px | null;
    diameter_px: number | null;
    outline_px: Px[];
  };
  aim?: { default_aim_px: Px };
  /** False on a unit without a camera: no step can be done there. */
  camera_available?: boolean;
  /** False when the kiosk as started can never set up (no tilt sensor, ...); the reason says why. */
  setup_possible?: boolean;
  setup_blocked_reason?: string | null;
  /** True when that is a failure (the tilt sensor did not start): the ready light holds red. */
  setup_fault?: boolean;
}

/** GET /api/setup and the `setup_changed` event. */
export interface SetupSnapshot {
  schema?: string;
  revision: number;
  box: SetupSection;
  aim: SetupSection;
  ball_range: SetupSection;
  net_range: SetupSection;
  scene: SetupSection;
  live: SetupLive;
  /** Sections saved but not in effect, and why. */
  unapplied: Record<string, string>;
}

export type SetupActionSection = 'box' | 'aim' | 'range';

export interface SetupActionBody {
  action: string;
  aim_px?: Px;
  floor_line_px?: [Px, Px];
}

/** `reason` is the server's refusal; null with `ok` false means the kiosk was not reached. */
export interface SetupActionResult {
  ok: boolean;
  reason: string | null;
}

const SECTIONS = ['box', 'aim', 'ball_range', 'net_range', 'scene'] as const;

function isSnapshot(value: unknown): value is SetupSnapshot {
  if (!value || typeof value !== 'object') return false;
  const record = value as Record<string, unknown>;
  return (
    typeof record.revision === 'number' &&
    SECTIONS.every((name) => {
      const section = record[name];
      return !!section && typeof section === 'object' && typeof (section as SetupSection).state === 'string';
    })
  );
}

function normalised(snapshot: SetupSnapshot): SetupSnapshot {
  return { ...snapshot, live: snapshot.live ?? {}, unapplied: snapshot.unapplied ?? {} };
}

const READ_ATTEMPTS = 3;
const RETRY_MS = 2000;

/**
 * The kiosk's setup record, kept current from `setup_changed` and re-read on every socket connect
 * (a restarted kiosk may have marked sections stale), with its actions and `loadFailed`.
 */
export function useSetup(): {
  setup: SetupSnapshot | null;
  loadFailed: boolean;
  act: (section: SetupActionSection, body: SetupActionBody) => Promise<SetupActionResult>;
} {
  const [setup, setSetup] = useState<SetupSnapshot | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);

  // a repeated snapshot keeps the state as it is, so the app does not re-render for it
  const take = useCallback((value: unknown): boolean => {
    if (!isSnapshot(value)) return false;
    const next = normalised(value);
    setSetup((previous) => (previous && JSON.stringify(previous) === JSON.stringify(next) ? previous : next));
    return true;
  }, []);

  useEffect(() => {
    let cancelled = false;
    let reading = 0;
    let retry: ReturnType<typeof setTimeout> | null = null;
    const read = (attempt = 1, generation = ++reading) => {
      if (retry) clearTimeout(retry);
      retry = null;
      fetch(`${getServerOrigin()}/api/setup`, { cache: 'no-store' })
        .then((response) => (response.ok ? response.json() : null))
        .catch(() => null)
        .then((body) => {
          if (cancelled || generation !== reading) return;
          if (take(body)) {
            setLoadFailed(false);
          } else if (attempt < READ_ATTEMPTS) {
            retry = setTimeout(() => read(attempt + 1, generation), RETRY_MS);
          } else {
            setLoadFailed(true);
          }
        });
    };
    const unsubscribeChanged = socketService.onSetupChanged((value) => {
      if (!cancelled) take(value);
    });
    const unsubscribeConnect = socketService.onConnect(() => read());
    read();
    return () => {
      cancelled = true;
      if (retry) clearTimeout(retry);
      unsubscribeChanged?.();
      unsubscribeConnect?.();
    };
  }, [take]);

  const act = useCallback(
    async (section: SetupActionSection, body: SetupActionBody) => {
      try {
        const response = await fetch(`${getServerOrigin()}/api/setup/${section}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const answer = (await response.json()) as { ok?: unknown; reason?: unknown; setup?: unknown };
        take(answer.setup);
        return {
          ok: answer.ok === true,
          reason: typeof answer.reason === 'string' ? answer.reason : null,
        };
      } catch {
        return { ok: false, reason: null };
      }
    },
    [take]
  );

  return { setup, loadFailed, act };
}
