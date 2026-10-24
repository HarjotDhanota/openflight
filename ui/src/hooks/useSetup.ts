import { useCallback, useEffect, useState } from 'react';
import { socketService } from '../services/socketService';
import { getServerOrigin } from '../utils/serverOrigin';

type Px = [number, number];

/** One section of the setup record; `state` is not_set, confirmed, set, skipped, default or stale. */
export interface SetupSection {
  state: string;
  reason?: string | null;
  source?: string;
  // box
  box_px?: number[];
  ball_px?: Px | null;
  diameter_px?: number | null;
  image_size_px?: Px;
  // aim
  aim_px?: Px;
  floor_line?: [Px, Px] | null;
  yaw_deg?: number;
  uncertainty_deg?: number | null;
  floor_line_used?: boolean;
  // ball range
  range_m?: number;
  uncertainty_m?: number | null;
  // net range
  value_m?: number;
  // scene
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

/** The kiosk's setup record, kept current from `setup_changed`, and its actions. */
export function useSetup(): {
  setup: SetupSnapshot | null;
  act: (section: SetupActionSection, body: SetupActionBody) => Promise<SetupActionResult>;
} {
  const [setup, setSetup] = useState<SetupSnapshot | null>(null);

  useEffect(() => {
    let cancelled = false;
    const take = (value: unknown) => {
      if (!cancelled && isSnapshot(value)) setSetup(normalised(value));
    };
    const unsubscribe = socketService.onSetupChanged(take);
    fetch(`${getServerOrigin()}/api/setup`, { cache: 'no-store' })
      .then((response) => (response.ok ? response.json() : null))
      .then(take)
      .catch(() => {
        // no setup service (an older kiosk) or no kiosk yet: the screen says so
      });
    return () => {
      cancelled = true;
      unsubscribe?.();
    };
  }, []);

  const act = useCallback(async (section: SetupActionSection, body: SetupActionBody) => {
    try {
      const response = await fetch(`${getServerOrigin()}/api/setup/${section}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const answer = (await response.json()) as { ok?: unknown; reason?: unknown; setup?: unknown };
      if (isSnapshot(answer.setup)) setSetup(normalised(answer.setup));
      return {
        ok: answer.ok === true,
        reason: typeof answer.reason === 'string' ? answer.reason : null,
      };
    } catch {
      return { ok: false, reason: null };
    }
  }, []);

  return { setup, act };
}
