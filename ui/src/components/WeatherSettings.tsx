import { useState } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { useEnvironmentStore, SOURCE_LABELS, isUncorrected } from '../stores/useEnvironmentStore';
import { useUnitPreference } from '../state/useUnitPreference';
import { socketService } from '../services/socketService';
import {
  convertElevationFromMeters,
  convertElevationToMeters,
  convertPressureFromHpa,
  convertPressureToHpa,
  convertTempFromC,
  convertTempToC,
  getElevationUnit,
  getPressureDigits,
  getPressureUnit,
  getTempUnit,
  stepInDisplayUnits,
  type UnitSystem,
} from '../utils/units';
import type { EnvironmentReading, LocationResult, WeatherSettings as Settings } from '../types/socket';
import { NumericKeypad } from './NumericKeypad';
import { TextKeyboard } from './TextKeyboard';
import './WeatherSettings.css';

/**
 * Air density settings.
 *
 * Carry scales with air density, and before this existed every number assumed
 * ISA sea level -- 15 C, 1013 hPa, dry. A hot afternoon at sea level is
 * already ~7% off that (about 5 yd on a driver) and Denver is ~20%.
 *
 * Values are held in SI on the wire (C, hPa, m) and converted only for
 * display and entry, following the app-wide unit preference. Deliberately NOT
 * a tuning panel: there is no carry-calibration multiplier, because the usual
 * reason carry looks wrong is estimated spin, not air.
 */
export function WeatherSettings() {
  const { reading, settings, refreshing, error, locationQuery, locationResults } = useEnvironmentStore(
    useShallow((s) => ({
      reading: s.reading,
      settings: s.settings,
      refreshing: s.refreshing,
      error: s.error,
      locationQuery: s.locationQuery,
      locationResults: s.locationResults,
    }))
  );
  const { unitSystem } = useUnitPreference();

  return (
    <WeatherSettingsView
      reading={reading}
      settings={settings}
      refreshing={refreshing}
      error={error}
      unitSystem={unitSystem}
      locationQuery={locationQuery}
      locationResults={locationResults}
      onChange={(next) => socketService.setWeatherSettings(next)}
      onRefresh={() => {
        useEnvironmentStore.getState().setRefreshing(true);
        socketService.refreshWeather();
      }}
      onSearchLocations={(query) => socketService.searchLocations(query)}
      onSelectLocation={(result) => {
        useEnvironmentStore.getState().setRefreshing(true);
        socketService.selectLocation(result);
      }}
    />
  );
}

interface WeatherSettingsViewProps {
  reading: EnvironmentReading | null;
  settings: Settings | null;
  refreshing: boolean;
  error: string | null;
  unitSystem: UnitSystem;
  locationQuery?: string;
  locationResults?: LocationResult[];
  onChange: (settings: Settings) => void;
  onRefresh: () => void;
  onSearchLocations?: (query: string) => void;
  onSelectLocation?: (result: LocationResult) => void;
}

/**
 * The screen itself, with every input passed in.
 *
 * Split from the store-connected wrapper above so it can be rendered in a
 * test: this repo's component tests use renderToString, and React resolves
 * stores through getServerSnapshot there, which returns their initial state
 * regardless of what the test wrote.
 */
