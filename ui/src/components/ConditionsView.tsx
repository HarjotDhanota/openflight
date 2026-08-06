import { useEnvironmentStore } from '../stores/useEnvironmentStore';
import type { EnvironmentReading } from '../types/socket';
import { formatAge } from '../utils/age';
import { formatPressure, formatTemp, type UnitSystem } from '../utils/units';
import './ConditionsView.css';

const CHIP_NAMES: Record<string, string> = {
  bme280: 'BME280',
  bmp280: 'BMP280',
};

/**
 * Read-only for now. Manual entry and an outdoor weather lookup are separate
 * changes; this panel exists so a fitted sensor is verifiable at a glance
 * rather than only in the log.
 */
export function ConditionsViewInner({
  reading,
  unitSystem,
}: {
  reading: EnvironmentReading | null;
  unitSystem: UnitSystem;
}) {
  if (!reading) {
    return <div className="conditions conditions--loading">Reading conditions…</div>;
  }

  const measured = reading.source !== 'default';
  const age = formatAge(reading.age_s);

  return (
    <div className="conditions">
      <header className="conditions__header">
        <h2>Conditions</h2>
        <span className={`conditions__badge ${measured ? 'conditions__badge--ok' : 'conditions__badge--warn'}`}>
          {measured ? (CHIP_NAMES[reading.source] ?? reading.source) : 'No sensor'}
        </span>
      </header>

      {!measured ? (
        <p className="conditions__empty">
          No air-density sensor is fitted, so carry assumes standard sea-level air — 15 °C, 1013 hPa, dry. On a hot
          afternoon that is worth several yards on a driver, and about 14 yards at Denver&rsquo;s altitude.
          <br />
          <br />
          Fit a BME280 on I²C and start with <code>--air-sensor</code> to measure it instead.
        </p>
      ) : (
        <>
          <div className="conditions__plays-like">
            <span className="conditions__plays-like-label">Plays like</span>
            <span className="conditions__plays-like-value">{`${reading.density_altitude_ft.toLocaleString()} ft`}</span>
            <span className="conditions__plays-like-hint">
              {reading.deviation_pct < 0
                ? 'thinner air than standard — the ball flies further'
                : 'denser air than standard — the ball flies shorter'}
            </span>
          </div>

          <div className="conditions__readout">
            <div className="conditions__value">
              <span className="conditions__value-label">Density</span>
              <span className="conditions__value-number">{`${reading.air_density_kg_m3.toFixed(3)} kg/m³`}</span>
            </div>
            <div className="conditions__value">
              <span className="conditions__value-label">vs ISA sea level</span>
              <span className="conditions__value-number">{`${reading.deviation_pct.toFixed(1)}%`}</span>
            </div>
            {reading.temp_c !== null && (
              <div className="conditions__value">
                <span className="conditions__value-label">Temp</span>
                <span className="conditions__value-number">{formatTemp(reading.temp_c, unitSystem)}</span>
              </div>
            )}
            {reading.pressure_hpa !== null && (
              <div className="conditions__value">
                <span className="conditions__value-label">Pressure</span>
                <span className="conditions__value-number">{formatPressure(reading.pressure_hpa, unitSystem)}</span>
              </div>
            )}
            {reading.humidity_pct !== null && (
              <div className="conditions__value">
                <span className="conditions__value-label">Humidity</span>
                <span className="conditions__value-number">
                  {`${reading.humidity_pct}%${reading.humidity_assumed ? ' (assumed)' : ''}`}
                </span>
              </div>
            )}
          </div>

          {reading.humidity_assumed && (
            <p className="conditions__hint">
              This chip has no humidity channel, so 50% is assumed. Humidity is the smallest of the three terms — the
              assumption is worth under half a yard on a driver.
            </p>
          )}

          {age && <p className="conditions__age">{`Updated ${age}`}</p>}

          <p className="conditions__hint">
            Corrects for air density only. Neither radar measures wind, and no amount of sensing here can — a headwind
            is not an air-density effect.
          </p>
        </>
      )}
    </div>
  );
}

export function ConditionsView({ unitSystem }: { unitSystem: UnitSystem }) {
  const reading = useEnvironmentStore((state) => state.reading);
  return <ConditionsViewInner reading={reading} unitSystem={unitSystem} />;
}
