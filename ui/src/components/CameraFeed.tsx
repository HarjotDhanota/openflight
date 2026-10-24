import { useEffect, useState } from 'react';
import { useCameraPointerDragScroll } from '../hooks/useCameraPointerDragScroll';
import { useCameraStill } from '../hooks/useCameraStill';
import type { SetupSnapshot } from '../hooks/useSetup';
import type { CameraAutoExposureStatus, CameraCaptureSettings } from '../stores/useCameraStore';
import type { Handed } from '../stores/useSystemStore';
import { SetupPicture } from './setup/SetupPicture';
import { confirmedBox } from './setup/setupOverlay';
import { verticalViewTargets } from '../utils/cameraView';
import { getServerOrigin } from '../utils/serverOrigin';
import './CameraFeed.css';

interface CameraFeedProps {
  captureSettings: CameraCaptureSettings;
  captureSettingsError: string | null;
  onUpdateCaptureSettings: (settings: Partial<CameraCaptureSettings>) => void;
  /** The kiosk's setup record: its confirmed box is drawn on the preview. */
  setup?: SetupSnapshot | null;
  handed?: Handed | null;
}

interface CaptureSettingsPanelProps {
  settings: CameraCaptureSettings;
  exposureQuality: ExposureQuality | null;
  error: string | null;
  onUpdate: (settings: Partial<CameraCaptureSettings>) => void;
  boxSet: boolean;
}

const PREVIEW_URL = `${getServerOrigin()}/api/camera/preview.jpg`;
const EXPOSURE_QUALITY_URL = `${getServerOrigin()}/api/camera/exposure-quality`;
const PREVIEW_REFRESH_MS = 5000;

interface ExposureQuality {
  sample_available: boolean;
  status: 'good' | 'too_dark' | 'too_bright' | 'marginal' | 'unavailable';
  recommendation?: 'brighter' | 'darker' | 'hold';
  message?: string;
  clipped_pct?: number;
  contrast?: number;
  auto_exposure?: CameraAutoExposureStatus;
}

// Why the controls are what they are (the swing exposure's status)
const AUTO_EXPOSURE_LABELS: Record<CameraAutoExposureStatus['status'], string> = {
  seeded: 'Waiting for the ball',
  ball_driven: 'Ball-driven',
  too_bright: 'Too bright',
  too_dark: 'Too dark',
  holding_no_ball: 'Holding: no ball',
  searching_for_ball: 'Searching for the ball',
  unavailable: 'Waiting',
  manual: 'Manual',
};

function formatGain(gain: number | undefined): string {
  return gain === undefined ? '—' : `${Math.round(gain * 100) / 100}`;
}

