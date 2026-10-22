import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Shot } from '../types/shot';

const ballShot = {
  mode: 'rolling-buffer',
  ball_speed_mph: 145,
  club: '7i',
  timestamp: '2026-08-23T12:00:00Z',
} as Shot;

class FakeAudioNode {
  connect() {
    return this;
  }
}

class FakeOscillator extends FakeAudioNode {
  type = 'sine';
  frequency = { setValueAtTime() {} };
  started = false;

  start() {
    this.started = true;
  }

  stop() {}
}

class FakeGain extends FakeAudioNode {
  gain = {
    setValueAtTime() {},
    exponentialRampToValueAtTime() {},
  };
}

class FakeAudioContext {
  state = 'running';
  currentTime = 0;
  destination = {};
  oscillators: FakeOscillator[] = [];

  resume() {
    return Promise.resolve();
  }

  createOscillator() {
    const oscillator = new FakeOscillator();
    this.oscillators.push(oscillator);
    return oscillator;
  }

  createGain() {
    return new FakeGain();
  }
}

describe('handleShotMessage', () => {
  afterEach(() => {
    vi.resetModules();
    vi.unstubAllGlobals();
  });

  it('plays a beep when a live ball shot is received', async () => {
    const context = new FakeAudioContext();
    vi.stubGlobal('window', {
      AudioContext: function AudioContext() {
        return context;
      },
    } as unknown as Window & typeof globalThis);

    const { handleShotMessage } = await import('./handleShotMessage');
    const { useShotStore } = await import('../stores/useShotStore');
    useShotStore.setState({
      latestShot: null,
      shots: [],
      isNewShot: false,
      shotProcessingPhase: null,
      shotVersion: 0,
    });

    handleShotMessage({
      shot: ballShot,
      stats: {
        shot_count: 1,
        avg_ball_speed: 145,
        max_ball_speed: 145,
        min_ball_speed: 145,
        avg_club_speed: null,
        avg_smash_factor: null,
        avg_carry_est: 0,
      },
    });

    expect(useShotStore.getState().latestShot).toEqual(ballShot);
    expect(context.oscillators.some((oscillator) => oscillator.started)).toBe(true);
  });

  it('updates late hardware metrics without replaying the shot cue', async () => {
    const context = new FakeAudioContext();
    vi.stubGlobal('window', {
      AudioContext: function AudioContext() {
        return context;
      },
    } as unknown as Window & typeof globalThis);

    const { handleShotUpdate } = await import('./handleShotMessage');
    const { useShotStore } = await import('../stores/useShotStore');
    useShotStore.setState({ latestShot: ballShot, shots: [ballShot], shotVersion: 1 });
    const enriched = { ...ballShot, launch_angle_vertical: 17.4 } as Shot;

    handleShotUpdate({
      shot: enriched,
      stats: {
        shot_count: 1,
        avg_ball_speed: 145,
        max_ball_speed: 145,
        min_ball_speed: 145,
        avg_club_speed: null,
        avg_smash_factor: null,
        avg_carry_est: 0,
      },
    });

    expect(useShotStore.getState().latestShot).toEqual(enriched);
    expect(context.oscillators.some((oscillator) => oscillator.started)).toBe(false);
  });

  it('keeps IWR dump feedback active after provisional OPS metrics arrive', async () => {
    vi.stubGlobal('window', {} as Window & typeof globalThis);

    const { handleShotMessage } = await import('./handleShotMessage');
    const { useShotStore } = await import('../stores/useShotStore');
    useShotStore.setState({
      latestShot: null,
      shots: [],
      shotProcessingPhase: 'calculating',
      shotProcessingShotTimestamp: null,
    });

    handleShotMessage({
      shot: ballShot,
      stats: {
        shot_count: 1,
        avg_ball_speed: 145,
        max_ball_speed: 145,
        min_ball_speed: 145,
        avg_club_speed: null,
        avg_smash_factor: null,
        avg_carry_est: 0,
      },
      pending: { iwr6843: true },
    });

    expect(useShotStore.getState().latestShot).toEqual(ballShot);
    expect(useShotStore.getState().shotProcessingPhase).toBe('iwr_dump');
    expect(useShotStore.getState().shotProcessingShotTimestamp).toBe(ballShot.timestamp);
  });

  it('keeps camera processing feedback active after provisional OPS metrics arrive', async () => {
    vi.stubGlobal('window', {} as Window & typeof globalThis);

    const { handleShotMessage } = await import('./handleShotMessage');
    const { useShotStore } = await import('../stores/useShotStore');

    handleShotMessage({
      shot: ballShot,
      stats: {
        shot_count: 1,
        avg_ball_speed: 145,
        max_ball_speed: 145,
        min_ball_speed: 145,
        avg_club_speed: null,
        avg_smash_factor: null,
        avg_carry_est: 0,
      },
      pending: { camera: true },
    });

    expect(useShotStore.getState().shotProcessingPhase).toBe('camera_processing');
    expect(useShotStore.getState().shotProcessingShotTimestamp).toBe(ballShot.timestamp);
  });

  it('clears hardware feedback immediately when enrichment is skipped', async () => {
    vi.stubGlobal('window', {} as Window & typeof globalThis);

    const { handleShotMessage, handleShotUpdate } = await import('./handleShotMessage');
    const { useShotStore } = await import('../stores/useShotStore');
    const stats = {
      shot_count: 1,
      avg_ball_speed: 145,
      max_ball_speed: 145,
      min_ball_speed: 145,
      avg_club_speed: null,
      avg_smash_factor: null,
      avg_carry_est: 0,
    };

    handleShotMessage({ shot: ballShot, stats, pending: { iwr6843: true } });
    handleShotUpdate({
      shot: ballShot,
      stats,
      pending: {},
      enrichment: {
        status: 'skipped',
        reason: 'enrichment_backlog_full',
        hardware: ['iwr6843'],
      },
    });

    const state = useShotStore.getState();
    expect(state.shotProcessingPhase).toBeNull();
    expect(state.shotProcessingShotTimestamp).toBeNull();
    expect(state.shots).toEqual([ballShot]);
  });

  it.each(['older', 'unknown', 'capturing', 'calculating'] as const)(
    'keeps the active processing indicator when a pending update is %s',
    async (scenario) => {
      const { handleShotUpdate } = await import('./handleShotMessage');
      const { useShotStore } = await import('../stores/useShotStore');
      const latest = { ...ballShot, timestamp: '2026-08-23T12:00:10Z' };
      useShotStore.getState().setShots([ballShot, latest]);
      if (scenario === 'capturing' || scenario === 'calculating') {
        useShotStore.getState().startShotProcessing(scenario);
      } else {
        useShotStore.getState().startShotProcessing('camera_processing', latest.timestamp);
      }
      const before = useShotStore.getState();
      const updated = {
        ...(scenario === 'older' ? ballShot : latest),
        ...(scenario === 'unknown' ? { timestamp: '2026-08-23T12:00:20Z' } : {}),
        ball_speed_mph: 155,
      };

      handleShotUpdate({
        shot: updated,
        stats: {
          shot_count: 2,
          avg_ball_speed: 150,
          max_ball_speed: 155,
          min_ball_speed: 145,
          avg_club_speed: null,
          avg_smash_factor: null,
          avg_carry_est: 0,
        },
        pending: { iwr6843: true },
        enrichment: { status: 'pending', reason: 'queue_full', hardware: ['iwr6843'] },
      });

      const after = useShotStore.getState();
      expect(after.shotProcessingPhase).toBe(before.shotProcessingPhase);
      expect(after.shotProcessingShotTimestamp).toBe(before.shotProcessingShotTimestamp);
      const stored = after.shots.find((shot) => shot.timestamp === updated.timestamp);
      expect(stored).toEqual(scenario === 'unknown' ? undefined : updated);
      expect(after.latestShot?.timestamp).toBe(latest.timestamp);
      after.clearShots();
    }
  );

  it('keeps hardware feedback when enrichment is deferred, not skipped', async () => {
    vi.stubGlobal('window', {} as Window & typeof globalThis);

    const { handleShotMessage, handleShotUpdate } = await import('./handleShotMessage');
    const { useShotStore } = await import('../stores/useShotStore');
    const stats = {
      shot_count: 1,
      avg_ball_speed: 145,
      max_ball_speed: 145,
      min_ball_speed: 145,
      avg_club_speed: null,
      avg_smash_factor: null,
      avg_carry_est: 0,
    };

    handleShotMessage({ shot: ballShot, stats, pending: { iwr6843: true, camera: true } });
    handleShotUpdate({
      shot: ballShot,
      stats,
      pending: { iwr6843: true, camera: true },
      enrichment: {
        status: 'pending',
        reason: 'queue_full',
        hardware: ['iwr6843', 'camera'],
      },
    });

    const state = useShotStore.getState();
    expect(state.shotProcessingPhase).toBe('hardware_enrichment');
    expect(state.shotProcessingShotTimestamp).toBe(ballShot.timestamp);
    expect(state.shots).toEqual([ballShot]);
  });
});
