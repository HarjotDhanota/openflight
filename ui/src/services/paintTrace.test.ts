import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Shot } from '../types/shot';
import {
  DOCUMENT_ID,
  afterNextPaint,
  answerClockPing,
  clearPendingPaints,
  clientKindFor,
  createAckScheduler,
  noteShotReceived,
  rendererVersionFrom,
  setLiveViewShown,
  type TraceTag,
} from './paintTrace';

const CORE_FIELDS = ['ball_speed_mph', 'launch_angle_vertical', 'launch_angle_vertical_source'];

const tag = (revision: number, final = false, updateKind: TraceTag['update_kind'] = 'first'): TraceTag => ({
  attempt_id: 't-00001',
  revision,
  final,
  update_kind: updateKind,
  core_eligible: false,
  core_fields: CORE_FIELDS,
});
const shot = (ts: string, mph: number | null, extra: Partial<Shot> = {}) =>
  ({ timestamp: ts, ball_speed_mph: mph, ...extra }) as Shot;

type Sent = Array<[string, Record<string, unknown>]>;

/** A manual scheduler: callbacks run only when flush() is called (the next frame). */
function manual() {
  const queue: Array<() => void> = [];
  return { schedule: (cb: () => void) => void queue.push(cb), flush: () => queue.splice(0).forEach((cb) => cb()) };
}

function harness(opts: { now?: () => number; visible?: () => boolean } = {}) {
  const sent: Sent = [];
  const frame = manual();
  const acks = createAckScheduler(
    (event, payload) => sent.push([event, payload as Record<string, unknown>]),
    frame.schedule,
    opts.now ?? (() => 250),
    opts.visible ?? (() => true)
  );
  const paints = () => sent.filter(([event]) => event === 'trace_paint').map(([, p]) => p);
  const notShown = () => sent.filter(([event]) => event === 'trace_not_shown').map(([, p]) => p);
  return { sent, frame, acks, paints, notShown };
}

