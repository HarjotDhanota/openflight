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
      onDetectLocation={() => {
        useEnvironmentStore.getState().setRefreshing(true);
        socketService.detectLocation();
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
  onDetectLocation?: () => void;
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
  onDetectLocation = () => {},
  onSearchLocations = () => {},
  onSelectLocation = () => {},
}: WeatherSettingsViewProps) {
  // Optimistic local copy, so a tap applies instantly instead of waiting for
  // the server to echo the change back. Re-synced during render rather than in
  // an effect: an effect would paint the stale draft for a frame first, which
  // is why react-hooks flags setState-in-effect.
  const [draft, setDraft] = useState<Settings | null>(settings);
  const [syncedSettings, setSyncedSettings] = useState<Settings | null>(settings);
  const [searching, setSearching] = useState(false);
  const [editingElevation, setEditingElevation] = useState(false);
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

  /**
   * Switch source, seeding manual entry from whatever is on screen.
   *
   * Seeding, not sharing: the numbers are copied once, when manual is empty,
   * and diverge from there. Local weather keeps its own values, so going back
   * still shows the fetch rather than anything typed here. Sharing the fields
   * is what corrupted the fetch before.
   *
   * Starting from today's real conditions is also the only sensible place to
   * start -- nobody wants to type four numbers from nothing to nudge one.
   */
  const selectMode = (mode: Settings['mode']) => {
    const seed =
      mode === 'manual' && draft.manual_temp_c == null && reading.temp_c != null
        ? {
            manual_temp_c: reading.temp_c,
            manual_pressure_hpa: reading.pressure_hpa,
            manual_humidity_pct: reading.humidity_pct,
          }
        : {};
    update({ mode, ...seed });
  };

  const uncorrected = isUncorrected(reading.source);
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

      {/* Density altitude leads: "plays like 2,700 ft" can be checked against
          experience, where a percentage cannot. The raw density and the
          deviation stay for anyone who wants them. */}
      <div className="weather-plays-like">
        <span className="weather-plays-like__label">Plays like</span>
        <span className="weather-plays-like__value">
          {reading.density_altitude_ft != null ? formatDensityAltitude(reading.density_altitude_ft, unitSystem) : '—'}
        </span>
        <span className="weather-plays-like__hint">{describeDensityAltitude(reading.density_altitude_ft)}</span>
      </div>

      <div className="weather-readout">
        <Value label="Density" value={`${reading.air_density_kg_m3.toFixed(3)} kg/m³`} />
        <Value
          label="Air vs standard"
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
            <input type="radio" name="weather-mode" checked={draft.mode === mode} onChange={() => selectMode(mode)} />
            <span>
              {mode === 'auto' && 'Local weather'}
              {mode === 'manual' && 'Enter manually'}
              {mode === 'off' && 'No correction'}
            </span>
          </label>
        ))}
      </fieldset>

      {draft.mode === 'auto' && (
        <section className="weather-location">
          {/* Consent gates both actions, so it comes first. It used to sit
              under the buttons it controls, which read as unrelated. */}
          <label className="weather-check">
            <input
              type="checkbox"
              checked={draft.location_consent}
              onChange={(e) => update({ location_consent: e.target.checked })}
            />
            <span>Let OpenFlight look up locations and fetch weather</span>
          </label>

          <div className="weather-location__place">
            <span className="weather-location__name">{draft.location_label ?? 'Location not set'}</span>
            {draft.location_label && (
              <button
                type="button"
                className="weather-location__elevation"
                aria-label={`Elevation (${getElevationUnit(unitSystem)})`}
                onClick={() => setEditingElevation(true)}
              >
                {draft.elevation_m == null
                  ? 'set elevation'
                  : `${Math.round(convertElevationFromMeters(draft.elevation_m, unitSystem))} ${getElevationUnit(unitSystem)}`}
              </button>
            )}
          </div>

          {/* The three things you can do with a location, side by side. */}
          <div className="weather-location__actions">
            <button type="button" onClick={() => setSearching(true)} disabled={!draft.location_consent}>
              Search
            </button>
            <button type="button" onClick={onDetectLocation} disabled={refreshing || !draft.location_consent}>
              {refreshing ? 'Fetching…' : 'Use my location'}
            </button>
            <button
              type="button"
              onClick={onRefresh}
              disabled={refreshing || !draft.location_consent || !draft.location_label}
            >
              Refresh
            </button>
          </div>

          <p className="weather-hint">
            <strong>Search</strong> for a place or postal code, or <strong>use my location</strong> to guess from your
            public IP — a VPN will place you at its exit node, so search if that looks wrong. <strong>Refresh</strong>{' '}
            re-fetches weather for the place already set. Nothing is fetched until you ask, and never during a shot.
            Weather and place data by Open-Meteo (CC BY 4.0) and GeoNames.
          </p>

          {editingElevation && (
            <NumericKeypad
              label={`Elevation (${getElevationUnit(unitSystem)})`}
              initial={
                draft.elevation_m == null
                  ? ''
                  : String(Math.round(convertElevationFromMeters(draft.elevation_m, unitSystem)))
              }
              onCommit={(text) => {
                setEditingElevation(false);
                const parsed = Number(text.trim());
                update({
                  elevation_m:
                    text.trim() === '' || !Number.isFinite(parsed)
                      ? null
                      : convertElevationToMeters(parsed, unitSystem),
                });
              }}
              onCancel={() => setEditingElevation(false)}
            />
          )}

          {searching && (
            <LocationSearch
              query={locationQuery}
              results={locationResults}
              unitSystem={unitSystem}
              onQueryChange={onSearchLocations}
              onSelect={onSelectLocation}
              onClose={() => setSearching(false)}
            />
          )}
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
          {/* Elevation only estimates pressure, so it is dead weight while a
              real pressure is entered. Hidden rather than merely captioned:
              a visible field that silently does nothing invites exactly the
              kind of experimenting that broke local weather. */}
          {draft.manual_pressure_hpa == null ? (
            <UnitField
              label={`Elevation (${getElevationUnit(unitSystem)})`}
              hint="Estimates pressure, since none is entered above. Separate from your location's elevation, so experimenting here cannot change local weather."
              value={draft.manual_elevation_m}
              digits={0}
              step={unitSystem === 'imperial' ? 25 : 10}
              toDisplay={(v) => convertElevationFromMeters(v, unitSystem)}
              fromDisplay={(v) => convertElevationToMeters(v, unitSystem)}
              onCommit={(v) => update({ manual_elevation_m: v })}
            />
          ) : (
            <p className="weather-hint">
              Elevation is not used while a pressure is entered — clear the pressure above to estimate from elevation
              instead.
            </p>
          )}
          {elevationSuspect && (
            <p className="weather-warning">
              That elevation is higher than almost any golf course. If you are raising it to make distances match what
              you expect, check the spin source on your shots instead — estimated spin, not air density, is the usual
              reason carry looks wrong.
            </p>
          )}
        </section>
      )}

      {/* Nothing to compare against with no correction applied: today's carry
          and the reference carry would be the same number. */}
      {draft.mode !== 'off' && (
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
      )}

      {error && <p className="weather-warning">{error}</p>}
    </div>
  );
}

