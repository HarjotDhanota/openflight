import { shotProcessingCoversResult, type ShotProcessingPhase } from '../stores/useShotStore';

/** Everything the app can draw over the live result. */
export interface LiveOverlays {
  replayOpen: boolean;
  shutdownOpen: boolean;
  pickerOpen: boolean;
  menuOpen: boolean;
  profileDialogOpen: boolean;
  clearSessionOpen: boolean;
  processingPhase: ShotProcessingPhase | null;
}

/** True while any overlay covers the live result, so it is not acknowledged as shown. */
export function isLiveResultCovered(overlays: LiveOverlays): boolean {
  return (
    overlays.replayOpen ||
    overlays.shutdownOpen ||
    overlays.pickerOpen ||
    overlays.menuOpen ||
    overlays.profileDialogOpen ||
    overlays.clearSessionOpen ||
    shotProcessingCoversResult(overlays.processingPhase)
  );
}
