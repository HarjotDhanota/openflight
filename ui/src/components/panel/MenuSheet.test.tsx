import { describe, expect, it } from 'vitest';
import { renderToString } from 'react-dom/server';
import { MenuSheet, RadarDataControl } from './MenuSheet';
import { useSystemStore } from '../../stores/useSystemStore';
import type { PowerStatus } from '../../types/power';

function renderMenu() {
  return renderToString(<MenuSheet onClose={() => {}} onShutdown={() => {}} />);
}

describe('MenuSheet profiles', () => {
  it('does not manage profiles in the menu', () => {
    const html = renderMenu();

    expect(html).not.toContain('menu-sheet__section-title">Profile');
    expect(html).not.toContain('Add profile');
    expect(html).not.toContain('menu-sheet__input');
  });
});

describe('MenuSheet language', () => {
  it('offers a language dropdown with the shipped locales', () => {
    const html = renderMenu();

    expect(html).toContain('menu-sheet__section-title">Language');
    expect(html).toContain('aria-label="Language"');
    expect(html).toContain('>English</option>');
    expect(html).toContain('>Español</option>');
    expect(html).toContain('>Français</option>');
    expect(html).toContain('>Português</option>');
  });
});

describe('MenuSheet battery', () => {
  it('does not show battery in the menu, even when telemetry is present', () => {
    const powerStatus: PowerStatus = {
      available: true,
      provider: 'geekworm',
      state: 'on_battery',
      battery_percent: 64.2,
      battery_voltage_v: 3.81,
      external_power: false,
      updated_at: '2026-08-20T12:00:00+00:00',
      error: null,
    };
    useSystemStore.setState({ powerStatus });

    const html = renderMenu();

    expect(html).not.toContain('menu-sheet__status-label">Battery');
    expect(html).not.toContain('power-status');
    expect(html).not.toContain('64%');

    useSystemStore.setState({ powerStatus: null });
  });
});

describe('MenuSheet radar data', () => {
  const status = {
    enabled: true,
    requested: 'two_round' as const,
    effective: 'two_round' as const,
    available: true,
    reason: null,
    last_fallback: null,
  };
  const control = (value: typeof status | null) =>
    renderToString(<RadarDataControl status={value} onChange={() => {}} />);

  it('hides the control without an IWR radar', () => {
    expect(control(null)).toBe('');
    expect(control({ ...status, enabled: false })).toBe('');
  });

  it('offers Fast and Full and marks the mode asked for', () => {
    const html = control(status);

    expect(html).toContain('menu-sheet__section-title">Radar data');
    expect(html).toContain('aria-pressed="true">Fast</button>');
    expect(html).toContain('aria-pressed="false">Full (complete data)</button>');
    expect(html).not.toContain('Unavailable:');
  });

  it('says why Fast is unavailable', () => {
    const html = control({
      ...status,
      effective: 'full',
      available: false,
      reason: 'the radar firmware has no fast-transfer commands',
    });

    expect(html).toContain('Unavailable: the radar firmware has no fast-transfer commands');
  });

  it('shows a refused change without marking the requested mode as accepted', () => {
    const html = renderToString(
      <RadarDataControl status={status} error="Changes are only accepted on the kiosk" onChange={() => {}} />
    );
    expect(html).toContain('role="alert"');
    expect(html).toContain('Changes are only accepted on the kiosk');
  });
});
