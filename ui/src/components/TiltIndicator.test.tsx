import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { InclinometerStatus } from '../types/inclinometer';
import { TiltIndicator } from './TiltIndicator';

const status = (overrides: Partial<InclinometerStatus> = {}): InclinometerStatus => ({
  enabled: true,
  status: 'ok',
  reading_status: 'stable',
  pitch_deg: 10.2,
  roll_deg: -0.4,
  raw_pitch_deg: 10.2,
  raw_roll_deg: -0.4,
  expected_pitch_deg: 10.0,
  expected_roll_deg: 0.0,
  pitch_error_deg: 0.2,
  roll_error_deg: -0.4,
  tolerance_deg: 5,
  reason: 'placed as designed',
  ...overrides,
});

describe('TiltIndicator', () => {
  it('shows pitch, roll, and a green verdict when the unit is placed as designed', () => {
    const html = renderToString(<TiltIndicator status={status()} />);

    expect(html).toContain('tilt-indicator--ok');
    expect(html).toContain('10.2°');
    expect(html).toContain('-0.4°');
    expect(html).toContain('>Placed OK<');
    expect(html).toContain('aria-label="Tilt: Placed OK, pitch 10.2°, roll -0.4°"');
  });

  it('turns red and names the axis that is out', () => {
    const pitched = renderToString(
      <TiltIndicator status={status({ status: 'tilted_pitch', pitch_deg: 17 })} />
    );
    const rolled = renderToString(<TiltIndicator status={status({ status: 'tilted_roll' })} />);
    const both = renderToString(<TiltIndicator status={status({ status: 'tilted_both' })} />);

    expect(pitched).toContain('tilt-indicator--crit');
    expect(pitched).toContain('>Pitch off<');
    expect(pitched).toContain('17.0°');
    expect(rolled).toContain('>Roll off<');
    expect(both).toContain('>Pitch + roll off<');
  });

  it('says "cannot tell" in amber rather than dressing it up as a pass', () => {
    const noReading = renderToString(
      <TiltIndicator
        status={status({ status: 'no_reading', reading_status: 'moving', pitch_deg: null, roll_deg: null })}
      />
    );
    const noExpectation = renderToString(
      <TiltIndicator status={status({ status: 'no_expected_orientation' })} />
    );

    expect(noReading).toContain('tilt-indicator--warn');
    expect(noReading).toContain('>No reading<');
    expect(noReading).toContain('--');
    expect(noReading).not.toContain('tilt-indicator--ok');
    expect(noExpectation).toContain('>Not checked<');
  });

  it('says there is no tilt sensor when the inclinometer is disabled', () => {
    const html = renderToString(
      <TiltIndicator
        status={status({
          enabled: false,
          status: 'disabled',
          pitch_deg: null,
          roll_deg: null,
          expected_pitch_deg: null,
          expected_roll_deg: null,
          reason: 'no tilt sensor: the inclinometer is not enabled (--inclinometer)',
        })}
      />
    );

    expect(html).toContain('>No tilt sensor<');
    expect(html).toContain('aria-label="Tilt: No tilt sensor"');
  });

  it('renders nothing before the server has reported', () => {
    expect(renderToString(<TiltIndicator status={null} />)).toBe('');
  });
});