describe('post-render acknowledgement', () => {
  beforeEach(() => setLiveViewShown(true));
  afterEach(() => clearPendingPaints());

  it('acknowledges the committed revision with its rendered core values and the document identity', () => {
    const { frame, acks, paints } = harness();
    noteShotReceived('ts-1', 'shot', tag(1), 200);
    acks.commit(shot('ts-1', 120, { launch_angle_vertical: 12.5, launch_angle_vertical_source: 'radar' }), true);
    expect(paints()).toEqual([]); // nothing before the frame
    frame.flush();
    expect(paints()).toHaveLength(1);
    expect(paints()[0]).toMatchObject({
      attempt_id: 't-00001',
      revision: 1,
      final: false,
      update_kind: 'first',
      event: 'shot',
      received_ms: 200,
      painted_ms: 250,
      rendered_core: { ball_speed_mph: 120, launch_angle_vertical: 12.5, launch_angle_vertical_source: 'radar' },
      semantics: 'post_render_task',
      document_id: DOCUMENT_ID,
    });
    expect(typeof paints()[0].time_origin_ms).toBe('number');
    expect(paints()[0]).toHaveProperty('client_kind');
    expect(paints()[0]).toHaveProperty('user_agent');
    expect(paints()[0]).toHaveProperty('renderer_version');
  });

  it('echoes a missing or null core value as null', () => {
    const { frame, acks, paints } = harness();
    noteShotReceived('ts-1', 'shot', tag(1), 10);
    acks.commit(shot('ts-1', null), true);
    frame.flush();
    expect(paints()[0].rendered_core).toEqual({
      ball_speed_mph: null,
      launch_angle_vertical: null,
      launch_angle_vertical_source: null,
    });
  });

  it('acknowledges a revision once: later re-renders of it send nothing', () => {
    const { frame, acks, paints } = harness();
    noteShotReceived('ts-1', 'shot', tag(1), 10);
    acks.commit(shot('ts-1', 120), true);
    frame.flush();
    acks.commit(shot('ts-1', 120), true);
    frame.flush();
    expect(paints()).toHaveLength(1);
  });

  it('test_superseded_revision_is_cancelled', () => {
    const { frame, acks, paints, notShown } = harness();
    noteShotReceived('ts-1', 'shot', tag(1), 10);
    acks.commit(shot('ts-1', 120), true); // A scheduled
    noteShotReceived('ts-1', 'shot_update', tag(2, true, 'late_update'), 20);
    acks.commit(shot('ts-1', 121), true); // B commits before A's frame
    frame.flush();
    expect(paints()).toHaveLength(1);
    expect(paints()[0]).toMatchObject({ revision: 2, event: 'shot_update', rendered_core: { ball_speed_mph: 121 } });
    expect(notShown()).toEqual([expect.objectContaining({ revision: 1, reason: 'superseded', received_ms: 10 })]);
  });

  it('a revision replaced before it was ever committed is reported superseded', () => {
    const { frame, acks, paints, notShown } = harness();
    noteShotReceived('ts-1', 'shot', tag(1), 10);
    noteShotReceived('ts-1', 'shot_update', tag(2, true, 'late_update'), 20);
    acks.commit(shot('ts-1', 121), true);
    frame.flush();
    expect(paints().map((p) => p.revision)).toEqual([2]);
    expect(notShown().map((p) => [p.revision, p.reason])).toEqual([[1, 'superseded']]);
  });

  it('a revision whose frame ran before the next revision committed is acknowledged, not superseded', () => {
    const { frame, acks, paints, notShown } = harness();
    noteShotReceived('ts-1', 'shot', tag(1), 10);
    acks.commit(shot('ts-1', 120), true);
    noteShotReceived('ts-1', 'shot_update', tag(2, true, 'late_update'), 20); // arrives before A's frame
    frame.flush(); // A's frame: A is what is on screen
    acks.commit(shot('ts-1', 121), true);
    frame.flush();
    expect(paints().map((p) => [p.revision, (p.rendered_core as Record<string, unknown>).ball_speed_mph])).toEqual([
      [1, 120],
      [2, 121],
    ]);
    expect(notShown()).toEqual([]);
  });

  it('an identical re-render does not cancel the scheduled acknowledgement (R-F6)', () => {
    const { frame, acks, paints } = harness();
    noteShotReceived('ts-1', 'shot', tag(1), 10);
    acks.commit(shot('ts-1', 120), true);
    acks.commit(shot('ts-1', 120), true); // a new object with the same values
    acks.commit(shot('ts-1', 120, { club: 'driver' }), true); // a non-core change
    frame.flush();
    expect(paints()).toHaveLength(1);
    expect(paints()[0]).toMatchObject({ revision: 1, rendered_core: { ball_speed_mph: 120 } });
  });

  it('a changed rendered value reschedules: only the values on screen are acknowledged', () => {
    const { frame, acks, paints } = harness();
    noteShotReceived('ts-1', 'shot', tag(1), 10);
    acks.commit(shot('ts-1', 120), true);
    acks.commit(shot('ts-1', 119), true);
    frame.flush();
    expect(paints()).toHaveLength(1);
    expect(paints()[0].rendered_core).toMatchObject({ ball_speed_mph: 119 });
  });

  it('test_hidden_or_covered_panel_sends_nothing', () => {
    let visible = true;
    const { frame, acks, paints, notShown } = harness({ visible: () => visible });
    noteShotReceived('ts-1', 'shot', tag(1), 10);
    acks.commit(shot('ts-1', 120), false); // covered by an overlay
    frame.flush();
    noteShotReceived('ts-2', 'shot', tag(1), 10);
    acks.commit(shot('ts-2', 99), true);
    visible = false; // document hidden before the frame
    frame.flush();
    expect(paints()).toEqual([]);
    expect(notShown().map((p) => p.reason)).toEqual(['covered', 'document_hidden']);
  });

  it('a covered revision is never acknowledged, even after the overlay closes (R-F6)', () => {
    const { frame, acks, paints, notShown } = harness();
    noteShotReceived('ts-1', 'shot', tag(1), 10);
    acks.commit(shot('ts-1', 120), false);
    acks.commit(shot('ts-1', 120), true); // picker dismissed
    frame.flush();
    expect(paints()).toEqual([]);
    expect(notShown()).toHaveLength(1);
  });

  it('a scheduled acknowledgement is cancelled when an overlay covers the result before its frame', () => {
    const { frame, acks, paints, notShown } = harness();
    noteShotReceived('ts-1', 'shot', tag(1), 10);
    acks.commit(shot('ts-1', 120), true);
    acks.commit(shot('ts-1', 120), false);
    frame.flush();
    expect(paints()).toEqual([]);
    expect(notShown().map((p) => p.reason)).toEqual(['covered']);
  });

  it('a hidden document at commit is reported, not acknowledged', () => {
    const { frame, acks, paints, notShown } = harness({ visible: () => false });
    noteShotReceived('ts-1', 'shot', tag(1), 10);
    acks.commit(shot('ts-1', 120), true);
    frame.flush();
    expect(paints()).toEqual([]);
    expect(notShown().map((p) => p.reason)).toEqual(['document_hidden']);
  });

  it('a result received while the live view was not shown is never acknowledged', () => {
    const { frame, acks, paints, notShown } = harness();
    setLiveViewShown(false);
    noteShotReceived('ts-1', 'shot', tag(1), 10);
    setLiveViewShown(true);
    acks.commit(shot('ts-1', 120), true);
    frame.flush();
    expect(paints()).toEqual([]);
    expect(notShown().map((p) => p.reason)).toEqual(['live_view_not_shown']);
  });

  it('leaving the live view before the frame withholds the revision for good', () => {
    const first = harness();
    noteShotReceived('ts-1', 'shot', tag(1), 10);
    first.acks.commit(shot('ts-1', 120), true);
    first.acks.dispose(); // the live panel unmounts (view switch)
    setLiveViewShown(false);
    first.frame.flush();
    setLiveViewShown(true);
    const second = harness(); // the live panel mounts again later
    second.acks.commit(shot('ts-1', 120), true);
    second.frame.flush();
    expect([...first.paints(), ...second.paints()]).toEqual([]);
    expect(second.notShown().map((p) => p.reason)).toEqual(['live_view_not_shown']);
  });

  it('dispose cancels a scheduled acknowledgement (unmount, view switch)', () => {
    const { frame, acks, paints } = harness();
    noteShotReceived('ts-1', 'shot', tag(1), 10);
    acks.commit(shot('ts-1', 120), true);
    acks.dispose();
    frame.flush();
    expect(paints()).toEqual([]);
  });

  it('a scheduler that commits again after dispose still acknowledges (StrictMode remount)', () => {
    const { frame, acks, paints } = harness();
    noteShotReceived('ts-1', 'shot', tag(1), 10);
    acks.commit(shot('ts-1', 120), true);
    acks.dispose();
    acks.commit(shot('ts-1', 120), true);
    frame.flush();
    expect(paints()).toHaveLength(1);
  });

  it('another shot replacing the result before its frame reports the first superseded', () => {
    const { frame, acks, paints, notShown } = harness();
    noteShotReceived('ts-1', 'shot', tag(1), 10);
    acks.commit(shot('ts-1', 120), true);
    acks.commit(shot('ts-0', 90), true); // e.g. a profile switch shows an older, untraced shot
    frame.flush();
    expect(paints()).toEqual([]);
    expect(notShown().map((p) => [p.revision, p.reason])).toEqual([[1, 'superseded']]);
  });

  it('an untraced shot is never acknowledged', () => {
    const { frame, acks, sent } = harness();
    acks.commit(shot('ts-9', 120), true);
    frame.flush();
    expect(sent).toEqual([]);
  });

  it('a status notice is never acknowledged and never displaces the shot revision (R-F5)', () => {
    const { frame, acks, paints } = harness();
    noteShotReceived('ts-1', 'shot', tag(1), 10);
    noteShotReceived('ts-1', 'shot_update', tag(1, false, 'status'), 15);
    acks.commit(shot('ts-1', 120), true);
    frame.flush();
    expect(paints().map((p) => [p.revision, p.update_kind, p.received_ms])).toEqual([[1, 'first', 10]]);
    noteShotReceived('ts-2', 'shot_update', tag(0, false, 'status'), 20);
    acks.commit(shot('ts-2', 99), true);
    frame.flush();
    expect(paints()).toHaveLength(1);
  });

  it('the same revision received twice keeps its first receive time and is not superseded', () => {
    const { frame, acks, paints, notShown } = harness();
    noteShotReceived('ts-1', 'shot', tag(1), 10);
    noteShotReceived('ts-1', 'shot', tag(1), 30);
    acks.commit(shot('ts-1', 120), true);
    frame.flush();
    expect(paints().map((p) => p.received_ms)).toEqual([10]);
    expect(notShown()).toEqual([]);
  });

  it('a malformed tag is ignored without throwing', () => {
    const { frame, acks, sent } = harness();
    expect(() =>
      noteShotReceived('ts-1', 'shot', { attempt_id: 't-1', revision: 1 } as unknown as TraceTag, 10)
    ).not.toThrow();
    expect(() => noteShotReceived('ts-1', 'shot', null as unknown as TraceTag, 10)).not.toThrow();
    acks.commit(shot('ts-1', 120), true);
    frame.flush();
    expect(sent).toEqual([]);
  });

  it('a failing emit never throws out of the scheduler', () => {
    const frame = manual();
    const acks = createAckScheduler(
      () => {
        throw new Error('socket gone');
      },
      frame.schedule,
      () => 1,
      () => true
    );
    noteShotReceived('ts-1', 'shot', tag(1), 10);
    noteShotReceived('ts-2', 'shot', tag(1), 10);
    expect(() => acks.commit(shot('ts-1', 120), false)).not.toThrow();
    expect(() => acks.commit(shot('ts-2', 120), true)).not.toThrow();
    expect(() => frame.flush()).not.toThrow();
  });

  it('a shot whose fields cannot be read never throws out of commit', () => {
    const { frame, acks, sent } = harness();
    noteShotReceived('ts-1', 'shot', tag(1), 10);
    const hostile = {
      timestamp: 'ts-1',
      get ball_speed_mph(): number {
        throw new Error('bad getter');
      },
    } as unknown as Shot;
    expect(() => acks.commit(hostile, true)).not.toThrow();
    frame.flush();
    expect(sent.filter(([event]) => event === 'trace_paint')).toEqual([]);
  });

  it('keeps at most a bounded number of unacknowledged revisions', () => {
    const { frame, acks, paints } = harness();
    for (let i = 0; i < 100; i += 1) noteShotReceived(`ts-${i}`, 'shot', tag(1), i);
    acks.commit(shot('ts-0', 120), true); // the oldest was forgotten
    frame.flush();
    acks.commit(shot('ts-99', 120), true);
    frame.flush();
    expect(paints().map((p) => p.received_ms)).toEqual([99]);
  });
});

