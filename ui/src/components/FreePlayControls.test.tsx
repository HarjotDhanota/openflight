import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { FreePlayStatus } from './FreePlayControls';

const noop = () => {};
describe('Free-play status', () => {
  it('only appears in a free-play kiosk', () => {
    expect(renderToString(<FreePlayStatus state={{ enabled: false }} onAction={noop} />)).toBe('');
  });
  it('keeps the stop action and unverified geometry visible', () => {
    const html = renderToString(
      <FreePlayStatus state={{ enabled: true, phase: 'recording', geometry_unverified: true }} onAction={noop} />
    );
    expect(html).toContain('Stop &amp; package');
    expect(html).toContain('Geometry unverified');
  });
  it('prevents a second stop while packaging', () => {
    const html = renderToString(<FreePlayStatus state={{ enabled: true, phase: 'packaging' }} onAction={noop} />);
    expect(html).toContain('disabled');
    expect(html).toContain('Stopping and packaging');
  });
  it('shows the completed bundle path and a Done action', () => {
    const html = renderToString(
      <FreePlayStatus
        state={{ enabled: true, phase: 'ready', bundle: { name: 'session.zip', path: '/captures/session.zip' } }}
        onAction={noop}
      />
    );
    expect(html).toContain('Package ready: session.zip');
    expect(html).toContain('/captures/session.zip');
    expect(html).toContain('>Done</button>');
    expect(html).not.toContain('disabled');
  });
  it('names the bundle even when only its name is known', () => {
    const html = renderToString(
      <FreePlayStatus state={{ enabled: true, phase: 'ready', bundle: { name: 'session.zip' } }} onAction={noop} />
    );
    expect(html).toContain('Package ready: session.zip');
  });
  it('takes the whole band once acquisition stops: the ready light no longer applies', () => {
    const recording = renderToString(<FreePlayStatus state={{ enabled: true, phase: 'recording' }} onAction={noop} />);
    expect(recording).not.toContain('free-play--finished');
    for (const phase of ['stopping', 'packaging', 'ready']) {
      const html = renderToString(<FreePlayStatus state={{ enabled: true, phase }} onAction={noop} />);
      expect(html).toContain('free-play--finished');
    }
    // a failed stop may leave acquisition running: the light stays
    const failed = renderToString(
      <FreePlayStatus state={{ enabled: true, phase: 'error', error: 'x' }} onAction={noop} />
    );
    expect(failed).not.toContain('free-play--finished');
  });
});
