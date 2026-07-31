import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { EnvironmentReading, LocationResult, WeatherSettings as Settings } from '../types/socket';
import type { UnitSystem } from '../utils/units';
import { WeatherSettingsView } from './WeatherSettings';

const reading = (overrides: Partial<EnvironmentReading> = {}): EnvironmentReading => ({
  air_density_kg_m3: 1.1316,
  source: 'open-meteo',
  temp_c: 36.1,
  pressure_hpa: 1010.2,
  humidity_pct: 25,
  age_s: 120,
  deviation_pct: -7.6,
  ...overrides,
});

const settings = (overrides: Partial<Settings> = {}): Settings => ({
  mode: 'auto',
  latitude: 38.58,
  longitude: -121.49,
  location_label: 'Sacramento, California',
  elevation_m: 9,
  location_consent: true,
  manual_temp_c: null,
  manual_pressure_hpa: null,
  manual_humidity_pct: null,
  manual_elevation_m: null,
  indoors: false,
  indoor_temp_c: null,
  indoor_humidity_pct: null,
  show_standard: true,
  standard_temp_c: 25,
  standard_elevation_m: 0,
  sensor_present: false,
  ...overrides,
});

function render(
  state: {
    reading?: EnvironmentReading | null;
    settings?: Settings | null;
    unitSystem?: UnitSystem;
    error?: string | null;
    locationResults?: LocationResult[];
  } = {}
) {
  return renderToString(
    <WeatherSettingsView
      reading={state.reading === undefined ? reading() : state.reading}
      settings={state.settings === undefined ? settings() : state.settings}
      refreshing={false}
      error={state.error ?? null}
      unitSystem={state.unitSystem ?? 'imperial'}
      locationResults={state.locationResults ?? []}
      onChange={() => {}}
      onRefresh={() => {}}
    />
  );
}