/** "2,700 ft" / "820 m", rounded to something a person would say out loud. */
function formatDensityAltitude(feet: number, unitSystem: UnitSystem): string {
  if (unitSystem === 'metric') {
    const metres = Math.round((feet * 0.3048) / 10) * 10;
    return `${metres.toLocaleString('en-US')} m`;
  }
  return `${(Math.round(feet / 50) * 50).toLocaleString('en-US')} ft`;
}

/**
 * One line saying what the density altitude means for the shot.
 *
 * The number alone still asks the reader to know which direction is which;
 * this closes that gap without a paragraph.
 */
function describeDensityAltitude(feet: number | null | undefined): string {
  if (feet == null) return '';
  if (feet > 500) return 'thinner air than standard — the ball flies further';
  if (feet < -500) return 'denser air than standard — the ball flies shorter';
  return 'close to standard sea-level air';
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
export function LocationSearch({
  query,
  results,
  unitSystem,
  onQueryChange,
  onSelect,
  onClose,
}: {
  query: string;
  results: LocationResult[];
  unitSystem: UnitSystem;
  onQueryChange: (query: string) => void;
  onSelect: (result: LocationResult) => void;
  onClose: () => void;
}) {
  const [text, setText] = useState(query);

  const change = (next: string) => {
    setText(next);
    onQueryChange(next);
  };

  return (
    <TextKeyboard label="Search for a place or postal code" value={text} onChange={change} onDone={onClose}>
      {results.map((result) => (
        <button
          type="button"
          className="location-search__result"
          key={`${result.latitude},${result.longitude}`}
          onClick={() => {
            onSelect(result);
            onClose();
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
      {text.trim().length < 3 && <p className="location-search__empty">Type at least three characters.</p>}
    </TextKeyboard>
  );
}

/** "22.0" -> "22", but "29.92" stays put. Avoids a wall of trailing zeros. */
function trimZeros(value: string): string {
  return value.includes('.') ? value.replace(/\.?0+$/, '') : value;
}
