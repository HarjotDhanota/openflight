import { create } from 'zustand';

export interface PendingShutdown {
  id: string;
  reason: string;
  // No deadline field: the server's is monotonic and process-local, so it is
  // stripped before emit. Use shutdown_remaining_seconds below.
}

export interface PowerView {
  pack_volts: number | null;
  pack_percent: number | null;
  pack_level: 'ok' | 'low' | 'critical' | 'unknown';
  rail_volts: number | null;
  rail_level: 'green' | 'amber' | 'red' | 'unknown';
  source: 'external' | 'battery' | 'unknown';
  runtime_minutes: number | null;
  shutdown_eligible: boolean;
  pending_shutdown: PendingShutdown | null;
  shutdown_remaining_seconds: number | null;
  warnings: string[];
}

interface PowerState {
  view: PowerView | null;
  setView: (view: PowerView) => void;
}

export const usePowerStore = create<PowerState>((set) => ({
  view: null,
  setView: (view) => set({ view }),
}));
