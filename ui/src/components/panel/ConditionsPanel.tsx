import { useEnvironmentStore } from '../../stores/useEnvironmentStore';
import { useI18n } from '../../i18n/useI18n';
import { useUnitPreference } from '../../state/useUnitPreference';
import type { EnvironmentReading } from '../../types/socket';
import { formatAge } from '../../utils/age';
import { formatPressure, formatTemp, type UnitSystem } from '../../utils/units';
import { MetricCard } from '../ui/MetricCard';
import { PanelHeader } from './PanelHeader';

/**
 * The conditions carry silently assumes when nothing is fitted. Quoted back in
 * the user's own units, because an imperial user cannot sanity-check 15 °C /
 * 1013.25 hPa against anything they know.
 */
const ISA_TEMP_C = 15;
const ISA_PRESSURE_HPA = 1013.25;

const CHIP_NAMES: Record<string, string> = {
  bme280: 'BME280',
  bmp280: 'BMP280',
};

interface ConditionsPanelInnerProps {
  reading: EnvironmentReading | null;
  unitSystem: UnitSystem;
  /** Injected in tests so SSR is not stuck with the store's initial value. */
  connected?: boolean;
}

/**
 * Read-only view of the air the ball is flying through. Manual entry and an
 * outdoor weather lookup are separate changes; this panel exists so a fitted
 * sensor is verifiable at a glance rather than only in the session log.
 *
 * Exported separately from the store-connected wrapper so tests can drive it
 * with a fixed reading.
 */
export function ConditionsPanelInner({ reading, unitSystem, connected }: ConditionsPanelInnerProps) {
  const { t } = useI18n();

  if (!reading) {
    return (
      <div className="panel">
        <PanelHeader title={t('nav.conditions')} connected={connected} />
        <div className="panel__body conditions-panel">
          <div className="panel__body--empty">
            <span className="panel__empty-title">{t('conditions.loading')}</span>
          </div>
        </div>
      </div>
    );
  }

  const measured = reading.source !== 'default';
  const age = formatAge(reading.age_s);

  if (!measured) {
    return (
      <div className="panel">
        <PanelHeader
          title={t('nav.conditions')}
          subtitle={t('conditions.sensorNone')}
          connected={connected}
        />
        <div className="panel__body conditions-panel">
          <div className="panel__body--empty">
            <span className="panel__empty-title">{t('conditions.noSensor')}</span>
            <span className="panel__empty-detail">
              {t('conditions.noSensorDetail', {
                temp: formatTemp(ISA_TEMP_C, unitSystem),
                pressure: formatPressure(ISA_PRESSURE_HPA, unitSystem),
              })}
            </span>
          </div>
        </div>
      </div>
    );
  }

  const tiles: Array<{ id: string; label: string; value: string; unit?: string }> = [
    {
      id: 'density',
      label: t('conditions.density'),
      value: reading.air_density_kg_m3.toFixed(3),
      unit: 'kg/m³',
    },
    {
      id: 'deviation',
      label: t('conditions.vsIsa'),
      value: `${reading.deviation_pct.toFixed(1)}%`,
    },
  ];

  if (reading.temp_c !== null) {
    tiles.push({
      id: 'temp',
      label: t('conditions.temp'),
      value: formatTemp(reading.temp_c, unitSystem),
    });
  }
  if (reading.pressure_hpa !== null) {
    tiles.push({
      id: 'pressure',
      label: t('conditions.pressure'),
      value: formatPressure(reading.pressure_hpa, unitSystem),
    });
  }
  if (reading.humidity_pct !== null) {
    tiles.push({
      id: 'humidity',
      label: t('conditions.humidity'),
      value: reading.humidity_assumed
        ? t('conditions.humidityAssumed', { value: String(reading.humidity_pct) })
        : `${reading.humidity_pct}%`,
    });
  }

  return (
    <div className="panel">
      <PanelHeader
        title={t('nav.conditions')}
        subtitle={CHIP_NAMES[reading.source] ?? reading.source}
        connected={connected}
      />
      <div className="panel__body conditions-panel">
        <div className="conditions-panel__plays-like">
          <span className="conditions-panel__plays-like-label">{t('conditions.playsLike')}</span>
          <span className="conditions-panel__plays-like-value">
            {`${reading.density_altitude_ft.toLocaleString()} ft`}
          </span>
          <span className="conditions-panel__plays-like-hint">
            {reading.deviation_pct < 0 ? t('conditions.thinner') : t('conditions.denser')}
          </span>
        </div>

        <div className={`conditions-panel__grid conditions-panel__grid--of-${tiles.length}`}>
          {tiles.map((tile) => (
            <MetricCard
              key={tile.id}
              label={tile.label}
              value={tile.value}
              unit={tile.unit}
              labelPosition="above"
            />
          ))}
        </div>

        {reading.humidity_assumed && (
          <p className="conditions-panel__note">{t('conditions.humidityNote')}</p>
        )}
        {age && <p className="conditions-panel__age">{t('conditions.updated', { age })}</p>}
        <p className="conditions-panel__note">{t('conditions.windNote')}</p>
      </div>
    </div>
  );
}

export function ConditionsPanel() {
  const reading = useEnvironmentStore((state) => state.reading);
  const { unitSystem } = useUnitPreference();
  return <ConditionsPanelInner reading={reading} unitSystem={unitSystem} />;
}
