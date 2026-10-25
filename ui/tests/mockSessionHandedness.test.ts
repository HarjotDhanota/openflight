import { describe, expect, it } from 'vitest';
import { MockSession } from '../mock-server/session';

describe('mock server: the golfer hand, as the kiosk serves it', () => {
  it('is right-handed by default', () => {
    expect(new MockSession().handedness()).toEqual({ handed: 'RH', source: 'default' });
  });

  it("keeps a tap's hand with the active profile, as the kiosk does", () => {
    const session = new MockSession();

    expect(session.setHandedness('LH')).toBe(true);

    expect(session.handedness()).toEqual({ handed: 'LH', source: 'profile' });
    expect(session.activeProfile.settings.handed).toBe('LH');
  });

  it('refuses anything but RH or LH and changes nothing', () => {
    const session = new MockSession();

    expect(session.setHandedness('left')).toBe(false);
    expect(session.setHandedness(undefined)).toBe(false);
    expect(session.handedness()).toEqual({ handed: 'RH', source: 'default' });
  });

  it('gives each profile its own hand', () => {
    const session = new MockSession();
    const first = session.activeProfile.id;
    session.setHandedness('LH');
    session.addProfile('Second');

    expect(session.handedness().handed).toBe('RH');
    session.setActiveProfile(first);
    expect(session.handedness().handed).toBe('LH');
  });

  it('stamps each shot with the hand it was hit with', () => {
    const session = new MockSession();
    const { shot: before } = session.simulateShot();
    session.setHandedness('LH');
    const { shot: after } = session.simulateShot();

    expect([before.handed, after.handed]).toEqual(['RH', 'LH']);
    expect(after.handed_source).toBe('profile');
  });
});
