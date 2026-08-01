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
 * How often local weather may re-fetch itself. Mirrors
 * AUTO_REFRESH_CHOICES_MINUTES in environment/config.py, which is also the
 * server-side allowlist -- an interval outside this set is refused there.
 *
 * Nothing under 15 minutes: Open-Meteo's models update hourly, so a faster
 * poll re-fetches identical numbers.
 */
const AUTO_REFRESH_CHOICES = [0, 15, 30, 60] as const;

/**
 * The two reference conventions, mirroring ISA_REFERENCE and
 * TRACKMAN_REFERENCE in environment/config.py.
 *
 * ISA is dry by definition — at 50% humidity it would sit ~107 ft above zero
 * density altitude, which is small but defeats the reason for choosing it.
 * TrackMan does not publish its reference humidity; 50% is our assumption and
 * is worth under a yard either way.
 */
const REFERENCE_PRESETS = [
  { name: 'ISA', temp_c: 15, elevation_m: 0, humidity_pct: 0 },
  { name: 'TrackMan', temp_c: 25, elevation_m: 0, humidity_pct: 50 },
] as const;

/** Which preset the current reference matches, or 'Custom'. */
function referenceName(settings: Settings): string {
  const match = REFERENCE_PRESETS.find(
    (p) =>
      p.temp_c === settings.standard_temp_c &&
      p.elevation_m === settings.standard_elevation_m &&
      p.humidity_pct === settings.standard_humidity_pct
  );
  return match ? match.name : 'Custom';
}

/**
 * Every condition a reference fixes, spelled out.
 *
 * All three, not just temperature: tapping a preset also resets elevation and
 * humidity, so a label naming only the temperature hides the fact that a
 * custom elevation is about to be thrown away.
 */
function describeReference(temp_c: number, elevation_m: number, humidity_pct: number, unitSystem: UnitSystem): string {
  const temp = formatTemp(temp_c, unitSystem);
  const elevation =
    elevation_m === 0
      ? 'sea level'
      : `${Math.round(convertElevationFromMeters(elevation_m, unitSystem)).toLocaleString('en-US')} ${getElevationUnit(unitSystem)}`;
  // "dry" rather than "0% RH": it is what ISA actually specifies, and reads as
  // a deliberate choice instead of a value someone forgot to fill in.
  const humidity = humidity_pct === 0 ? 'dry' : `${Math.round(humidity_pct)}% RH`;
  return `${temp} · ${elevation} · ${humidity}`;
}

/**
 * How old a reading is, in words.
 *
 * Coarse on purpose. Nobody needs the second a fetch landed, and a figure that
 * ticks every second on a kiosk draws the eye away from the shot.
 */
function formatAge(seconds: number): string {
  if (seconds < 45) return 'just now';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(seconds / 3600);
  return hours === 1 ? 'an hour ago' : `${hours} h ago`;
}