export function WeatherSettingsView({
  reading,
  settings,
  refreshing,
  error,
  unitSystem,
  locationQuery = '',
  locationResults = [],
  onChange,
  onRefresh,
  onSearchLocations = () => {},
  onSelectLocation = () => {},
}: WeatherSettingsViewProps) {
  // Optimistic local copy, so a tap applies instantly instead of waiting for
  // the server to echo the change back. Re-synced during render rather than in
  // an effect: an effect would paint the stale draft for a frame first, which
  // is why react-hooks flags setState-in-effect.
  const [draft, setDraft] = useState<Settings | null>(settings);
  const [syncedSettings, setSyncedSettings] = useState<Settings | null>(settings);
  if (syncedSettings !== settings) {
    setSyncedSettings(settings);
    setDraft(settings);
  }

  if (!draft || !reading) {
    return <div className="weather-settings weather-settings--loading">Loading conditions…</div>;
  }

  const update = (patch: Partial<Settings>) => {
    const next = { ...draft, ...patch };
    setDraft(next);
    onChange(next);
  };

  const uncorrected = isUncorrected(reading.source);
  const sensorInUse = reading.source === 'bme280';
  // Above this an entered elevation is far more likely to be a fudge than a
  // fact -- R10 users set 10,000 ft to make numbers look right, which silently
  // corrupts every carry figure.
  const elevationSuspect = Math.max(draft.elevation_m ?? 0, draft.manual_elevation_m ?? 0) > 2500;

  return (
    <div className="weather-settings">
      <header className="weather-settings__header">
        <h2>Conditions</h2>
        <span
          className={`weather-badge weather-badge--${uncorrected ? 'warn' : 'ok'}`}
          title={uncorrected ? 'Carry is assuming standard sea-level air' : undefined}
        >
          {SOURCE_LABELS[reading.source] ?? reading.source}
        </span>
      </header>

      {draft.sensor_present && (
        <div className={`weather-sensor ${sensorInUse ? '' : 'weather-sensor--idle'}`}>
          <span className="weather-sensor__dot" aria-hidden="true" />
          <span>
            {sensorInUse
              ? 'BME280 connected — measuring the air at the unit'
              : 'BME280 connected but not in use with this source'}
          </span>
        </div>
      )}

      <div className="weather-readout">
        <Value label="Density" value={`${reading.air_density_kg_m3.toFixed(3)} kg/m³`} />
        <Value
          label="vs sea level"
          value={`${reading.deviation_pct > 0 ? '+' : ''}${reading.deviation_pct.toFixed(1)}%`}
        />
        <Value label="Temp" value={formatTemp(reading.temp_c, unitSystem)} />
        <Value label="Pressure" value={formatPressure(reading.pressure_hpa, unitSystem)} />
        <Value label="Humidity" value={reading.humidity_pct != null ? `${reading.humidity_pct}%` : '—'} />
      </div>

      {uncorrected && (
        <p className="weather-warning">
          Carry is assuming standard sea-level air. On a warm day that reads several yards short.
        </p>
      )}

      <fieldset className="weather-modes">
        <legend>Source</legend>
        {(['auto', 'manual', 'off'] as const).map((mode) => (
          <label key={mode}>
            <input type="radio" name="weather-mode" checked={draft.mode === mode} onChange={() => update({ mode })} />
            <span>
              {mode === 'auto' && (draft.sensor_present ? 'Sensor' : 'Local weather')}
              {mode === 'manual' && 'Enter manually'}
              {mode === 'off' && 'No correction'}
            </span>
          </label>
        ))}
      </fieldset>

      {draft.mode === 'auto' && !draft.sensor_present && (
        <section className="weather-location">
          <div className="weather-location__row">
            <span>{draft.location_label ?? 'Location not set'}</span>
            <button type="button" onClick={onRefresh} disabled={refreshing || !draft.location_consent}>
              {refreshing ? 'Fetching…' : draft.location_label ? 'Refresh' : 'Detect location'}
            </button>
          </div>
          <label className="weather-check">
            <input
              type="checkbox"
              checked={draft.location_consent}
              onChange={(e) => update({ location_consent: e.target.checked })}
            />
            <span>Look up my location and fetch local weather</span>
          </label>
          <p className="weather-hint">
            Detection guesses from your public IP, so a VPN will place you at its exit node — search instead if the
            result looks wrong. Nothing is fetched until you ask, and never during a shot. Weather and place data by
            Open-Meteo (CC BY 4.0) and GeoNames.
          </p>

          <LocationSearch
            query={locationQuery}
            results={locationResults}
            unitSystem={unitSystem}
            onQueryChange={onSearchLocations}
            onSelect={onSelectLocation}
          />
          {reading.age_s != null && reading.age_s > 3600 && (
            <p className="weather-hint">
              Last updated {Math.round(reading.age_s / 3600)} h ago. Tap refresh if conditions have changed.
            </p>
          )}
          <label className="weather-check">
            <input type="checkbox" checked={draft.indoors} onChange={(e) => update({ indoors: e.target.checked })} />
            <span>Playing indoors</span>
          </label>
          {draft.indoors && (
            <>
              <p className="weather-hint">Outdoor pressure is correct indoors, so only the temperature is replaced.</p>
              <UnitField
                label={`Indoor temperature (${getTempUnit(unitSystem)})`}
                value={draft.indoor_temp_c}
                fallback={20}
                toDisplay={(v) => convertTempFromC(v, unitSystem)}
                fromDisplay={(v) => convertTempToC(v, unitSystem)}
                onCommit={(v) => update({ indoor_temp_c: v })}
              />
            </>
          )}
          <UnitField
            label={`Your elevation (${getElevationUnit(unitSystem)})`}
            hint="Open-Meteo reports pressure at its own terrain height unless told yours. 100 m out is about a yard on a driver."
            value={draft.elevation_m}
            digits={0}
            step={unitSystem === 'imperial' ? 25 : 10}
            toDisplay={(v) => convertElevationFromMeters(v, unitSystem)}
            fromDisplay={(v) => convertElevationToMeters(v, unitSystem)}
            onCommit={(v) => update({ elevation_m: v })}
          />
        </section>
      )}

      {draft.mode === 'manual' && (
        <section className="weather-manual">
          <UnitField
            label={`Temperature (${getTempUnit(unitSystem)})`}
            value={draft.manual_temp_c}
            fallback={20}
            toDisplay={(v) => convertTempFromC(v, unitSystem)}
            fromDisplay={(v) => convertTempToC(v, unitSystem)}
            onCommit={(v) => update({ manual_temp_c: v })}
          />
          <UnitField
            label={`Pressure (${getPressureUnit(unitSystem)})`}
            hint="Absolute station pressure, not the sea-level value a weather app shows"
            value={draft.manual_pressure_hpa}
            digits={getPressureDigits(unitSystem)}
            // 1 hPa / 0.01 inHg is ~0.1% of density -- finer than any barometer
            // you would read off, so a step never overshoots.
            step={unitSystem === 'imperial' ? 0.01 : 1}
            fallback={1013.25}
            toDisplay={(v) => convertPressureFromHpa(v, unitSystem)}
            fromDisplay={(v) => convertPressureToHpa(v, unitSystem)}
            onCommit={(v) => update({ manual_pressure_hpa: v })}
          />
          <UnitField
            label="Humidity (%)"
            hint="Smallest term — leave blank for 50%"
            value={draft.manual_humidity_pct}
            digits={0}
            step={5}
            fallback={50}
            toDisplay={(v) => v}
            fromDisplay={(v) => v}
            onCommit={(v) => update({ manual_humidity_pct: v })}
          />
          <UnitField
            label={`Elevation (${getElevationUnit(unitSystem)})`}
            hint="Used only when no pressure is entered above. Separate from your location's elevation, so experimenting here cannot change local weather."
            value={draft.manual_elevation_m}
            digits={0}
            step={unitSystem === 'imperial' ? 25 : 10}
            toDisplay={(v) => convertElevationFromMeters(v, unitSystem)}
            fromDisplay={(v) => convertElevationToMeters(v, unitSystem)}
            onCommit={(v) => update({ manual_elevation_m: v })}
          />
          {elevationSuspect && (
            <p className="weather-warning">
              That elevation is higher than almost any golf course. If you are raising it to make distances match what
              you expect, check the spin source on your shots instead — estimated spin, not air density, is the usual
              reason carry looks wrong.
            </p>
          )}
        </section>
      )}

      <section className="weather-standard">
        <label className="weather-check">
          <input
            type="checkbox"
            checked={draft.show_standard}
            onChange={(e) => update({ show_standard: e.target.checked })}
          />
          <span>Show standard-conditions carry</span>
        </label>
        <p className="weather-hint">
          A second carry figure under the main one, adjusted to {formatTemp(draft.standard_temp_c, unitSystem)} at sea
          level, so sessions on different days compare. Adjusts for air density only — wind is never measured.
        </p>
        {draft.show_standard && (
          <UnitField
            label={`Reference temperature (${getTempUnit(unitSystem)})`}
            value={draft.standard_temp_c}
            fallback={25}
            toDisplay={(v) => convertTempFromC(v, unitSystem)}
            fromDisplay={(v) => convertTempToC(v, unitSystem)}
            onCommit={(v) => update({ standard_temp_c: v ?? 25 })}
          />
        )}
      </section>

      {error && <p className="weather-warning">{error}</p>}
    </div>
  );
}

