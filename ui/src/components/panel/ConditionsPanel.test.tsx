import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { EnvironmentReading } from '../../types/socket';
import type { UnitSystem } from '../../utils/units';
import { ConditionsPanelInner } from './ConditionsPanel';

const reading = (overrides: Partial<EnvironmentReading> = {}): EnvironmentReading => ({
  air_density_kg_m3: 1.1316,
  source: 'bme280',
  temp_c: 36.1,
  pressure_hpa: 1010.2,
  humidity_pct: 25,
  humidity_assumed: false,
  age_s: 4,
  deviation_pct: -7.6,
  density_altitude_ft: 2685,
  ...overrides,
});

function render(value: EnvironmentReading | null, unitSystem: UnitSystem = 'imperial') {
  return renderToString(
    <ConditionsPanelInner reading={value} unitSystem={unitSystem} connected={true} />,
  );
}

describe('ConditionsPanel', () => {
  it('says plainly that carry is uncorrected when no sensor is fitted', () => {
    // The default state, and the one that silently costs yards on a warm day.
    // It has to be visible rather than merely absent.
    const html = render(reading({ source: 'default', deviation_pct: 0, temp_c: null }));

    expect(html).toContain('No sensor');
    expect(html).toContain('assumes standard sea-level air');
    expect(html).toContain('--air-sensor');
  });

  it('states the assumed conditions in the units the user actually reads', () => {
    // 15 C / 1013.25 hPa is 59.0 F / 29.92 inHg. An imperial user quoted metric
    // reference conditions cannot sanity-check them against anything.
    const noSensor = reading({ source: 'default', deviation_pct: 0, temp_c: null });

    const imperial = render(noSensor, 'imperial');
    expect(imperial).toContain('59.0 °F');
    expect(imperial).toContain('29.92 inHg');
    expect(imperial).not.toContain('15.0 °C');

    const metric = render(noSensor, 'metric');
    expect(metric).toContain('15.0 °C');
    expect(metric).toContain('1013 hPa');
  });

  it('leads with density altitude, not the density figure', () => {
    // "Plays like 2,685 ft" is a number a golfer already has an instinct for;
    // 1.132 kg/m3 is not.
    const html = render(reading());

    expect(html).toContain('Plays like');
    expect(html).toContain('2,685 ft');
    expect(html).toContain('1.132');
  });

  it('names the chip that produced the reading', () => {
    expect(render(reading({ source: 'bme280' }))).toContain('BME280');
    expect(render(reading({ source: 'bmp280', humidity_assumed: true }))).toContain('BMP280');
  });

  it('says which way thinner air cuts', () => {
    // Sign errors here are invisible in a number and obvious in a sentence.
    expect(render(reading({ deviation_pct: -7.6 }))).toContain('flies further');
    expect(render(reading({ deviation_pct: 3.1 }))).toContain('flies shorter');
  });

  it('marks an assumed humidity rather than presenting it as measured', () => {
    // A BMP280 has no humidity channel. Showing a bare "50%" would read as a
    // measurement.
    const html = render(reading({ source: 'bmp280', humidity_pct: 50, humidity_assumed: true }));

    expect(html).toContain('(assumed)');
    expect(html).toContain('no humidity channel');
  });

  it('does not mark a measured humidity as assumed', () => {
    const html = render(reading({ humidity_pct: 25, humidity_assumed: false }));

    expect(html).toContain('25%');
    expect(html).not.toContain('(assumed)');
  });

  it('omits channels the chip did not report', () => {
    const html = render(reading({ temp_c: null, pressure_hpa: null, humidity_pct: null }));

    expect(html).not.toContain('Pressure');
    expect(html).not.toContain('Humidity');
    // Density and its deviation come from the provider, so they always show.
    expect(html).toContain('Density');
  });

  it('states that wind is not corrected', () => {
    // Air density is not wind, and a user seeing a weather panel will assume it
    // is unless told otherwise.
    expect(render(reading())).toContain('Neither radar measures wind');
  });

  it('renders before the first reading arrives', () => {
    expect(render(null)).toContain('Reading conditions');
  });
});
