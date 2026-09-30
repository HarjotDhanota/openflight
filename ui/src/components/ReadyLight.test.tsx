import { renderToString } from 'react-dom/server';
import { afterEach, describe, expect, it } from 'vitest';
import type { ReadyLight } from '../types/readyLight';
import { useSystemStore } from '../stores/useSystemStore';
import { kioskRestartingLight, swingIsFresh, timeLeftSeconds } from '../utils/readyLight';
import { ReadyLightBand } from './ReadyLight';

const light = (overrides: Partial<ReadyLight> = {}): ReadyLight => ({
  schema_version: 1,
  state: 'green',
  word: 'SWING',
  cause: 'Ready for a swing',
  time_left_s: null,
  causes: [],
  since: 100,
  checked_at: 110,
  hold: null,
  swing: null,
  ...overrides,
});

describe('ReadyLightBand', () => {
  afterEach(() => {
    useSystemStore.setState({ readyLight: null });
  });

  it('shows nothing until the kiosk reports a light, or where it is off', () => {
    expect(renderToString(<ReadyLightBand light={null} />)).toBe('');
    expect(renderToString(<ReadyLightBand light={light({ state: 'off', word: '', cause: null })} />)).toBe('');
  });

  it('says the word as well as the colour, with the cause', () => {
    const green = renderToString(<ReadyLightBand light={light()} />);
    const amber = renderToString(
      <ReadyLightBand light={light({ state: 'amber', word: 'WAIT', cause: 'IWR6843 dumping', time_left_s: 4.2 })} />
    );
    const red = renderToString(
      <ReadyLightBand light={light({ state: 'red', word: 'NOT READY', cause: 'camera frames stopped' })} />
    );

    expect(green).toContain('data-state="green"');
    expect(green).toContain('SWING');
    expect(green).toContain('role="status"');
    expect(amber).toContain('data-state="amber"');
    expect(amber).toContain('WAIT');
    expect(amber).toContain('IWR6843 dumping');
    expect(amber).toContain('about 5 s');
    expect(red).toContain('data-state="red"');
    expect(red).toContain('NOT READY');
    expect(red).toContain('camera frames stopped');
  });

  it('shows the last swing and its result', () => {
    const shot = renderToString(
      <ReadyLightBand
        light={light({
          swing: { id: 3, at: 100, source: 'edge', result: { kind: 'shot', text: '141.3 mph', ball_speed_mph: 141.3 } },
        })}
      />
    );
    const noShot = renderToString(
      <ReadyLightBand
        light={light({
          swing: {
            id: 4,
            at: 109,
            source: 'edge',
            result: { kind: 'no_radar_shot', text: 'No radar shot', ball_speed_mph: null },
          },
        })}
      />
    );
    const pending = renderToString(
      <ReadyLightBand light={light({ swing: { id: 5, at: 109.5, source: 'edge', result: null } })} />
    );

    expect(shot).toContain('Last swing: 141.3 mph');
    expect(noShot).toContain('Last swing: No radar shot');
    expect(pending).toContain('Swing picked up');
  });

  it('flashes only for a swing picked up moments ago', () => {
    const fresh = renderToString(
      <ReadyLightBand light={light({ swing: { id: 6, at: 109, source: 'edge', result: null } })} />
    );
    const old = renderToString(
      <ReadyLightBand light={light({ swing: { id: 6, at: 90, source: 'edge', result: null } })} />
    );

    expect(fresh).toContain('ready-light__flash');
    expect(old).not.toContain('ready-light__flash');
  });

  it('reads the light from the system store', () => {
    useSystemStore.getState().setReadyLight(light({ state: 'amber', word: 'WAIT', cause: 'OPS243 re-arming' }));

    expect(useSystemStore.getState().readyLight?.cause).toBe('OPS243 re-arming');
  });
});

describe('ready light helpers', () => {
  it('rounds the time left up to whole seconds', () => {
    expect(timeLeftSeconds(null)).toBeNull();
    expect(timeLeftSeconds(0)).toBe(0);
    expect(timeLeftSeconds(6.1)).toBe(7);
  });

  it('judges a swing fresh for three seconds from the kiosk clock', () => {
    expect(swingIsFresh(light({ swing: { id: 1, at: 108, source: 'edge', result: null } }))).toBe(true);
    expect(swingIsFresh(light({ swing: { id: 1, at: 106, source: 'edge', result: null } }))).toBe(false);
    expect(swingIsFresh(light())).toBe(false);
  });

  it('turns a lost kiosk red, keeping the last swing', () => {
    const swing = { id: 2, at: 100, source: 'edge', result: null };
    const lost = kioskRestartingLight(light({ swing }));

    expect(lost?.state).toBe('red');
    expect(lost?.word).toBe('NOT READY');
    expect(lost?.cause).toBe('kiosk restarting');
    expect(lost?.swing).toEqual(swing);
    expect(kioskRestartingLight(null)).toBeNull();
    expect(kioskRestartingLight(light({ state: 'off', word: '', cause: null }))).toBeNull();
  });
});
