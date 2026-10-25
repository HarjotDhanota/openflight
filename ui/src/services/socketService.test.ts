import { beforeEach, describe, expect, it, vi } from 'vitest';

const order: string[] = [];
const handlers: Record<string, (data: unknown) => void> = {};
const emitted: Array<[string, unknown]> = [];

vi.mock('socket.io-client', () => ({
  io: () => ({
    on: (event: string, handler: (data: unknown) => void) => {
      handlers[event] = handler;
    },
    emit: (event: string, payload: unknown) => emitted.push([event, payload]),
    close: () => {},
  }),
}));
vi.mock('./handleShotMessage', () => ({
  handleShotMessage: () => order.push('handleShotMessage'),
  handleShotUpdate: () => order.push('handleShotUpdate'),
}));
vi.mock('./paintTrace', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./paintTrace')>();
  return {
    ...actual,
    noteShotReceived: vi.fn((...args: unknown[]) => {
      order.push(`noteShotReceived:${String(args[1])}`);
    }),
  };
});

const { socketService } = await import('./socketService');
const { noteShotReceived } = await import('./paintTrace');

const trace = {
  attempt_id: 't-00003',
  revision: 1,
  final: false,
  update_kind: 'first',
  core_eligible: false,
  core_fields: ['ball_speed_mph'],
};

describe('socketService trace wiring', () => {
  beforeEach(() => {
    order.length = 0;
    emitted.length = 0;
    vi.mocked(noteShotReceived).mockClear();
    socketService.disconnect();
    socketService.connect();
  });

  it('notes the receive time of a shot before handling it', () => {
    const message = { shot: { timestamp: 'ts-1' }, stats: {}, trace };
    handlers.shot(message);
    handlers.shot_update({ ...message, trace: { ...trace, revision: 2, update_kind: 'late_update' } });

    expect(order).toEqual([
      'noteShotReceived:shot',
      'handleShotMessage',
      'noteShotReceived:shot_update',
      'handleShotUpdate',
    ]);
    // The message's own shot object: the one the store keeps and the live panel renders.
    expect(vi.mocked(noteShotReceived).mock.calls[0][0]).toBe(message.shot);
    expect(vi.mocked(noteShotReceived).mock.calls[0].slice(1, 3)).toEqual(['shot', trace]);
  });

  it('still handles an untraced shot', () => {
    const message = { shot: { timestamp: 'ts-2' }, stats: {} };
    handlers.shot(message);

    expect(order).toContain('handleShotMessage');
    expect(vi.mocked(noteShotReceived).mock.calls[0].slice(0, 3)).toEqual([message.shot, 'shot', undefined]);
  });

  it('asks for a clock ping as soon as it (re)connects', () => {
    handlers.connect(undefined);

    expect(emitted.map(([event]) => event)).toContain('trace_clock_hello');
  });

  it('answers a clock ping on the same socket at once', () => {
    handlers.trace_clock_ping({ ping_seq: 4 });

    expect(emitted).toHaveLength(1);
    expect(emitted[0][0]).toBe('trace_clock_pong');
    expect(emitted[0][1]).toMatchObject({ ping_seq: 4 });
  });

  it('a trace report while disconnected is dropped without throwing', () => {
    socketService.disconnect();

    expect(() => socketService.emit('trace_paint', {})).not.toThrow();
  });
});

describe('socketService air conditions', () => {
  beforeEach(() => {
    socketService.disconnect();
    socketService.connect();
  });

  it('keeps the air the server sends for the Conditions panel', async () => {
    const { useAirStore } = await import('../stores/useAirStore');
    const conditions = {
      air_density_kg_m3: 1.225,
      source: 'isa_default',
      temperature_c: null,
      pressure_hpa: null,
      humidity_pct: null,
      measured_at: null,
      age_s: null,
      reason: 'no BME280 on I2C bus 1',
      density_altitude_ft: null,
    };

    handlers.air_conditions(conditions);

    expect(useAirStore.getState().conditions).toEqual(conditions);
  });
});