describe('afterNextPaint', () => {
  afterEach(() => vi.unstubAllGlobals());

  function stubFrame() {
    const frames: Array<() => void> = [];
    vi.stubGlobal('requestAnimationFrame', (cb: () => void) => frames.push(cb));
    return () => frames.splice(0).forEach((cb) => cb());
  }
  const nextTask = () => new Promise((resolve) => setTimeout(resolve, 0));

  it('runs the callback in a task after the frame callback', async () => {
    const runFrame = stubFrame();
    const calls: string[] = [];
    afterNextPaint(() => calls.push('after'));
    expect(calls).toEqual([]);
    runFrame();
    expect(calls).toEqual([]); // not inside the frame callback
    await nextTask();
    expect(calls).toEqual(['after']);
  });

  it('checks the revision is still current at the frame and again after it', async () => {
    const runFrame = stubFrame();
    const calls: string[] = [];
    let current = false;
    afterNextPaint(
      () => calls.push('a'),
      () => current
    ); // stale at the frame
    runFrame();
    await nextTask();
    current = true;
    afterNextPaint(
      () => calls.push('b'),
      () => current
    );
    runFrame();
    current = false; // stale by the post-frame task
    await nextTask();
    expect(calls).toEqual([]);
  });

  it('does nothing where there is no frame scheduler', () => {
    vi.stubGlobal('requestAnimationFrame', undefined);
    expect(() => afterNextPaint(() => {})).not.toThrow();
  });
});

