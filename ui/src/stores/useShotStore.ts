import { create } from 'zustand';
import type { Shot } from '../types/shot';

/** Duration to keep isNewShot true — covers the longest animation (shot-glow: 2s) */
const NEW_SHOT_DURATION_MS = 2500;
const SHOT_PROCESSING_TIMEOUT_MS = 30_000;
export type ShotProcessingPhase = 'capturing' | 'calculating';

interface ShotState {
  latestShot: Shot | null;
  shots: Shot[];
  isNewShot: boolean;
  shotProcessingPhase: ShotProcessingPhase | null;
  shotVersion: number;
  startShotProcessing: (phase: ShotProcessingPhase) => void;
  finishShotProcessing: () => void;
  addShot: (shot: Shot) => void;
  setShots: (shots: Shot[]) => void;
  clearShots: () => void;
}

export const useShotStore = create<ShotState>((set) => {
  let timerRef: ReturnType<typeof setTimeout> | null = null;
  let processingTimerRef: ReturnType<typeof setTimeout> | null = null;

  const finishShotProcessing = () => {
    if (processingTimerRef) clearTimeout(processingTimerRef);
    processingTimerRef = null;
    set({ shotProcessingPhase: null });
  };

  return {
    latestShot: null,
    shots: [],
    isNewShot: false,
    shotProcessingPhase: null,
    shotVersion: 0,
    startShotProcessing: (shotProcessingPhase) => {
      if (processingTimerRef) clearTimeout(processingTimerRef);
      set({ shotProcessingPhase });
      processingTimerRef = setTimeout(finishShotProcessing, SHOT_PROCESSING_TIMEOUT_MS);
    },
    finishShotProcessing,
    addShot: (shot) => {
      if (processingTimerRef) clearTimeout(processingTimerRef);
      processingTimerRef = null;
      set((state) => {
        const updated = [...state.shots, shot];
        const newShots = updated.length > 200 ? updated.slice(-200) : updated;
        return {
          latestShot: shot,
          shots: newShots,
          isNewShot: true,
          shotProcessingPhase: null,
          shotVersion: state.shotVersion + 1,
        };
      });

      if (timerRef) clearTimeout(timerRef);
      timerRef = setTimeout(() => {
        set({ isNewShot: false });
      }, NEW_SHOT_DURATION_MS);
    },
    setShots: (newShots) => {
      finishShotProcessing();
      set({
        shots: newShots,
        latestShot: newShots.length > 0 ? newShots[newShots.length - 1] : null,
      });
    },
    clearShots: () => {
      if (timerRef) clearTimeout(timerRef);
      if (processingTimerRef) clearTimeout(processingTimerRef);
      processingTimerRef = null;
      set({
        latestShot: null,
        shots: [],
        isNewShot: false,
        shotProcessingPhase: null,
      });
    },
  };
});
