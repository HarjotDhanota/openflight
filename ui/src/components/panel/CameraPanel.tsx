import type { SetupSnapshot } from '../../hooks/useSetup';
import type { CameraCaptureSettings } from '../../stores/useCameraStore';
import type { Handed } from '../../stores/useSystemStore';
import { CameraFeed } from '../CameraFeed';

interface CameraPanelProps {
  captureSettings: CameraCaptureSettings;
  captureSettingsError: string | null;
  onUpdateCaptureSettings: (settings: Partial<CameraCaptureSettings>) => void;
  setup?: SetupSnapshot | null;
  handed?: Handed | null;
}

/** High-speed capture setup and preview panel. */
export function CameraPanel({
  captureSettings,
  captureSettingsError,
  onUpdateCaptureSettings,
  setup = null,
  handed = null,
}: CameraPanelProps) {
  return (
    <div className="panel camera-panel camera-panel--capture">
      <div className="panel__body camera-panel__body camera-panel__body--capture">
        <CameraFeed
          captureSettings={captureSettings}
          captureSettingsError={captureSettingsError}
          onUpdateCaptureSettings={onUpdateCaptureSettings}
          setup={setup}
          handed={handed}
        />
      </div>
    </div>
  );
}
