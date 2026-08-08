// Uses renderToString, matching SimStatus.test.tsx and the rest of the suite.
// The project has no jsdom and no @testing-library; adding them is shared
// infrastructure and belongs in its own PR, not inside a feature.
//
// The view arrives as a prop rather than from the store, matching
// ConnectionStatus and SimStatus. That is not only convention: zustand v5
// backs its hook with useSyncExternalStore, whose server snapshot is
// getInitialState(), so setState() is invisible to renderToString and a
// store-reading component would render its initial state forever under SSR.
//
// Effects do not run under SSR, so this covers first render only. The
// countdown tick, the re-sync on a fresh view, and the "Keep running" click
// are verified by hand -- see Task 12.
import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { BatteryStatus } from './BatteryStatus';
import type { PowerView } from '../stores/usePowerStore';

const base: PowerView = {
  pack_volts: 3.81,
  pack_percent: 62,
  pack_level: 'ok',
  rail_volts: 5.09,
  rail_level: 'green',
  source: 'battery',
  runtime_minutes: null,
  shutdown_eligible: false,
  pending_shutdown: null,
  shutdown_remaining_seconds: null,
  warnings: [],
};

const pending = { id: 'abc', reason: 'Pack at 3.18 V' };

describe('BatteryStatus', () => {
  it('renders nothing when no power data has arrived', () => {
    expect(renderToString(<BatteryStatus view={null} />)).toBe('');
  });

  it('renders nothing when every reader is absent', () => {
    const view = {
      ...base,
      pack_level: 'unknown' as const,
      rail_level: 'unknown' as const,
      pack_percent: null,
    };
    expect(renderToString(<BatteryStatus view={view} />)).toBe('');
  });

  it('shows the percentage', () => {
    expect(renderToString(<BatteryStatus view={base} />)).toContain('62%');
  });

  it('shows the percentage and an external-power marker on wall power', () => {
    // ModelGauge tracks across charge, so the number stays meaningful.
    const html = renderToString(<BatteryStatus view={{ ...base, source: 'external' }} />);
    expect(html).toContain('62%');
    expect(html).toContain('On external power');
  });

  it('carries the rail level as a class on the dot', () => {
    const view = { ...base, rail_level: 'amber' as const };
    expect(renderToString(<BatteryStatus view={view} />)).toContain(
      'battery-status__dot--amber'
    );
  });

  it('hides the fuel bar when only the rail is present', () => {
    const view = { ...base, pack_level: 'unknown' as const, pack_percent: null };
    const html = renderToString(<BatteryStatus view={view} />);
    expect(html).not.toContain('%');
    expect(html).toContain('battery-status__dot');
  });

  it('renders the warnings the server sent, verbatim', () => {
    // Policy lives on the server. The component must not re-derive a warning
    // from pack_level, or the two would drift.
    const view = {
      ...base,
      pack_level: 'critical' as const,
      warnings: ['Battery critically low'],
    };
    expect(renderToString(<BatteryStatus view={view} />)).toContain(
      'Battery critically low'
    );
  });

  it('shows a Keep running button and the server-supplied seconds', () => {
    // useState seeds from the prop and effects do not run in SSR, so this
    // asserts the countdown starts from the right number -- not that it ticks.
    const view = {
      ...base,
      pack_level: 'critical' as const,
      pending_shutdown: pending,
      shutdown_remaining_seconds: 45,
    };
    const html = renderToString(<BatteryStatus view={view} />);
    expect(html).toContain('Keep running');
    expect(html).toContain('45s');
  });

  it('omits the shutdown block entirely when nothing is pending', () => {
    const view = {
      ...base,
      pack_level: 'critical' as const,
      warnings: ['Battery critically low'],
    };
    const html = renderToString(<BatteryStatus view={view} />);
    expect(html).not.toContain('Keep running');
    expect(html).toContain('Battery critically low');
  });
});
