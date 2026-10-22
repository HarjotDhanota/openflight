import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { FreePlayActionsView, FreePlayStatus } from './FreePlayControls';
import { openTesterPage } from '../utils/testerPage';

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
  it('says where to send the finished package, and offers Done', () => {
    const html = renderToString(
      <FreePlayStatus
        state={{ enabled: true, phase: 'ready', bundle: { name: 'session.zip', path: '/home/someone/captures/session.zip' } }}
        onAction={noop}
      />
    );
    // decision 3 (19 Oct): the tester sends it from the tester page's step 8
    expect(html).toContain('Package ready: session.zip · send it from the tester page (step 8)');
    // the unit's own folders are never shown on the scoreboard
    expect(html).not.toContain('/home/someone');
    expect(html).toContain('>Done</button>');
    expect(html).not.toContain('disabled');
  });
  it('gives Stop & package an active look while recording', () => {
    const html = renderToString(<FreePlayStatus state={{ enabled: true, phase: 'recording' }} onAction={noop} />);
    expect(html).toMatch(/<button[^>]*class="free-play__stop"[^>]*>Stop &amp; package<\/button>/);
  });
  it('names the bundle even when only its name is known', () => {
    const html = renderToString(
      <FreePlayStatus state={{ enabled: true, phase: 'ready', bundle: { name: 'session.zip' } }} onAction={noop} />
    );
    expect(html).toContain('Package ready: session.zip · send it from the tester page (step 8)');
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

describe('Free play exposure study line', () => {
  it('shows the ladder progress while recording', () => {
    const html = renderToString(
      <FreePlayStatus
        state={{
          enabled: true,
          phase: 'recording',
          exposure_study: { phase: 'ladder', text: 'Exposure study: 150 µs · swing 3 of 5 · setting 3 of 6' },
        }}
        onAction={noop}
      />
    );
    expect(html).toContain('Exposure study: 150 µs · swing 3 of 5 · setting 3 of 6');
    expect(html).toContain('free-play__study');
  });
  it('says automatic exposure once the ladder is done', () => {
    const html = renderToString(
      <FreePlayStatus
        state={{ enabled: true, phase: 'recording', exposure_study: { phase: 'automatic', text: 'Automatic exposure' } }}
        onAction={noop}
      />
    );
    expect(html).toContain('Automatic exposure');
  });
  it('drops the line once acquisition stops', () => {
    const html = renderToString(
      <FreePlayStatus
        state={{ enabled: true, phase: 'packaging', exposure_study: { phase: 'ladder', text: 'Exposure study: 150 µs' } }}
        onAction={noop}
      />
    );
    expect(html).not.toContain('Exposure study');
  });
  it('shows nothing for a run without a study', () => {
    const html = renderToString(
      <FreePlayStatus state={{ enabled: true, phase: 'recording', exposure_study: null }} onAction={noop} />
    );
    expect(html).not.toContain('free-play__study');
  });
});

describe('Setup and the face photo on the scoreboard', () => {
  const recording = { enabled: true, phase: 'recording', tester_url: 'http://pi.local:8765/' };
  const view = (state: Parameters<typeof FreePlayActionsView>[0]['state']) =>
    renderToString(<FreePlayActionsView state={state} onSetup={noop} onPhoto={noop} onResume={noop} />);

  it('offers Setup and the face photo while free play records', () => {
    const html = view(recording);
    expect(html).toContain('>Setup</button>');
    expect(html).toContain('>Face photo</button>');
    expect(html).toContain('aria-label="Photograph the face"');
    expect(html).not.toContain('Resume');
  });
  it('offers Resume instead while free play is paused', () => {
    const html = view({ ...recording, paused: { cause: 'photo' } });
    expect(html).toContain('>Resume</button>');
    expect(html).not.toContain('Face photo');
    expect(html).toContain('>Setup</button>');
  });
  it('keeps Setup once recording stops, and shows nothing outside free play', () => {
    const html = view({ ...recording, phase: 'ready' });
    expect(html).toContain('>Setup</button>');
    expect(html).not.toContain('Face photo');
    expect(view({ enabled: false })).toBe('');
  });
});

describe('Bringing the tester page back', () => {
  function fakeWindow(opener: { closed: boolean; focus: () => void } | null) {
    const assigned: string[] = [];
    return {
      assigned,
      win: { opener, location: { assign: (url: string) => assigned.push(url) } } as unknown as Window,
    };
  }
  it('focuses the tester page that opened this window', () => {
    let focused = 0;
    const { assigned, win } = fakeWindow({ closed: false, focus: () => (focused += 1) });
    expect(openTesterPage('http://pi.local:8765/', '', win)).toBe(true);
    expect(focused).toBe(1);
    expect(assigned).toEqual([]);
  });
  it("goes to the tester page in this window on the unit's own screen", () => {
    const { assigned, win } = fakeWindow(null);
    expect(openTesterPage('http://pi.local:8765/', '#free-play-photo', win)).toBe(true);
    expect(assigned).toEqual(['http://pi.local:8765/#free-play-photo']);
  });
  it('goes there too when the opener was closed', () => {
    const { assigned, win } = fakeWindow({ closed: true, focus: noop });
    openTesterPage('http://pi.local:8765/', '', win);
    expect(assigned).toEqual(['http://pi.local:8765/']);
  });
  it('does nothing without an address', () => {
    const { assigned, win } = fakeWindow(null);
    expect(openTesterPage(null, '', win)).toBe(false);
    expect(assigned).toEqual([]);
  });
});
