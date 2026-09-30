/** The kiosk's ready light (P7-14), as its `ready_light` socket event carries it. */
export type ReadyLightState = 'red' | 'amber' | 'green' | 'off';

export interface ReadyLightSwingResult {
  kind: 'shot' | 'no_radar_shot' | 'not_a_shot' | 'not_counted';
  text: string;
  ball_speed_mph: number | null;
}

export interface ReadyLightSwing {
  id: number;
  /** Kiosk epoch seconds when the swing was picked up. */
  at: number;
  source: string;
  result: ReadyLightSwingResult | null;
}

export interface ReadyLight {
  schema_version: number;
  state: ReadyLightState;
  word: string;
  cause: string | null;
  time_left_s: number | null;
  causes: { cause: string; time_left_s: number | null }[];
  since: number | null;
  /** Kiosk epoch seconds when this light was computed. */
  checked_at: number;
  hold: string | null;
  swing: ReadyLightSwing | null;
}
