import { useRef } from 'react';
import { useDragScroll } from '../../hooks/useDragScroll';
import { useI18n } from '../../i18n/useI18n';
import { useUnitPreference } from '../../state/useUnitPreference';
import { useAirStore } from '../../stores/useAirStore';
import type { AirConditions } from '../../types/socket';
import { formatAltitude, formatPressure, formatTemperature, type UnitSystem } from '../../utils/units';
import { MetricCard } from '../ui/MetricCard';
import { PanelHeader } from './PanelHeader';

// ISA sea level, dry: the raw flight's air when there is no reading (1.225 kg/m³).
const ISA = { temperatureC: 15, pressureHpa: 1013.25, densityKgM3: 1.225 };
// TrackMan's normalization reference, which the normalized flight always uses.
const NORMALIZED = { temperatureC: 25, pressureHpa: 1013.25 };

interface ConditionsPanelInnerProps {
  conditions: AirConditions | null;
  unitSystem: UnitSystem;
  /** Pass in tests so SSR is not stuck with the store's initial value. */
  connected?: boolean;
}

/** What the BME280 measured, or that standard air is assumed and why. */
export function ConditionsPanelInner({ conditions, unitSystem, connected }: ConditionsPanelInnerProps) {
  const { t } = useI18n();
  const bodyRef = useRef<HTMLDivElement>(null);
  const dragScroll = useDragScroll(bodyRef);
  const measured = conditions?.source === 'bme280';
  const subtitle = conditions === null ? undefined : measured ? 'BME280' : t('conditions.standardAir');
  const normalizedNote = t('conditions.flightNote', {
    temperature: formatTemperature(NORMALIZED.temperatureC, unitSystem),
    pressure: formatPressure(NORMALIZED.pressureHpa, unitSystem),
  });

  let body;
  if (conditions === null) {
    body = (
      <div className="panel__body panel__body--empty">
        <span className="panel__empty-title">{t('conditions.loading')}</span>
      </div>
    );
  } else if (!measured) {
    body = (
      <div className="panel__body panel__body--empty conditions-panel__standard">
        <span className="panel__empty-title">{t('conditions.isaTitle')}</span>
        <span className="conditions-panel__note">
          {t('conditions.isaDetail', {
            temperature: formatTemperature(ISA.temperatureC, unitSystem),
            pressure: formatPressure(ISA.pressureHpa, unitSystem),
            density: ISA.densityKgM3.toFixed(3),
          })}
        </span>
        {conditions.reason ? (
          <span className="conditions-panel__note">{t('conditions.isaReason', { reason: conditions.reason })}</span>
        ) : null}
        <span className="conditions-panel__note">{t('conditions.windNote')}</span>
      </div>
    );
  } else {
    const tiles = [
      {
        id: 'temperature',
        label: t('conditions.temperature'),
        value: conditions.temperature_c,
        format: formatTemperature,
      },
      { id: 'pressure', label: t('conditions.pressure'), value: conditions.pressure_hpa, format: formatPressure },
    ];
    body = (
      <div
        className="panel__body conditions-panel__body"
        ref={bodyRef}
        onPointerDown={dragScroll.onPointerDown}
        onPointerMove={dragScroll.onPointerMove}
        onPointerUp={dragScroll.onPointerUp}
        onPointerCancel={dragScroll.onPointerCancel}
        onClickCapture={dragScroll.onClickCapture}
      >
        {conditions.density_altitude_ft !== null ? (
          <div className="conditions-panel__plays-like">
            <span className="conditions-panel__label">{t('conditions.playsLike')}</span>
            <span className="conditions-panel__altitude">
              {formatAltitude(conditions.density_altitude_ft, unitSystem)}
            </span>
            <span className="conditions-panel__note">
              {conditions.air_density_kg_m3 < ISA.densityKgM3 ? t('conditions.thinner') : t('conditions.denser')}
            </span>
          </div>
        ) : null}
        <div className="conditions-panel__grid">
          {tiles.map((tile) =>
            tile.value === null ? null : (
              <MetricCard
                key={tile.id}
                label={tile.label}
                value={tile.format(tile.value, unitSystem)}
                labelPosition="above"
              />
            )
          )}
          {conditions.humidity_pct !== null ? (
            <MetricCard
              label={t('conditions.humidity')}
              value={conditions.humidity_pct.toFixed(0)}
              unit="%"
              labelPosition="above"
            />
          ) : null}
          <MetricCard
            label={t('conditions.density')}
            value={conditions.air_density_kg_m3.toFixed(3)}
            unit="kg/m³"
            labelPosition="above"
          />
        </div>
        {conditions.age_s !== null ? (
          <p className="conditions-panel__note">{t('conditions.age', { seconds: conditions.age_s.toFixed(0) })}</p>
        ) : null}
        <p className="conditions-panel__note">{normalizedNote}</p>
        <p className="conditions-panel__note">{t('conditions.windNote')}</p>
      </div>
    );
  }

  return (
    <div className="panel conditions-panel">
      <PanelHeader title={t('nav.conditions')} subtitle={subtitle} connected={connected} />
      {body}
    </div>
  );
}

export function ConditionsPanel() {
  const conditions = useAirStore((state) => state.conditions);
  const { unitSystem } = useUnitPreference();
  return <ConditionsPanelInner conditions={conditions} unitSystem={unitSystem} />;
}
