import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { AirConditions } from '../../types/socket';
import type { UnitSystem } from '../../utils/units';
import { ConditionsPanelInner } from './ConditionsPanel';

const measured = (overrides: Partial<AirConditions> = {}): AirConditions => ({
  air_density_kg_m3: 1.1316,
  source: 'bme280',
  temperature_c: 31.5,
  pressure_hpa: 1002.4,
  humidity_pct: 38,
  measured_at: 1000,
  age_s: 4,
  reason: null,
  density_altitude_ft: 2685,
  ...overrides,
});

const standardAir = (reason = 'no BME280 on I2C bus 1 (0x76: no reply; 0x77: no reply)'): AirConditions => ({
  air_density_kg_m3: 1.225,
  source: 'isa_default',
  temperature_c: null,
  pressure_hpa: null,
  humidity_pct: null,
  measured_at: null,
  age_s: null,
  reason,
  density_altitude_ft: null,
});

function render(conditions: AirConditions | null, unitSystem: UnitSystem = 'imperial') {
  return renderToString(<ConditionsPanelInner conditions={conditions} unitSystem={unitSystem} connected />);
}

describe('ConditionsPanel', () => {
  it('waits for the first reading without claiming either air', () => {
    const html = render(null);
    expect(html).toContain('Reading conditions');
    expect(html).not.toContain('BME280');
    expect(html).not.toContain('Standard air');
  });

  it('shows what the BME280 measured, in the user units', () => {
    const html = render(measured());
    expect(html).toContain('panel-header__title">Conditions<');
    expect(html).toContain('BME280');
    expect(html).toContain('88.7');
    expect(html).toContain('°F');
    expect(html).toContain('29.60');
    expect(html).toContain('inHg');
    expect(html).toContain('38');
    expect(html).toContain('1.132');
    expect(html).toContain('kg/m³');
  });

  it('shows metric readings in metric', () => {
    const html = render(measured(), 'metric');
    expect(html).toContain('31.5');
    expect(html).toContain('°C');
    expect(html).toContain('1002');
    expect(html).toContain('hPa');
    expect(html).not.toContain('°F');
  });

  it('leads with the altitude the air plays like, and which way it cuts', () => {
    expect(render(measured())).toContain('2,685 ft');
    expect(render(measured(), 'metric')).toContain('818 m');
    expect(render(measured())).toContain('flies farther');
    expect(render(measured({ air_density_kg_m3: 1.27, density_altitude_ft: -1400 }))).toContain('flies shorter');
  });

  it('says how old the reading is', () => {
    expect(render(measured({ age_s: 4 }))).toContain('4 s ago');
  });

  it('says raw flight uses this air and normalized flight the reference', () => {
    const html = render(measured());
    expect(html).toContain('Raw flight uses this air');
    expect(html).toContain('77.0 °F');
  });

  it('says standard air is assumed, and why, with nothing filled in', () => {
    const html = render(standardAir());
    expect(html).toContain('Standard air');
    expect(html).toContain('59.0 °F');
    expect(html).toContain('29.92 inHg');
    expect(html).toContain('1.225');
    expect(html).toContain('0x76: no reply');
    expect(html).not.toContain('Humidity');
    expect(html).not.toContain('ft<');
  });

  it('states the assumed air in metric for a metric user', () => {
    const html = render(standardAir(), 'metric');
    expect(html).toContain('15.0 °C');
    expect(html).toContain('1013 hPa');
  });

  it('names a stale sensor as the reason', () => {
    expect(render(standardAir('the last BME280 reading is 75 s old (limit 60 s)'))).toContain('75 s old');
  });

  it('states that wind is not measured', () => {
    expect(render(measured())).toContain('wind');
    expect(render(standardAir())).toContain('wind');
  });
});
