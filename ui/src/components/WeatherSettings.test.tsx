import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { EnvironmentReading, LocationResult, WeatherSettings as Settings } from '../types/socket';
import type { UnitSystem } from '../utils/units';
import { LocationSearch, WeatherSettingsView } from './WeatherSettings';

const reading = (overrides: Partial<EnvironmentReading> = {}): EnvironmentReading => ({
  air_density_kg_m3: 1.1316,
  source: 'open-meteo',
  temp_c: 36.1,
  pressure_hpa: 1010.2,
  humidity_pct: 25,
  age_s: 120,
  deviation_pct: -7.6,
  density_altitude_ft: 2685,
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
  standard_temp_c: 15,
  standard_elevation_m: 0,
  standard_humidity_pct: 0,
  auto_refresh_minutes: 30,
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

  it('always shows how old the reading is, not only once it is stale', () => {
    // The only evidence auto-refresh is working: on a 15-minute interval the
    // reading never gets old enough for a stale-only message to appear.
    const html = render({ reading: reading({ age_s: 300 }) });

    expect(html).toContain('Updated 5 min ago');
  });

  it('says "just now" straight after a fetch', () => {
    const html = render({ reading: reading({ age_s: 12 }) });

    expect(html).toContain('just now');
  });

  it('switches to hours once minutes stop being useful', () => {
    expect(render({ reading: reading({ age_s: 3600 }) })).toContain('an hour ago');
    expect(render({ reading: reading({ age_s: 4 * 3600 }) })).toContain('4 h ago');
  });

  it('adds the refresh nudge only once the reading is genuinely stale', () => {
    expect(render({ reading: reading({ age_s: 600 }) })).not.toContain('tap Refresh');
    expect(render({ reading: reading({ age_s: 5 * 3600 }) })).toContain('tap Refresh');
  });

  it('shows no age at all for manual entry, which has none', () => {
    const html = render({ reading: reading({ age_s: null }) });

    expect(html).not.toContain('Updated');
  });

  it('offers the auto-refresh intervals, marking the active one', () => {
    const html = render({ settings: settings({ auto_refresh_minutes: 30 }) });

    expect(html).toContain('Auto-refresh');
    expect(html).toContain('Off');
    expect(html).toContain('15 min');
    expect(html).toContain('30 min');
    expect(html).toContain('60 min');
    expect(html).toContain('aria-pressed="true"');
  });

  it('offers nothing faster than 15 minutes', () => {
    // Open-Meteo's models update hourly; a faster poll re-fetches identical
    // numbers and is just traffic on someone else's range Wi-Fi.
    const html = render();

    expect(html).not.toContain('>5 min<');
    expect(html).not.toContain('>10 min<');
  });

  it('does not advertise a sensor, since no driver exists yet', () => {
    // The provider hook, the precedence entry and this row were all removed
    // rather than shipped unreachable. They return with the BME280 driver.
    const html = render();

    expect(html).not.toContain('BME280');
    expect(html).not.toContain('Sensor');
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

  it('shows the venue elevation on the location line, where it steers the fetch', () => {
    // It is auto-filled by the search, so it reads as information about the
    // place rather than a standing input asking to be filled in.
    const html = render();

    expect(html).toContain('weather-location__elevation');
    expect(html).toContain('aria-label="Elevation (ft)"');
  });

  it('offers to set the elevation when the search did not supply one', () => {
    const html = render({ settings: settings({ elevation_m: null }) });

    expect(html).toContain('set elevation');
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
    expect(html).toContain('Let OpenFlight look up');
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

  it('offers search, re-detect and refresh as three distinct actions', () => {
    // Refresh re-fetches the place already set; "use my location" replaces it.
    // With only one button there was no way back to where you actually are.
    const html = render();

    expect(html).toContain('>Search<');
    expect(html).toContain('Use my location');
    expect(html).toContain('>Refresh<');
  });

  it('puts consent above the actions it gates', () => {
    const html = render();

    expect(html.indexOf('Let OpenFlight look up')).toBeLessThan(html.indexOf('weather-location__actions'));
  });

  it('shows the elevation as a fact about the place, not another empty box', () => {
    const html = render();

    expect(html).toContain('weather-location__elevation');
    expect(html).toContain('>30 ft<'); // 9 m, filled in by the search
  });

  it('leads with density altitude rather than a percentage', () => {
    const html = render();

    expect(html).toContain('Plays like');
    expect(html).toContain('2,700 ft');
    expect(html).toContain('the ball flies further');
  });

  it('still shows the raw density and its deviation underneath', () => {
    const html = render();

    expect(html).toContain('1.132 kg/m³');
    expect(html).toContain('vs ISA sea level');
  });

  it('names the two references distinctly', () => {
    // Two different references are in play: ISA (15 C, 1.225) for the
    // deviation and density altitude, and 25 C for the reference carry.
    // Calling both "standard" made them read as the same number.
    const html = render();

    expect(html).toContain('vs ISA sea level');
    expect(html).not.toContain('Air vs standard');
  });

  it('offers the reference elevation, not just the temperature', () => {
    // It was settable only by hand-editing weather.json, which matters most
    // to the people this figure is for -- someone comparing sessions at
    // altitude wants their own elevation as the reference.
    const html = render();

    expect(html).toContain('Reference elevation');
    expect(html).toContain('Increase Reference elevation');
  });

  it('offers both reference conventions rather than imposing one', () => {
    // Naming both in the UI is also the shortest answer to "why 77 and not
    // 59" — the two conventions genuinely disagree.
    const html = render();

    expect(html).toContain('ISA');
    expect(html).toContain('TrackMan');
  });

  it('spells out every condition a preset fixes, not just temperature', () => {
    // Tapping a preset resets elevation and humidity too, so a label naming
    // only the temperature would hide a custom elevation being discarded.
    const html = render();

    expect(html).toContain('59.0 °F · sea level · dry'); // ISA
    expect(html).toContain('77.0 °F · sea level · 50% RH'); // TrackMan
  });

  it('says "dry" rather than 0% for ISA, which is how ISA is defined', () => {
    const html = render();

    expect(html).toContain('· dry');
    expect(html).not.toContain('· 0% RH');
  });

  it('summarises the reference actually in use, including a custom one', () => {
    const html = render({
      settings: settings({
        standard_temp_c: 18,
        standard_elevation_m: 1609,
        standard_humidity_pct: 30,
      }),
    });

    expect(html).toContain('64.4 °F · 5,279 ft · 30% RH');
  });

  it('describes the reference in the user unit system', () => {
    const html = render({ unitSystem: 'metric' });

    expect(html).toContain('15.0 °C · sea level · dry');
  });

  it('defaults to ISA, so the reference agrees with the rest of the screen', () => {
    // With ISA selected, "plays like 0 ft" means today matches the reference.
    const html = render();

    expect(html).toContain('Reference: <strong>ISA</strong>');
    expect(html).toContain('plays like 0 ft');
  });

  it('names the TrackMan reference when that one is selected', () => {
    const html = render({
      settings: settings({
        standard_temp_c: 25,
        standard_elevation_m: 0,
        standard_humidity_pct: 50,
      }),
    });

    expect(html).toContain('Reference: <strong>TrackMan</strong>');
    expect(html).toContain('comparable to a TrackMan session');
  });

  it('calls a hand-tuned reference Custom rather than claiming a convention', () => {
    const html = render({ settings: settings({ standard_temp_c: 18 }) });

    expect(html).toContain('Reference: <strong>Custom</strong>');
    expect(html).toContain('not to anyone else');
  });

  it('hides the standard-carry option when no correction is applied', () => {
    // With nothing corrected, today's carry and the reference carry are the
    // same number, so the checkbox offers a comparison that cannot exist.
    const html = render({ settings: settings({ mode: 'off' }) });

    expect(html).not.toContain('Show standard-conditions carry');
  });

  it('offers standard carry in every mode that does correct', () => {
    expect(render()).toContain('Show standard-conditions carry');
    expect(render({ settings: settings({ mode: 'manual' }) })).toContain('Show standard-conditions carry');
  });

  it('hides the manual elevation while a pressure is entered', () => {
    // Elevation only estimates pressure, so it does nothing here. A visible
    // field that silently does nothing is what invites bad experimenting.
    const html = render({ settings: settings({ mode: 'manual', manual_pressure_hpa: 1010.2 }) });

    expect(html).toContain('not used while a pressure is entered');
    expect(html).not.toContain('Increase Elevation');
  });

  it('offers the manual elevation once pressure is cleared', () => {
    const html = render({ settings: settings({ mode: 'manual', manual_pressure_hpa: null }) });

    expect(html).toContain('Increase Elevation');
    expect(html).toContain('Estimates pressure, since none is entered');
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

describe('LocationSearch', () => {
  const search = (results: LocationResult[]) =>
    renderToString(
      <LocationSearch
        query=""
        results={results}
        unitSystem="imperial"
        onQueryChange={() => {}}
        onSelect={() => {}}
        onClose={() => {}}
      />
    );

  it('lists results with the region that tells them apart', () => {
    // The postal code 95814 matches Sacramento CA and Argenteuil FR, so a
    // bare city name is not enough to choose between them.
    const html = search([
      { label: 'Sacramento, California, US', latitude: 38.58, longitude: -121.49, elevation_m: 9 },
      { label: 'Sacramento, Kentucky, US', latitude: 37.41, longitude: -87.26, elevation_m: 150 },
    ]);

    expect(html).toContain('Sacramento, California, US');
    expect(html).toContain('Sacramento, Kentucky, US');
  });

  it('shows each result its elevation, in the user unit', () => {
    const html = search([{ label: 'Sacramento, California, US', latitude: 38.58, longitude: -121.49, elevation_m: 9 }]);

    expect(html).toContain('30 ft'); // 9 m
  });

  it('omits the elevation when the search did not know it', () => {
    const html = search([{ label: 'Somewhere', latitude: 1, longitude: 1, elevation_m: null }]);

    expect(html).toContain('Somewhere');
    expect(html).not.toContain('location-search__elevation');
  });

  it('keeps the keys in a fixed panel so arriving results cannot move them', () => {
    // Results used to render beneath the field and shove the keyboard down the
    // screen mid-type, moving the target out from under a finger.
    const html = search([{ label: 'Sacramento', latitude: 1, longitude: 1, elevation_m: 9 }]);

    expect(html).toContain('text-keyboard__results');
    expect(html).toContain('text-keyboard__panel');
  });

  it('shows a caret so the box reads as live', () => {
    expect(search([])).toContain('text-keyboard__caret');
  });

  it('says what to do before a query is long enough', () => {
    expect(search([])).toContain('at least three characters');
  });
});
