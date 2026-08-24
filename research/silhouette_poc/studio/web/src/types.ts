export type Point = [number, number];

export interface CriteriaRow {
  club: string;
  candidate: string;
  role: 'primary' | 'comparison_only';
  n: number;
  solve_rate: number;
  median_mm: number | null;
  p90_mm: number | null;
  signed_horizontal_median_mm: number | null;
  signed_horizontal_p90_mm?: number | null;
  signed_vertical_median_mm: number | null;
  signed_vertical_p90_mm?: number | null;
  iou_median: number | null;
  rejections: Record<string, number>;
  passes: boolean;
}

export interface Frame {
  index: number;
  time_ms: number;
  exposure_start_ms: number;
  exposure_end_ms: number;
  image: string;
  overlays: {
    silhouette: Point[];
    truth_mask: Point[];
    template: Point[];
    track: Point[];
    extrapolation: Point[];
    face_center: Point;
    radar_ray: Point[];
  };
}

export interface Shot {
  seed: number;
  status: string;
  ok: boolean;
  impact_error_mm: number | null;
  config_hash: string;
  frames: Frame[];
  timeline: {
    ops_impact_ms: number;
    trigger_ms: number;
    radar_sample_ms: number[];
    used_frame_indices: number[];
  };
  clubface: {
    limits_mm: { u: number; v: number };
    impact: { estimated: Point | null; truth: Point };
  };
  diagnostics: {
    rejection_reason: string | null;
    quality: Record<string, number | boolean | string | null>;
    temporal: Record<string, unknown>;
    radar: Record<string, unknown>;
    objective: Array<{
      frame_index: number;
      template_fit_iou: number;
      best_second_margin: number;
      condition: number;
    }>;
  };
}

export interface Session {
  source: string;
  session_id: string;
  landing: {
    ambient_verdict: string;
    verdict_reason: string;
    evaluation_hash: string;
    criteria: CriteriaRow[];
    club_speed_sweep: Array<Record<string, number | string | boolean | null>>;
    strobe_policy: string;
  };
  controls: Controls;
  summary: {
    n_attempted: number;
    n_ok: number;
    solve_rate: number;
    median_mm: number | null;
    p90_mm: number | null;
    rejections: Record<string, number>;
  };
  shots: Shot[];
}

export interface Controls {
  club: string;
  n: number;
  candidate: string;
  template_variation: string | number;
  radar_residual_mm: number;
  sync_mode: string;
  club_speed_mph: number | null;
  root_seed: number;
}

export interface StudioOptions {
  clubs: string[];
  n: number[];
  candidates: Array<{ id: string; label: string; role: string }>;
  template_variations: Array<string | number>;
  radar_residual_mm: number[];
  sync_modes: string[];
  driver_speed_mph: number[];
}