function formatTemp(tempC: number | null, unitSystem: UnitSystem): string {
  if (tempC == null) return '—';
  return `${convertTempFromC(tempC, unitSystem).toFixed(1)} ${getTempUnit(unitSystem)}`;
}

function formatPressure(pressureHpa: number | null, unitSystem: UnitSystem): string {
  if (pressureHpa == null) return '—';
  const value = convertPressureFromHpa(pressureHpa, unitSystem);
  return `${value.toFixed(getPressureDigits(unitSystem))} ${getPressureUnit(unitSystem)}`;
}

function Value({ label, value }: { label: string; value: string }) {
  return (
    <div className="weather-value">
      <span className="weather-value__label">{label}</span>
      <span className="weather-value__number">{value}</span>
    </div>
  );
}

/**
 * Numeric input that displays in the user's units and stores in SI.
 *
 * Commits only on blur, so a half-typed value never applies, and re-syncs
 * whenever the unit system changes so the box shows the same physical value
 * in the new units rather than the same digits.
 */
function UnitField({
  label,
  hint,
  value,
  digits = 1,
  step = 1,
  fallback = 0,
  toDisplay,
  fromDisplay,
  onCommit,
}: {
  label: string;
  hint?: string;
  value: number | null;
  digits?: number;
  /** Step size in DISPLAY units, so one press moves one degree/hPa the user sees. */
  step?: number;
  /** Where stepping starts when the field is empty, in SI. */
  fallback?: number;
  toDisplay: (value: number) => number;
  fromDisplay: (value: number) => number;
  onCommit: (value: number | null) => void;
}) {
  const format = (v: number | null) => (v == null ? '' : trimZeros(toDisplay(v).toFixed(digits)));
  const [editing, setEditing] = useState(false);
  const shown = format(value);

  // Steppers nudge; the keypad enters a number outright. Both exist because
  // the panel is touch-only and Raspberry Pi OS Chromium ships without an
  // on-screen keyboard -- a plain <input type="number"> summons nothing there,
  // so it was decorative on the hardware this is built for. Stepping alone is
  // far too slow to dial in a pressure.
  const stepBy = (delta: number) =>
    onCommit(stepInDisplayUnits(value, fallback, delta, digits, toDisplay, fromDisplay));

  const commitText = (text: string) => {
    setEditing(false);
    const trimmed = text.trim();
    if (trimmed === '' || trimmed === '-') {
      onCommit(null);
      return;
    }
    const parsed = Number(trimmed);
    if (Number.isFinite(parsed)) onCommit(fromDisplay(parsed));
  };

  return (
    <div className="weather-field">
      <span className="weather-field__label">{label}</span>
      {hint && <span className="weather-field__hint">{hint}</span>}
      <div className="weather-field__control">
        <button
          type="button"
          className="weather-field__step"
          aria-label={`Decrease ${label}`}
          onClick={() => stepBy(-step)}
        >
          −
        </button>
        <button
          type="button"
          className={`weather-field__value ${shown === '' ? 'weather-field__value--empty' : ''}`}
          aria-label={label}
          onClick={() => setEditing(true)}
        >
          {shown === '' ? 'not set' : shown}
        </button>
        <button
          type="button"
          className="weather-field__step"
          aria-label={`Increase ${label}`}
          onClick={() => stepBy(step)}
        >
          +
        </button>
      </div>
      {editing && (
        <NumericKeypad label={label} initial={shown} onCommit={commitText} onCancel={() => setEditing(false)} />
      )}
    </div>
  );
}

