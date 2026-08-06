import { create } from 'zustand';
import type { EnvironmentReading } from '../types/socket';

interface EnvironmentState {
  /** Null until the server has answered, which is not the same as "no sensor" --
   *  no sensor is a reading whose source is "default". */
  reading: EnvironmentReading | null;
  setReading: (reading: EnvironmentReading) => void;
}

export const useEnvironmentStore = create<EnvironmentState>((set) => ({
  reading: null,
  setReading: (reading) => set({ reading }),
}));
