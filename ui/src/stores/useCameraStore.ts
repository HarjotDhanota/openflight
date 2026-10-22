import { create } from 'zustand';

export interface CameraCaptureSettings {
  available: boolean;
  enabled?: boolean;
  running?: boolean;
  armed?: boolean;
  width?: number;
  height?: number;
  fps?: number;
  pre_ms?: number;
  post_ms?: number;
  pre_frames?: number;
  post_frames?: number;
  buffered_frames?: number;
  required_pre_frames?: number;
  exposure_us?: number;
  max_exposure_us?: number;
  gain?: number;
  stream?: string;
  rotate_180?: boolean;
  mirror_horizontal?: boolean;
  roll_correction_deg?: number;
  alignment_x_pct?: number;
  alignment_y_pct?: number;
  raw_crop_adjustable?: boolean;
  vertical_offset_px?: number;
  vertical_offset_min_px?: number;
  vertical_offset_max_px?: number;
  vertical_offset_step_px?: number;
  auto_exposure?: CameraAutoExposureStatus;
}

/**
 * The kiosk's swing exposure (13 Oct 2026): the resting ball is the light meter,
 * re-checked before every swing. `status` is why the controls are what they are.
 */
export type SwingExposureStatus =
  | 'seeded'
  | 'ball_driven'
  | 'too_bright'
  | 'too_dark'
  | 'holding_no_ball'
  | 'searching_for_ball'
  | 'unavailable'
  | 'manual';

export interface CameraAutoExposureStatus {
  enabled: boolean;
  status: SwingExposureStatus;
  analysis_eligible: boolean;
  message: string;
  frame_label?: string | null;
  capture_deferred?: boolean;
  exposure_us?: number;
  gain?: number;
  blur_ceiling_us?: number | null;
  sensor_limits?: {
    exposure_min_us: number;
    exposure_max_us: number | null;
    gain_min: number;
    gain_max: number;
    gain_source?: string;
  } | null;
  reading?: {
    signal_dn: number;
    peak_fraction: number;
    clipped_pct: number;
    exposure_us: number;
    gain: number;
    source?: string;
  } | null;
  envelope?: {
    sensor_min_us: number;
    gain_cap: number;
    blur_ceiling_us: number;
    exposure_ceiling_us: number;
    target_peak_fraction: number;
  } | null;
  observation?: {
    sample_available: boolean;
    status: 'good' | 'too_dark' | 'too_bright' | 'marginal' | 'unavailable';
    median?: number | null;
    contrast?: number | null;
    clipped_pct?: number | null;
  };
}

interface CameraState {
  captureSettings: CameraCaptureSettings;
  captureSettingsError: string | null;
  setCaptureSettings: (settings: CameraCaptureSettings) => void;
  setCaptureSettingsError: (error: string | null) => void;
}

export const useCameraStore = create<CameraState>((set) => ({
  captureSettings: { available: false },
  captureSettingsError: null,
  setCaptureSettings: (settings) => set({ captureSettings: settings, captureSettingsError: null }),
  setCaptureSettingsError: (error) => set({ captureSettingsError: error }),
}));