function CaptureSettingsPanel({ settings, exposureQuality, error, onUpdate, boxSet }: CaptureSettingsPanelProps) {
  const verticalOffset = settings.vertical_offset_px ?? 0;
  const verticalMin = settings.vertical_offset_min_px ?? verticalOffset;
  const verticalMax = settings.vertical_offset_max_px ?? verticalOffset;
  const verticalStep = settings.vertical_offset_step_px ?? 10;
  const viewTargets = verticalViewTargets(verticalOffset, verticalStep, settings.rotate_180 ?? false);
  const viewUpOffset = viewTargets.up;
  const viewDownOffset = viewTargets.down;
  const autoExposure = exposureQuality?.auto_exposure ?? settings.auto_exposure;
  const reading = autoExposure?.reading;
  const blurCeiling = autoExposure?.blur_ceiling_us ?? autoExposure?.envelope?.blur_ceiling_us;
  const sensorMin = autoExposure?.sensor_limits?.exposure_min_us ?? autoExposure?.envelope?.sensor_min_us;
  const exposureStatus = autoExposure?.capture_deferred
    ? 'Capturing shot'
    : (AUTO_EXPOSURE_LABELS[autoExposure?.status ?? 'unavailable'] ?? autoExposure?.status);

  return (
    <aside className="camera-settings">
      <div className="camera-settings__heading">
        <div>
          <span className="camera-settings__eyebrow">Capture controls</span>
          <h3>Camera setup</h3>
        </div>
        <span className={`camera-settings__armed ${settings.armed ? 'camera-settings__armed--ready' : ''}`}>
          {settings.armed ? 'Armed' : settings.running ? 'Filling' : 'Offline'}
        </span>
      </div>

      <div className="camera-settings__controls">
        <section className="camera-settings__section">
          <div className="camera-settings__section-title">
            <span>Sensor view</span>
            <small>real 320 × 200 crop</small>
          </div>
          <div className="camera-settings__view-controls">
            <button
              type="button"
              disabled={!settings.raw_crop_adjustable || viewUpOffset < verticalMin || viewUpOffset > verticalMax}
              onClick={() => onUpdate({ vertical_offset_px: viewUpOffset })}
            >
              View up
            </button>
            <output>{`${verticalOffset > 0 ? '+' : ''}${verticalOffset} px`}</output>
            <button
              type="button"
              disabled={!settings.raw_crop_adjustable || viewDownOffset < verticalMin || viewDownOffset > verticalMax}
              onClick={() => onUpdate({ vertical_offset_px: viewDownOffset })}
            >
              View down
            </button>
          </div>
          <p className="camera-settings__note">
            Moves the captured sensor window by 10 pixels and briefly rearms the rolling buffer.
          </p>
        </section>

        <section className="camera-settings__section">
          <div className="camera-settings__section-title">
            <span>Swing exposure</span>
            <small>metered on the resting ball before every swing</small>
          </div>
          <div
            className={`camera-settings__auto-status camera-settings__auto-status--${autoExposure?.status ?? 'unavailable'}`}
          >
            <div>
              <strong>{exposureStatus}</strong>
              <span>{autoExposure?.message ?? 'Waiting for the first camera check'}</span>
            </div>
            <span className="camera-settings__auto-dot" aria-hidden="true" />
          </div>
          <dl className="camera-settings__exposure-metrics">
            <div>
              <dt>Shutter</dt>
              <dd>{autoExposure?.exposure_us ?? settings.exposure_us ?? '—'} µs</dd>
            </div>
            <div>
              <dt>Gain</dt>
              <dd>{formatGain(autoExposure?.gain ?? settings.gain)}×</dd>
            </div>
            <div>
              <dt>Blur ceiling</dt>
              <dd>{blurCeiling ? `${blurCeiling} µs` : '—'}</dd>
            </div>
            <div>
              <dt>Ball</dt>
              <dd>
                {reading
                  ? `${Math.round(reading.signal_dn)} DN · peak ${reading.peak_fraction.toFixed(2)}`
                  : 'not seen'}
              </dd>
            </div>
          </dl>
          {autoExposure?.frame_label && (
            <p className="camera-settings__frame-label">
              Frames labelled: <strong>{autoExposure.frame_label}</strong>
            </p>
          )}
          <div
            className={`camera-settings__analysis-state ${autoExposure?.analysis_eligible ? 'camera-settings__analysis-state--ready' : ''}`}
          >
            <strong>{autoExposure?.analysis_eligible ? 'Camera analysis active' : 'Radar fallback active'}</strong>
            <span>
              {autoExposure?.analysis_eligible
                ? 'Camera-assisted aim, club path, and attack angle are eligible.'
                : 'Preview and raw clips continue recording; camera-derived metrics are withheld.'}
            </span>
          </div>
          <p className="camera-settings__note">
            Shortest exposure first, gain before exposure, never past the blur ceiling
            {sensorMin ? ` (the sensor's shortest is ${sensorMin} µs)` : ''}. Too bright at the shortest: shade the ball
            area or fit an ND filter. Too dark at the ceiling: add light. Clips are labelled, never blocked.
          </p>
        </section>

        <section className="camera-settings__section">
          <div className="camera-settings__section-title">
            <span>Ball box</span>
          </div>
          <div className="camera-settings__ball-guide-position">
            {boxSet ? 'Confirmed on the Setup tab' : 'Not set: confirm the ball in the box on the Setup tab'}
          </div>
        </section>

        <section className="camera-settings__section camera-settings__section--summary">
          <div className="camera-settings__section-title">
            <span>Capture</span>
            <small>restart required to change</small>
          </div>
          <dl className="camera-settings__summary">
            <div>
              <dt>Mode</dt>
              <dd>{settings.width && settings.height ? `${settings.width} × ${settings.height}` : '—'}</dd>
            </div>
            <div>
              <dt>Requested</dt>
              <dd>{settings.fps ? `${settings.fps.toFixed(0)} fps` : '—'}</dd>
            </div>
            <div>
              <dt>Buffer</dt>
              <dd>
                {settings.pre_ms !== undefined && settings.post_ms !== undefined
                  ? `${settings.pre_ms.toFixed(0)} / ${settings.post_ms.toFixed(0)} ms`
                  : '—'}
              </dd>
            </div>
            <div>
              <dt>Frames</dt>
              <dd>
                {settings.pre_frames !== undefined && settings.post_frames !== undefined
                  ? `${settings.pre_frames} + ${settings.post_frames}`
                  : '—'}
              </dd>
            </div>
          </dl>
        </section>

        {error && <p className="camera-settings__error">{error}</p>}
      </div>
    </aside>
  );
}