/**
 * Search for a location by name or postal code.
 *
 * The primary way a location gets set. IP detection stays as a suggestion,
 * but it cannot be the only path: behind a VPN it returns the exit node, and
 * the weather that follows is wrong in a way that looks entirely plausible.
 *
 * The on-screen keyboard is only mounted once the field is open, so it does
 * not eat the panel when nobody is searching.
 */
function LocationSearch({
  query,
  results,
  unitSystem,
  onQueryChange,
  onSelect,
}: {
  query: string;
  results: LocationResult[];
  unitSystem: UnitSystem;
  onQueryChange: (query: string) => void;
  onSelect: (result: LocationResult) => void;
}) {
  const [opened, setOpened] = useState(false);
  const [text, setText] = useState(query);

  // Derived rather than stored: if the server has sent matches, the list is
  // shown whether or not this client is the one that asked. Closing is the
  // only thing that needs remembering.
  const [dismissed, setDismissed] = useState(false);
  const open = (opened || results.length > 0) && !dismissed;

  const change = (next: string) => {
    setText(next);
    setDismissed(false);
    onQueryChange(next);
  };

  const setOpen = (next: boolean) => {
    setOpened(next);
    setDismissed(!next);
  };

  if (!open) {
    return (
      <button type="button" className="weather-field__value" onClick={() => setOpen(true)}>
        Search for a place or postal code
      </button>
    );
  }

  return (
    <div className="location-search">
      <button type="button" className="weather-field__value" aria-label="Location search">
        {text === '' ? 'Type a place or postal code' : text}
      </button>

      <div className="location-search__results">
        {results.map((result) => (
          <button
            type="button"
            className="location-search__result"
            key={`${result.latitude},${result.longitude}`}
            onClick={() => {
              onSelect(result);
              setOpen(false);
            }}
          >
            <span>{result.label}</span>
            {result.elevation_m != null && (
              <span className="location-search__elevation">
                {`${Math.round(convertElevationFromMeters(result.elevation_m, unitSystem))} ${getElevationUnit(unitSystem)}`}
              </span>
            )}
          </button>
        ))}
        {text.trim().length >= 3 && results.length === 0 && (
          <p className="location-search__empty">No matches. Try a postal code.</p>
        )}
      </div>

      <TextKeyboard value={text} onChange={change} onDone={() => setOpen(false)} />
    </div>
  );
}

/** "22.0" -> "22", but "29.92" stays put. Avoids a wall of trailing zeros. */
function trimZeros(value: string): string {
  return value.includes('.') ? value.replace(/\.?0+$/, '') : value;
}
