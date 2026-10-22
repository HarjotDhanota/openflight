import type { SessionStats, Shot } from '../types/shot';
import { useShotStore } from '../stores/useShotStore';
import { playSwingCapturedCue } from '../utils/audioCue';
import type { TraceTag } from './paintTrace';

export interface ShotMessage {
  shot: Shot;
  stats: SessionStats;
  pending?: {
    iwr6843?: boolean;
    camera?: boolean;
  };
  /** Present only when the server runs the stage 1b Pi trace. */
  trace?: TraceTag;
}

export interface ShotUpdateMessage {
  shot: Shot;
  stats: SessionStats;
  pending?: Record<string, boolean>;
  enrichment?: {
    // skipped: the hardware metrics will never come; pending: they are
    // deferred and will arrive as a later update to this same shot.
    status: 'skipped' | 'pending';
    reason: string;
    hardware: string[];
  };
  /** Present only when the server runs the stage 1b Pi trace. */
  trace?: TraceTag;
}

function showPendingHardware(pending: ShotMessage['pending'], timestamp: string) {
  const shotStore = useShotStore.getState();
  if (
    shotStore.latestShot?.timestamp !== timestamp ||
    (shotStore.shotProcessingPhase !== null && shotStore.shotProcessingShotTimestamp !== timestamp)
  ) {
    return;
  }
  if (pending?.iwr6843 && pending?.camera) {
    shotStore.startShotProcessing('hardware_enrichment', timestamp);
  } else if (pending?.iwr6843) {
    shotStore.startShotProcessing('iwr_dump', timestamp);
  } else if (pending?.camera) {
    shotStore.startShotProcessing('camera_processing', timestamp);
  }
}

export function handleShotMessage(data: ShotMessage) {
  useShotStore.getState().addShot(data.shot);
  showPendingHardware(data.pending, data.shot.timestamp);
  playSwingCapturedCue();
}

export function handleShotUpdate(data: ShotUpdateMessage) {
  useShotStore.getState().updateShot(data.shot);
  if (data.enrichment?.status === 'pending') {
    showPendingHardware(data.pending, data.shot.timestamp);
  }
}
