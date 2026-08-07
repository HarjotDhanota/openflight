import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { EnvironmentReading } from '../types/socket';
import type { UnitSystem } from '../utils/units';
import { formatAge } from '../utils/age';
import { ConditionsViewInner } from './ConditionsView';

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
  return renderToString(<ConditionsViewInner reading={value} unitSystem={unitSystem} />);
}

describe('ConditionsView', () => {
  it('says plainly that carry is uncorrected when no sensor is fitted', () => {
    // The default state, and the one that silently costs yards on a warm day.
    // It has to be visible rather than merely absent.
    const html = render(reading({ source: 'default', deviation_pct: 0, temp_c: null }));

    expect(html).toContain('No sensor');
    expect(html).toContain('assumes standard sea-level air');
    expect(html).toContain('--air-sensor');
  });

  it('states the assumed conditions in the units the user actually reads', () => {
    // 15 C / 1013.25 hPa is 59.0 F / 29.92 inHg. An imperial user quoted
    // metric reference conditions cannot sanity-check them against anything.
    const noSensor = reading({ source: 'default', deviation_pct: 0, temp_c: null });

    const imperial = render(noSensor, 'imperial');
    expect(imperial).toContain('59.0 °F');
    expect(imperial).toContain('29.92 inHg');
    expect(imperial).not.toContain('15.0 °C');

    const metric = render(noSensor, 'metric');
    expect(metric).toContain('15.0 °C');
    expect(metric).toContain('1013 hPa');
    expect(metric).not.toContain('°F');
  });

  it('does not show a density readout it has not measured', () => {
    const html = render(reading({ source: 'default', deviation_pct: 0, temp_c: null }));

    expect(html).not.toContain('Plays like');
    expect(html).not.toContain('kg/m³');
  });

  it('names the chip that was actually found', () => {
    expect(render(reading())).toContain('BME280');
    expect(render(reading({ source: 'bmp280' }))).toContain('BMP280');
  });

  it('leads with density altitude, the unit that can be checked against experience', () => {
    const html = render(reading());

    expect(html).toContain('Plays like');
    expect(html).toContain('2,685 ft');
  });

  it('explains which way thinner air pushes the ball', () => {
    expect(render(reading({ deviation_pct: -7.6 }))).toContain('the ball flies further');
    expect(render(reading({ deviation_pct: 4.2 }))).toContain('the ball flies shorter');
  });

  it('renders temperature in Fahrenheit for imperial users', () => {
    // 36.1 C is 97.0 F. The digits must be converted, not relabelled.
    expect(render(reading())).toContain('97.0 °F');
  });

  it('renders temperature in Celsius for metric users', () => {
    const html = render(reading(), 'metric');

    expect(html).toContain('36.1 °C');
    expect(html).not.toContain('97.0 °F');
  });

  it('renders pressure in inHg for imperial and hPa for metric', () => {
    // 1010.2 hPa is 29.83 inHg.
    expect(render(reading())).toContain('29.83 inHg');
    expect(render(reading(), 'metric')).toContain('1010 hPa');
  });

  it('marks an assumed humidity as assumed rather than presenting it as read', () => {
    const html = render(reading({ source: 'bmp280', humidity_pct: 50, humidity_assumed: true }));

    expect(html).toContain('(assumed)');
    expect(html).toContain('no humidity channel');
  });

  it('does not caveat a humidity that really was measured', () => {
    const html = render(reading());

    expect(html).not.toContain('(assumed)');
    expect(html).not.toContain('no humidity channel');
  });

  it('says wind is not measured, because density correction invites that assumption', () => {
    expect(render(reading())).toContain('Neither radar measures wind');
  });

  it('shows how old the reading is', () => {
    expect(render(reading({ age_s: 4 }))).toContain('just now');
    expect(render(reading({ age_s: 300 }))).toContain('5 min ago');
  });

  it('shows a placeholder until the server has answered', () => {
    // Distinct from "no sensor": nothing is known yet either way.
    const html = render(null);

    expect(html).toContain('Reading conditions');
    expect(html).not.toContain('No sensor');
  });
});

describe('formatAge', () => {
  it('is null when there is no age to show', () => {
    expect(formatAge(null)).toBeNull();
  });

  it('reads naturally either side of a minute', () => {
    expect(formatAge(4)).toBe('just now');
    expect(formatAge(60)).toBe('a minute ago');
    expect(formatAge(600)).toBe('10 min ago');
  });
});