describe('document identity and clock pings', () => {
  it('answers a clock ping with its own time and document', () => {
    const sent: Sent = [];
    answerClockPing({ ping_seq: 7 }, (e, p) => sent.push([e, p as Record<string, unknown>]), 42.5);
    expect(sent[0][0]).toBe('trace_clock_pong');
    expect(sent[0][1]).toMatchObject({ ping_seq: 7, browser_ms: 42.5, document_id: DOCUMENT_ID });
    expect(sent[0][1]).not.toHaveProperty('seq'); // R-F1: the recorder owns seq
    expect(typeof sent[0][1].time_origin_ms).toBe('number');
  });

  it('ignores a malformed ping and never throws on a failing emit', () => {
    const sent: Sent = [];
    answerClockPing({} as { ping_seq: number }, (e, p) => sent.push([e, p as Record<string, unknown>]), 1);
    answerClockPing({ ping_seq: 1.5 }, (e, p) => sent.push([e, p as Record<string, unknown>]), 1);
    expect(sent).toEqual([]);
    expect(() =>
      answerClockPing(
        { ping_seq: 1 },
        () => {
          throw new Error('socket gone');
        },
        1
      )
    ).not.toThrow();
  });

  it('names the kiosk only when the page is served from this machine', () => {
    expect(clientKindFor('localhost')).toBe('local_kiosk');
    expect(clientKindFor('127.0.0.1')).toBe('local_kiosk');
    expect(clientKindFor('[::1]')).toBe('local_kiosk');
    expect(clientKindFor('openflight.local')).toBe('remote');
    expect(clientKindFor(undefined)).toBe('remote');
  });

  it('reads the renderer version from the user agent', () => {
    const chromium =
      'Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.164 Safari/537.36';
    expect(rendererVersionFrom(chromium)).toBe('126.0.6478.164');
    expect(rendererVersionFrom('Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0')).toBeNull();
    expect(rendererVersionFrom(undefined)).toBeNull();
  });
});