function referenceHint(settings: Settings): string {
  switch (referenceName(settings)) {
    case 'ISA':
      return 'The standard in aviation and physics, and the air the rest of this screen measures against — so "plays like 0 ft" means today matches the reference.';
    case 'TrackMan':
      return "TrackMan's normalization reference, so this figure is comparable to a TrackMan session.";
    default:
      return 'Your own reference. Comparable to your other sessions, but not to anyone else.';
  }
}

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
        {/* Named for ISA specifically, not "standard". Two different
            references are in play -- this one (ISA, 15 °C, 1.225, what carry
            assumed before any of this existed) and the standard-conditions
            carry below (25 °C, TrackMan's reference). Calling both "standard"
            made them look like the same number. */}
        <Value
          label="vs ISA sea level"
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
          {/* Auto-refresh. Only ever fires once a fetch you asked for has
              succeeded, so it cannot start reaching out on a fresh install. */}
          <div className="weather-refresh">
            <span className="weather-refresh__label">Auto-refresh</span>
            <div className="weather-refresh__choices">
              {AUTO_REFRESH_CHOICES.map((minutes) => (
                <button
                  key={minutes}
                  type="button"
                  className={`weather-refresh__choice ${
                    draft.auto_refresh_minutes === minutes ? 'weather-refresh__choice--on' : ''
                  }`}
                  aria-pressed={draft.auto_refresh_minutes === minutes}
                  onClick={() => update({ auto_refresh_minutes: minutes })}
                >
                  {minutes === 0 ? 'Off' : `${minutes} min`}
                </button>
              ))}
            </div>
          </div>

          {/* Always shown, not only when stale. It is the only evidence that
              auto-refresh is doing anything: with a 15-minute interval the
              reading never gets old enough for a stale warning to appear, so
              a threshold-only message made the feature unobservable. */}
          {reading.age_s != null && (
            <p className={`weather-hint ${reading.age_s > 3600 ? 'weather-hint--stale' : ''}`}>
              {`Updated ${formatAge(reading.age_s)}${
                reading.age_s > 3600 ? ' — tap Refresh if conditions have changed' : ''
              }`}
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
            A second carry figure under the main one, re-flown in fixed reference air, so sessions on different days
            compare. Air density only; wind is never measured.
          </p>
          {draft.show_standard && (
            <>
              {/* Two conventions exist and they disagree, so both are offered
                  rather than one being imposed. ISA is the default because the
                  rest of the panel already measures against it -- with it
                  selected, "plays like 0 ft" means "today matches the
                  reference". */}
              <div className="weather-preset">
                <span className="weather-preset__label">
                  Reference: <strong>{referenceName(draft)}</strong> —{' '}
                  {describeReference(
                    draft.standard_temp_c,
                    draft.standard_elevation_m,
                    draft.standard_humidity_pct,
                    unitSystem
                  )}
                </span>
                <div className="weather-preset__choices">
                  {REFERENCE_PRESETS.map((preset) => (
                    <button
                      key={preset.name}
                      type="button"
                      className={`weather-preset__choice ${
                        referenceName(draft) === preset.name ? 'weather-preset__choice--on' : ''
                      }`}
                      aria-pressed={referenceName(draft) === preset.name}
                      onClick={() =>
                        update({
                          standard_temp_c: preset.temp_c,
                          standard_elevation_m: preset.elevation_m,
                          standard_humidity_pct: preset.humidity_pct,
                        })
                      }
                    >
                      <span className="weather-preset__name">{preset.name}</span>
                      <span className="weather-preset__conditions">
                        {describeReference(preset.temp_c, preset.elevation_m, preset.humidity_pct, unitSystem)}
                      </span>
                    </button>
                  ))}
                </div>
                <span className="weather-field__hint">{referenceHint(draft)}</span>
              </div>

              <UnitField
                label={`Reference temperature (${getTempUnit(unitSystem)})`}
                value={draft.standard_temp_c}
                fallback={25}
                toDisplay={(v) => convertTempFromC(v, unitSystem)}
                fromDisplay={(v) => convertTempToC(v, unitSystem)}
                onCommit={(v) => update({ standard_temp_c: v ?? 25 })}
              />
              {/* Was settable only by hand-editing weather.json. It matters
                  most to exactly the people this figure is for: someone in
                  Denver comparing sessions wants their own elevation as the
                  reference, or every session reads long against sea level. */}
              <UnitField
                label={`Reference elevation (${getElevationUnit(unitSystem)})`}
                hint="Sea level is the convention, and keeps the figure comparable between players. Set your own to compare only against yourself."
                value={draft.standard_elevation_m}
                digits={0}
                step={unitSystem === 'imperial' ? 25 : 10}
                toDisplay={(v) => convertElevationFromMeters(v, unitSystem)}
                fromDisplay={(v) => convertElevationToMeters(v, unitSystem)}
                onCommit={(v) => update({ standard_elevation_m: v ?? 0 })}
              />
            </>
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
