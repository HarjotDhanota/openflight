import { create } from 'zustand';
import type { AirConditions } from '../types/socket';

interface AirState {
  /** Null until the server has sent the air; standard air is a reading of its own. */
  conditions: AirConditions | null;
  setConditions: (conditions: AirConditions) => void;
}

export const useAirStore = create<AirState>((set) => ({
  conditions: null,
  setConditions: (conditions) => set({ conditions }),
}));
