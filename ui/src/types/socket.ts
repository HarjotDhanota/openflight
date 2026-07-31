export interface DebugReading {
  speed: number;
  direction: 'inbound' | 'outbound' | 'unknown';
  magnitude: number | null;
  timestamp: string;
}

export type SimState = 'connected' | 'connecting' | 'reconnecting' | 'disabled' | 'stopped' | 'error';

export interface SimStatus {
  target: string;
  state: SimState;
  host?: string;
  port?: number;
  message?: string;
  attempt?: number;
  next_retry_in_s?: number;
}

export interface SimShotInfo {
  target: string;
  shot_number: number;
  fields: string[];
  values: Record<string, number | null>;
  provenance: Record<string, 'measured' | 'estimated'>;
}

export interface RadarConfig {
  min_speed: number;
  max_speed: number;
  min_magnitude: number;
  transmit_power: number;
}

export interface DebugShotLog {
  type: 'shot';
  timestamp: string;
  radar: {
    ball_speed_mph: number;
    club_speed_mph: number | null;
    smash_factor: number | null;
    peak_magnitude: number;
  };
  camera: {
    launch_angle_vertical: number;
    launch_angle_horizontal: number;
    launch_angle_confidence: number;
    positions_tracked: number;
    launch_detected: boolean;
  } | null;
  club: string;
}

/** Resolved air-density conditions currently applied to carry. */
export interface EnvironmentReading {
  air_density_kg_m3: number;
  /** 'bme280' | 'manual' | 'open-meteo' | 'elevation' | 'default' */
  source: string;
  temp_c: number | null;
  pressure_hpa: number | null;
  humidity_pct: number | null;
  /** Age of the underlying data in seconds; null for manual entry. */
  age_s: number | null;
  /** Percent difference from ISA sea level. Negative means thinner air, longer carry. */
  deviation_pct: number;
}

/** Persisted weather settings, edited from the settings screen. */
export interface WeatherSettings {
  mode: 'auto' | 'manual' | 'off';
  latitude: number | null;
  longitude: number | null;
  location_label: string | null;
  elevation_m: number | null;
  location_consent: boolean;
  /** Manual entry. Never read in auto mode — it is a separate set-up. */
  manual_temp_c: number | null;
  manual_pressure_hpa: number | null;
  manual_humidity_pct: number | null;
  manual_elevation_m: number | null;
  indoors: boolean;
  /** The indoors override belongs to local weather, not to manual entry. */
  indoor_temp_c: number | null;
  indoor_humidity_pct: number | null;
  /** Second carry figure at fixed reference conditions, shown under the main one. */
  show_standard: boolean;
  standard_temp_c: number;
  standard_elevation_m: number;
  /** True when a sensor is fitted; the UI hides manual entry as the primary path. */
  sensor_present: boolean;
}
