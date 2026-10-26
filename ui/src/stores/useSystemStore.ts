import { create } from 'zustand';
import type { PowerStatus } from '../types/power';
import type { ReadyLight } from '../types/readyLight';
import type { SimShotInfo, SimShotNotSent, SimStatus } from '../types/socket';

export type Handed = 'RH' | 'LH';
/** Who set the hand in force: a connected simulator wins over the kiosk's choice. */
export type HandedSource = 'simulator' | 'profile' | 'setup' | 'default';
/** How the IWR radar's data reaches the kiosk (server ``iwr6843_transfer_status``). */
export interface Iwr6843Transfer {
  enabled: boolean;
  requested: 'full' | 'two_round';
  effective: 'full' | 'two_round';
  available: boolean;
  reason: string | null;
  last_fallback: string | null;
}

export const HANDED_SOURCES: readonly HandedSource[] = ['simulator', 'profile', 'setup', 'default'];

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
  handedSource: HandedSource | null;
  powerStatus: PowerStatus | null;
  readyLight: ReadyLight | null;
  iwr6843Transfer: Iwr6843Transfer | null;
  iwr6843TransferError: string | null;
  setConnected: (connected: boolean) => void;
  setMockMode: (mockMode: boolean) => void;
  setDebugMode: (debugMode: boolean) => void;
  setCloudUploadStatus: (state: SystemState['cloudUploadState'], message: string) => void;
  setSimStatus: (status: SimStatus) => void;
  setLatestSimShot: (shot: SimShotInfo) => void;
  setSimShotNotSent: (notSent: SimShotNotSent | null) => void;
  setServerClub: (club: string | null) => void;
  setHanded: (handed: Handed | null, source?: HandedSource | null) => void;
  setPowerStatus: (status: PowerStatus) => void;
  setReadyLight: (light: ReadyLight | null) => void;
  setIwr6843Transfer: (status: Iwr6843Transfer | null) => void;
  setIwr6843TransferError: (error: string | null) => void;
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
  handedSource: null,
  powerStatus: null,
  readyLight: null,
  iwr6843Transfer: null,
  iwr6843TransferError: null,
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
  setHanded: (handed, source = null) => set({ handed, handedSource: source }),
  setPowerStatus: (status) => set({ powerStatus: status }),
  setReadyLight: (readyLight) => set({ readyLight }),
  setIwr6843Transfer: (iwr6843Transfer) => set({ iwr6843Transfer, iwr6843TransferError: null }),
  setIwr6843TransferError: (iwr6843TransferError) => set({ iwr6843TransferError }),
}));
