import { renderToString } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import type { CameraCaptureSettings } from '../stores/useCameraStore';
import { verticalViewTargets } from '../utils/cameraView';
import { CameraFeed } from './CameraFeed';

const captureSettings: CameraCaptureSettings = {
  available: true,
  enabled: true,
  running: true,
  armed: true,
  width: 320,
  height: 200,
  fps: 600,
  pre_ms: 150,
  post_ms: 50,
  pre_frames: 90,
  post_frames: 30,
  exposure_us: 500,
  max_exposure_us: 1666,
  gain: 2,
  rotate_180: true,
  alignment_x_pct: 48,
  alignment_y_pct: 55,
  raw_crop_adjustable: true,
  vertical_offset_px: -20,
  vertical_offset_min_px: -70,
  vertical_offset_max_px: 70,
  vertical_offset_step_px: 10,
  auto_exposure: {
    enabled: true,
    status: 'ball_driven',
    reason: 'resting_ball',
    analysis_eligible: true,
    message: "Ball-driven: Holding 294 us x gain 15.9375; the ball's peak is 0.49 of clip (target 0.55)",
    frame_label: null,
    exposure_us: 294,
    gain: 15.9375,
    blur_ceiling_us: 294,
    sensor_limits: { exposure_min_us: 9, exposure_max_us: 8000, gain_min: 1, gain_max: 15.9375 },
    reading: { signal_dn: 69.4, peak_fraction: 0.487, clipped_pct: 0, exposure_us: 294, gain: 15.9375 },
  },
};

describe('CameraFeed', () => {
  it('maps view direction through the 180-degree mount rotation', () => {
    expect(verticalViewTargets(0, 10, true)).toEqual({ up: 10, down: -10 });
    expect(verticalViewTargets(0, 10, false)).toEqual({ up: -10, down: 10 });
  });

  it('renders the dominant preview workspace and operator settings', () => {
    const html = renderToString(
      <CameraFeed captureSettings={captureSettings} captureSettingsError={null} onUpdateCaptureSettings={vi.fn()} />
    );

    expect(html).toContain('camera-feed__workspace');
    expect(html).toContain('Camera setup');
    expect(html).toContain('Swing exposure');
    expect(html).toContain('metered on the resting ball before every swing');
    expect(html).not.toContain('calibrates once at startup');
    expect(html).toContain('Auto exposure');
    expect(html).toContain('camera-feed__exposure-quality');
    expect(html).toContain('Camera analysis active');
    expect(html).toContain('Ball-driven');
    expect(html).toContain('294<!-- --> µs');
    expect(html).toContain('15.94<!-- -->×');
    expect(html).toContain('Blur ceiling');
    expect(html).toContain('294 µs');
    expect(html).toContain('69 DN · peak 0.49');
    expect(html).toContain('the sensor&#x27;s shortest is 9 µs');
    expect(html).not.toContain('Motion blur risk');
    expect(html).not.toContain('Environment profile');
    expect(html).not.toContain('Darker');
    expect(html).not.toContain('Brighter');
    expect(html).toContain('Ball placement guide');
    expect(html).toContain('50% across · 78% down');
    expect(html).not.toContain('type="range"');
    expect(html).not.toContain('Apply alignment guide');
    expect(html).toContain('Sensor view');
    expect(html).toContain('View up');
    expect(html).toContain('View down');
    expect(html).toContain('-20 px');
    expect(html).toContain('320 × 200');
    expect(html).toContain('600 fps');
    expect(html).toContain('Armed');
  });

  it('labels a too-dark ball without disabling capture', () => {
    const html = renderToString(
      <CameraFeed
        captureSettings={{
          ...captureSettings,
          auto_exposure: {
            ...captureSettings.auto_exposure!,
            status: 'too_dark',
            analysis_eligible: false,
            frame_label: 'too dark: add light',
            message: 'Too dark: the ball is 6 DN above black at the blur ceiling (294 us) and the gain cap',
          },
        }}
        captureSettingsError={null}
        onUpdateCaptureSettings={vi.fn()}
      />
    );

    expect(html).toContain('Too dark');
    expect(html).toContain('too dark: add light');
    expect(html).toContain('Radar fallback active');
    expect(html).toContain('Preview and raw clips continue recording');
    expect(html).toContain('Armed');
  });

  it.each([
    ['too_bright', 'Too bright', 'too bright: shade the ball area (or an ND filter)'],
    ['holding_no_ball', 'Holding: no ball', null],
    ['seeded', 'Waiting for the ball', null],
  ] as const)('shows why the controls are what they are: %s', (status, label, frameLabel) => {
    const html = renderToString(
      <CameraFeed
        captureSettings={{
          ...captureSettings,
          auto_exposure: {
            ...captureSettings.auto_exposure!,
            status,
            frame_label: frameLabel,
            reading: status === 'holding_no_ball' ? null : captureSettings.auto_exposure!.reading,
          },
        }}
        captureSettingsError={null}
        onUpdateCaptureSettings={vi.fn()}
      />
    );

    expect(html).toContain(label);
    if (frameLabel) expect(html).toContain(frameLabel);
    if (status === 'holding_no_ball') expect(html).toContain('not seen');
  });
});