describe('WeatherSettingsView', () => {
  it('warns that carry is uncorrected when no source is configured', () => {
    // The default state is the one that silently costs several yards on a
    // warm day, so it has to be visible rather than merely absent.
    const html = render({ reading: reading({ source: 'default', deviation_pct: 0 }) });

    expect(html).toContain('assuming standard sea-level air');
    expect(html).toContain('weather-badge--warn');
  });

  it('does not warn when a real source is in use', () => {
    const html = render();

    expect(html).not.toContain('assuming standard sea-level air');
    expect(html).toContain('weather-badge--ok');
  });

  it('shows the sensor row when a BME280 is fitted', () => {
    const html = render({ settings: settings({ sensor_present: true }) });

    expect(html).toContain('BME280 connected');
  });

  it('hides the sensor row when none is fitted', () => {
    const html = render();

    expect(html).not.toContain('BME280 connected');
  });

  it('says the sensor is idle when something else is the active source', () => {
    // A fitted sensor sitting unused is a misconfiguration worth surfacing.
    const html = render({
      settings: settings({ sensor_present: true }),
      reading: reading({ source: 'manual' }),
    });

    expect(html).toContain('not in use with this source');
  });

  it('renders temperature in Fahrenheit for imperial users', () => {
    const html = render();

    // 36.1 C is 97.0 F. The digits must be converted, not relabelled.
    expect(html).toContain('97.0 °F');
  });

  it('renders temperature in Celsius for metric users', () => {
    const html = render({ unitSystem: 'metric' });

    expect(html).toContain('36.1 °C');
    expect(html).not.toContain('97.0 °F');
  });

  it('renders pressure in inHg for imperial users', () => {
    const html = render();

    // 1010.2 hPa is 29.83 inHg.
    expect(html).toContain('29.83 inHg');
  });

  it('renders pressure in hPa for metric users', () => {
    // Whole hPa: a tenth of a hPa is ~0.01% of density, below the noise floor
    // of anything downstream. inHg gets two decimals because its unit is
    // ~34x coarser.
    const html = render({ unitSystem: 'metric' });

    expect(html).toContain('1010 hPa');
    expect(html).not.toContain('inHg');
  });

  it('shows density and its deviation from sea level', () => {
    const html = render();

    expect(html).toContain('1.132 kg/m³');
    expect(html).toContain('-7.6%');
  });

  it('warns about an elevation too high to be a real course', () => {
    // The R10-in-E6 habit: entering 10,000 ft to make distances look right.
    const html = render({
      settings: settings({ mode: 'manual', manual_elevation_m: 3048 }),
    });

    expect(html).toContain('higher than almost any golf course');
    expect(html).toContain('spin source');
  });

  it('does not warn about a genuinely high but plausible elevation', () => {
    const html = render({
      settings: settings({ mode: 'manual', manual_elevation_m: 1609 }), // Denver
    });

    expect(html).not.toContain('higher than almost any golf course');
  });

  it('keeps manual entry and local weather on separate fields', () => {
    // Switching to manual, typing junk, and switching back must leave local
    // exactly as it was -- so the two elevations are different settings.
    const html = render({
      settings: settings({ mode: 'manual', elevation_m: 9, manual_elevation_m: 1609 }),
    });

    expect(html).toContain('cannot change local weather');
  });

  it('offers the venue elevation in local-weather mode, where it steers the fetch', () => {
    const html = render();

    expect(html).toContain('Your elevation');
    expect(html).toContain('Increase Your elevation');
  });

  it('gives the indoor override its own temperature', () => {
    const html = render({ settings: settings({ indoors: true, indoor_temp_c: 21 }) });

    expect(html).toContain('Indoor temperature');
    expect(html).toContain('>69.8<'); // 21 C as F, from indoor_temp_c not manual_temp_c
  });

  it('does not borrow the manual temperature for the indoor override', () => {
    // The leak that broke local weather: a value typed in manual mode became
    // the indoor temperature and silently rewrote what the fetch reported.
    const html = render({
      settings: settings({ indoors: true, indoor_temp_c: 21, manual_temp_c: -40 }),
    });

    expect(html).toContain('>69.8<');
    expect(html).not.toContain('-40');
  });

  it('requires consent before the location button can be used', () => {
    const html = render({
      settings: settings({ location_consent: false, location_label: null }),
    });

    expect(html).toContain('disabled');
    expect(html).toContain('Look up my location');
  });

  it('credits Open-Meteo and GeoNames where the fetch is offered', () => {
    const html = render();

    expect(html).toContain('Open-Meteo');
    expect(html).toContain('GeoNames');
  });

  it('warns that IP detection follows a VPN rather than the player', () => {
    // The failure this closes: on a VPN the detected location is the exit
    // node, and the weather that follows looks entirely plausible.
    const html = render();

    expect(html).toContain('VPN');
  });

  it('offers a location search alongside detection', () => {
    const html = render();

    expect(html).toContain('Search for a place or postal code');
  });

  it('lists search results with the region that tells them apart', () => {
    // The postal code 95814 matches Sacramento CA and Argenteuil FR, so a
    // bare city name is not enough to choose between them.
    const html = render({
      locationResults: [
        { label: 'Sacramento, California, US', latitude: 38.58, longitude: -121.49, elevation_m: 9 },
        { label: 'Sacramento, Kentucky, US', latitude: 37.41, longitude: -87.26, elevation_m: 150 },
      ],
    });

    expect(html).toContain('Sacramento, California, US');
    expect(html).toContain('Sacramento, Kentucky, US');
  });

  it('shows each result its elevation, in the user unit', () => {
    const html = render({
      locationResults: [{ label: 'Sacramento, California, US', latitude: 38.58, longitude: -121.49, elevation_m: 9 }],
    });

    expect(html).toContain('30 ft'); // 9 m
  });

  it('omits the elevation when the search did not know it', () => {
    const html = render({
      locationResults: [{ label: 'Somewhere', latitude: 1, longitude: 1, elevation_m: null }],
    });

    expect(html).toContain('Somewhere');
    expect(html).not.toContain('location-search__elevation');
  });

  it('offers manual entry fields in manual mode', () => {
    const html = render({ settings: settings({ mode: 'manual' }) });

    expect(html).toContain('Absolute station pressure');
  });

  it('gives every manual field a stepper, since the kiosk has no keyboard', () => {
    const html = render({ settings: settings({ mode: 'manual' }) });

    for (const field of ['Temperature', 'Pressure', 'Humidity', 'Elevation']) {
      expect(html).toContain(`Increase ${field}`);
      expect(html).toContain(`Decrease ${field}`);
    }
  });

  it('labels the steppers for screen readers rather than relying on the glyph', () => {
    const html = render({ settings: settings({ mode: 'manual' }) });

    expect(html).toContain('aria-label="Increase Humidity (%)"');
  });

  it('gives the reference temperature a stepper too', () => {
    const html = render();

    expect(html).toContain('Increase Reference temperature');
  });

  it('uses a tappable value rather than a text box that cannot be typed into', () => {
    // A <input type="number"> summons no keyboard on the kiosk, so the value
    // opens the in-app keypad instead.
    const html = render({ settings: settings({ mode: 'manual' }) });

    expect(html).toContain('weather-field__value');
    expect(html).not.toContain('type="number"');
  });

  it('shows manual values in display units on the field itself', () => {
    const html = render({
      settings: settings({ mode: 'manual', manual_temp_c: 20, manual_pressure_hpa: 1013.25 }),
    });

    expect(html).toContain('>68<'); // 20 C rendered as 68 F
  });

  it('marks an unset field rather than showing a bare zero', () => {
    const html = render({ settings: settings({ mode: 'manual', manual_pressure_hpa: null }) });

    expect(html).toContain('weather-field__value--empty');
    expect(html).toContain('not set');
  });

  it('explains that only temperature is replaced indoors', () => {
    const html = render({ settings: settings({ indoors: true }) });

    expect(html).toContain('only the temperature is replaced');
  });

  it('describes the standard figure as density-only, never wind', () => {
    // Claiming "normalized" would overstate what either radar can see.
    const html = render();

    expect(html).toContain('wind is never measured');
  });

  it('shows a loading state until the server has replied', () => {
    const html = render({ reading: null, settings: null });

    expect(html).toContain('Loading conditions');
  });

  it('surfaces a fetch error', () => {
    const html = render({ error: 'Could not reach the weather service.' });

    expect(html).toContain('Could not reach the weather service.');
  });
});