/**
 * Camera tab.
 *
 * When the high-speed capture runtime is active (--camera-capture), shows a
 * still refreshed every 5 s from the concurrent preview stream. The raw
 * rolling buffer keeps running, so shots are never missed while viewing.
 */
export function CameraFeed({
  captureSettings,
  captureSettingsError,
  onUpdateCaptureSettings,
  setup = null,
  handed = null,
}: CameraFeedProps) {
  useCameraPointerDragScroll();
  const {
    state: previewState,
    src: previewSrc,
    updatedAt: lastUpdated,
  } = useCameraStill(PREVIEW_URL, PREVIEW_REFRESH_MS);
  const [exposureQuality, setExposureQuality] = useState<ExposureQuality | null>(null);
  const box = confirmedBox(setup);

  // the exposure is read after each new frame, as the frame it describes
  useEffect(() => {
    if (!lastUpdated) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const qualityResponse = await fetch(`${EXPOSURE_QUALITY_URL}?t=${Date.now()}`, { cache: 'no-store' });
        if (qualityResponse.ok && !cancelled) {
          setExposureQuality(await qualityResponse.json());
        }
      } catch {
        // Keep the last reading and retry with the next frame.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [lastUpdated]);

  if (previewState === 'available' || previewState === 'checking') {
    return (
      <div className="camera-feed camera-feed--capture">
        <div className="camera-feed__header">
          <div>
            <span className="camera-feed__eyebrow">High-speed capture</span>
            <h2 className="camera-feed__title">Camera alignment</h2>
          </div>
          <div className="camera-feed__header-status">
            <span
              className={`camera-feed__exposure-quality camera-feed__exposure-quality--${exposureQuality?.status ?? 'checking'}`}
              title={exposureQuality?.message ?? 'Analyzing the impact area'}
            >
              Auto exposure:{' '}
              {exposureQuality?.auto_exposure ? AUTO_EXPOSURE_LABELS[exposureQuality.auto_exposure.status] : 'checking'}
            </span>
            {lastUpdated && <span className="camera-feed__timestamp">preview {lastUpdated.toLocaleTimeString()}</span>}
          </div>
        </div>
        <div className="camera-feed__workspace">
          <div className="camera-feed__preview-column">
            {previewSrc ? (
              <div className="camera-feed__stream">
                <SetupPicture src={previewSrc} alt="Camera preview" handed={handed} box={box} />
                <div className="camera-feed__overlay">
                  <div className="camera-feed__status">Rolling buffer remains armed</div>
                </div>
              </div>
            ) : (
              <div className="camera-feed__message camera-feed__message--preview">
                <h3>Waiting for camera</h3>
                <p>Fetching the first preview from the capture runtime</p>
              </div>
            )}
          </div>
          <CaptureSettingsPanel
            settings={captureSettings}
            exposureQuality={exposureQuality}
            error={captureSettingsError}
            onUpdate={onUpdateCaptureSettings}
            boxSet={box !== null}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="camera-feed camera-feed--unavailable">
      <div className="camera-feed__message">
        <h3>Camera Not Available</h3>
        <p>Start the server with --camera-capture to enable previews.</p>
      </div>
    </div>
  );
}
