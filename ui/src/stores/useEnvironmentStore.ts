import { create } from 'zustand';
import type { EnvironmentReading, LocationResult, WeatherSettings } from '../types/socket';

interface EnvironmentState {
  /** Matches for the current search, and the query they answer. */
  locationQuery: string;
  locationResults: LocationResult[];
  setLocationResults: (query: string, results: LocationResult[]) => void;
  /** Resolved conditions currently being applied to carry, null until the server replies. */
  reading: EnvironmentReading | null;
  /** Persisted settings as the server has them. */
  settings: WeatherSettings | null;
  /** True while a location lookup or weather fetch is in flight. */
  refreshing: boolean;
  /** Last failure, cleared on the next successful refresh. */
  error: string | null;
  setReading: (reading: EnvironmentReading) => void;
  setSettings: (settings: WeatherSettings) => void;
  setRefreshing: (refreshing: boolean) => void;
  setError: (error: string | null) => void;
}

export const useEnvironmentStore = create<EnvironmentState>((set) => ({
  reading: null,
  settings: null,
  refreshing: false,
  error: null,
  locationQuery: '',
  locationResults: [],
  setLocationResults: (query, results) => set({ locationQuery: query, locationResults: results }),
  setReading: (reading) => set({ reading }),
  setSettings: (settings) => set({ settings }),
  setRefreshing: (refreshing) => set({ refreshing }),
  setError: (error) => set({ error, refreshing: false }),
}));

/** Human label for each density source, used on the badge. */
export const SOURCE_LABELS: Record<string, string> = {
  manual: 'Manual',
  'open-meteo': 'Weather',
  elevation: 'Elevation',
  default: 'Not set',
};

/**
 * Whether a source should be visually flagged.
 *
 * `default` means carry is assuming ISA sea level, which on a hot afternoon is
 * already several yards out. The user should know that is happening rather
 * than trusting a silently uncorrected number.
 */
export function isUncorrected(source: string | undefined): boolean {
  return !source || source === 'default';
}
