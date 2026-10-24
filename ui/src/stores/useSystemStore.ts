import { create } from 'zustand';
import type { PowerStatus } from '../types/power';
import type { ReadyLight } from '../types/readyLight';
import type { SimShotInfo, SimShotNotSent, SimStatus } from '../types/socket';

export type Handed = 'RH' | 'LH';

interface SystemState {
  connected: boolean;
  mockMode: boolean;
  debugMode: boolean;
  cloudUploadState: 'idle' | 'running' | 'complete' | 'error';
  cloudUploadMessage: string;
  simStatuses: Record<string, SimStatus>;
  latestSimShots: Record<string, SimShotInfo>;
  /** The latest shot withheld from the simulators, if any. */
  simShotNotSent: SimShotNotSent | null;
  serverClub: string | null;
  handed: Handed | null;
  powerStatus: PowerStatus | null;
  readyLight: ReadyLight | null;
  setConnected: (connected: boolean) => void;
  setMockMode: (mockMode: boolean) => void;
  setDebugMode: (debugMode: boolean) => void;
  setCloudUploadStatus: (state: SystemState['cloudUploadState'], message: string) => void;
  setSimStatus: (status: SimStatus) => void;
  setLatestSimShot: (shot: SimShotInfo) => void;
  setSimShotNotSent: (notSent: SimShotNotSent | null) => void;
  setServerClub: (club: string | null) => void;
  setHanded: (handed: Handed | null) => void;
  setPowerStatus: (status: PowerStatus) => void;
  setReadyLight: (light: ReadyLight | null) => void;
}

export const useSystemStore = create<SystemState>((set) => ({
  connected: false,
  mockMode: false,
  debugMode: false,
  cloudUploadState: 'idle',
  cloudUploadMessage: '',
  simStatuses: {},
  latestSimShots: {},
  simShotNotSent: null,
  serverClub: null,
  handed: null,
  powerStatus: null,
  readyLight: null,
  setConnected: (connected) => set({ connected }),
  setMockMode: (mockMode) => set({ mockMode }),
  setDebugMode: (debugMode) => set({ debugMode }),
  setCloudUploadStatus: (cloudUploadState, cloudUploadMessage) => set({ cloudUploadState, cloudUploadMessage }),
  setSimStatus: (status) =>
    set((state) => ({
      simStatuses: { ...state.simStatuses, [status.target]: status },
    })),
  setLatestSimShot: (shot) =>
    set((state) => ({
      latestSimShots: { ...state.latestSimShots, [shot.target]: shot },
    })),
  setSimShotNotSent: (simShotNotSent) => set({ simShotNotSent }),
  setServerClub: (serverClub) => set({ serverClub }),
  setHanded: (handed) => set({ handed }),
  setPowerStatus: (status) => set({ powerStatus: status }),
  setReadyLight: (readyLight) => set({ readyLight }),
}));
