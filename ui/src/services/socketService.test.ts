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
    expect(vi.mocked(noteShotReceived).mock.calls[0].slice(0, 3)).toEqual(['ts-1', 'shot', trace]);
  });

  it('still handles an untraced shot', () => {
    handlers.shot({ shot: { timestamp: 'ts-2' }, stats: {} });

    expect(order).toContain('handleShotMessage');
    expect(vi.mocked(noteShotReceived).mock.calls[0].slice(0, 3)).toEqual(['ts-2', 'shot', undefined]);
  });

  it('answers a clock ping on the same socket at once', () => {
    handlers.trace_clock_ping({ ping_seq: 4 });

    expect(emitted).toHaveLength(1);
    expect(emitted[0][0]).toBe('trace_clock_pong');
    expect(emitted[0][1]).toMatchObject({ ping_seq: 4 });
  });

  it('a trace report while disconnected is dropped without throwing', () => {
    socketService.disconnect();

    expect(() => socketService.emitTrace('trace_paint', {})).not.toThrow();
  });
});
