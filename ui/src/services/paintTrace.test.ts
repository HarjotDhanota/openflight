import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Shot } from '../types/shot';
import {
  DOCUMENT_ID,
  NOT_SHOWN_REASONS,
  afterNextPaint,
  answerClockPing,
  clearPendingPaints,
  clientKindFor,
  createAckScheduler,
  noteShotReceived,
  rendererVersionFrom,
  pendingRevisionsVersion,
  setLiveViewShown,
  subscribePendingRevisions,
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
    const a = shot('ts-1', 120, { launch_angle_vertical: 12.5, launch_angle_vertical_source: 'radar' });
    noteShotReceived(a, 'shot', tag(1), 200);
    acks.commit(a, true);
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
    const a = shot('ts-1', null);
    noteShotReceived(a, 'shot', tag(1), 10);
    acks.commit(a, true);
    frame.flush();
    expect(paints()[0].rendered_core).toEqual({
      ball_speed_mph: null,
      launch_angle_vertical: null,
      launch_angle_vertical_source: null,
    });
  });

  it('acknowledges a revision once: later re-renders of it send nothing', () => {
    const { frame, acks, sent } = harness();
    const a = shot('ts-1', 120);
    noteShotReceived(a, 'shot', tag(1), 10);
    acks.commit(a, true);
    frame.flush();
    acks.commit(a, true);
    frame.flush();
    expect(sent.map(([event]) => event)).toEqual(['trace_paint']);
  });

  it('test_superseded_revision_is_cancelled', () => {
    const { frame, acks, paints, notShown } = harness();
    const a = shot('ts-1', 120);
    const b = shot('ts-1', 121);
    noteShotReceived(a, 'shot', tag(1), 10);
    acks.commit(a, true); // A scheduled
    noteShotReceived(b, 'shot_update', tag(2, true, 'late_update'), 20);
    acks.commit(b, true); // B commits before A's frame
    frame.flush();
    expect(paints()).toHaveLength(1);
    expect(paints()[0]).toMatchObject({ revision: 2, event: 'shot_update', rendered_core: { ball_speed_mph: 121 } });
    expect(notShown()).toEqual([expect.objectContaining({ revision: 1, reason: 'superseded', received_ms: 10 })]);
  });

  it('a revision replaced before it was ever committed is reported superseded', () => {
    const { frame, acks, paints, notShown } = harness();
    const b = shot('ts-1', 121);
    noteShotReceived(shot('ts-1', 120), 'shot', tag(1), 10);
    noteShotReceived(b, 'shot_update', tag(2, true, 'late_update'), 20);
    acks.commit(b, true);
    frame.flush();
    expect(paints().map((p) => p.revision)).toEqual([2]);
    expect(notShown().map((p) => [p.revision, p.reason])).toEqual([[1, 'superseded']]);
  });

  it('a revision whose frame ran before the next revision committed is acknowledged, not superseded', () => {
    const { frame, acks, paints, notShown } = harness();
    const a = shot('ts-1', 120);
    const b = shot('ts-1', 121);
    noteShotReceived(a, 'shot', tag(1), 10);
    acks.commit(a, true);
    noteShotReceived(b, 'shot_update', tag(2, true, 'late_update'), 20); // arrives before A's frame
    frame.flush(); // A's frame: A is what is on screen
    acks.commit(b, true);
    frame.flush();
    expect(paints().map((p) => [p.revision, (p.rendered_core as Record<string, unknown>).ball_speed_mph])).toEqual([
      [1, 120],
      [2, 121],
    ]);
    expect(notShown()).toEqual([]);
  });

  it('an identical re-render does not cancel the scheduled acknowledgement (R-F6)', () => {
    const { frame, acks, paints } = harness();
    const a = shot('ts-1', 120);
    noteShotReceived(a, 'shot', tag(1), 10);
    acks.commit(a, true);
    acks.commit(a, true);
    acks.commit(a, true);
    frame.flush();
    expect(paints()).toHaveLength(1);
    expect(paints()[0]).toMatchObject({ revision: 1, rendered_core: { ball_speed_mph: 120 } });
  });

  it('a status notice moves the revision onto the object now shown, without its own ack (R-F5)', () => {
    const { frame, acks, paints, notShown } = harness();
    const a = shot('ts-1', 120);
    const status = shot('ts-1', 120, { club: 'driver' }); // a new object, same revision values
    noteShotReceived(a, 'shot', tag(1), 10);
    acks.commit(a, true);
    noteShotReceived(status, 'shot_update', tag(1, false, 'status'), 15);
    acks.commit(status, true); // the store now shows the status object
    frame.flush();
    expect(paints().map((p) => [p.revision, p.update_kind, p.received_ms])).toEqual([[1, 'first', 10]]);
    expect(notShown()).toEqual([]);
  });

  it('a status notice whose core values differ from its revision is not bound to it (2c)', () => {
    const { frame, acks, paints, notShown } = harness();
    const a = shot('ts-1', 120);
    const status = shot('ts-1', 125); // claims revision 1 but carries other core values
    noteShotReceived(a, 'shot', tag(1), 10);
    noteShotReceived(status, 'shot_update', tag(1, false, 'status'), 15);
    acks.commit(status, true); // the store now shows the status object
    frame.flush();
    expect(paints()).toEqual([]);
    expect(notShown().map((p) => [p.revision, p.reason])).toEqual([[1, 'not_displayed']]);
  });

  it('a repeat of a revision with other core values is not bound to it either', () => {
    const { frame, acks, paints, notShown } = harness();
    const a = shot('ts-1', 120);
    const repeat = shot('ts-1', 121);
    noteShotReceived(a, 'shot', tag(1), 10);
    noteShotReceived(repeat, 'shot', tag(1), 15);
    acks.commit(repeat, true);
    frame.flush();
    expect(paints()).toEqual([]);
    expect(notShown().map((p) => p.reason)).toEqual(['not_displayed']);
  });

  it('a status notice for a revision already acknowledged, or with revision 0, sends nothing', () => {
    const { frame, acks, sent } = harness();
    const a = shot('ts-1', 120);
    noteShotReceived(a, 'shot', tag(1), 10);
    acks.commit(a, true);
    frame.flush();
    const status = shot('ts-1', 120);
    noteShotReceived(status, 'shot_update', tag(1, false, 'status'), 20);
    acks.commit(status, true);
    const early = shot('ts-2', 99);
    noteShotReceived(early, 'shot_update', tag(0, false, 'status'), 30);
    acks.commit(early, true);
    frame.flush();
    expect(sent.map(([event]) => event)).toEqual(['trace_paint']);
  });

  it('a changed rendered value of the same object reschedules: only the values on screen are acknowledged', () => {
    const { frame, acks, paints } = harness();
    const a = shot('ts-1', 120);
    noteShotReceived(a, 'shot', tag(1), 10);
    acks.commit(a, true);
    (a as { ball_speed_mph: number }).ball_speed_mph = 119;
    acks.commit(a, true);
    frame.flush();
    expect(paints()).toHaveLength(1);
    expect(paints()[0].rendered_core).toMatchObject({ ball_speed_mph: 119 });
  });

  it('test_hidden_or_covered_panel_sends_nothing', () => {
    let visible = true;
    const { frame, acks, paints, notShown } = harness({ visible: () => visible });
    const a = shot('ts-1', 120);
    const b = shot('ts-2', 99);
    noteShotReceived(a, 'shot', tag(1), 10);
    acks.commit(a, false); // covered by an overlay
    frame.flush();
    noteShotReceived(b, 'shot', tag(1), 10);
    acks.commit(b, true);
    visible = false; // document hidden before the frame
    frame.flush();
    expect(paints()).toEqual([]);
    expect(notShown().map((p) => p.reason)).toEqual(['covered', 'document_hidden']);
  });

  it('a covered revision is never acknowledged, even after the overlay closes (R-F6)', () => {
    const { frame, acks, paints, notShown } = harness();
    const a = shot('ts-1', 120);
    noteShotReceived(a, 'shot', tag(1), 10);
    acks.commit(a, false);
    acks.commit(a, true); // picker dismissed
    frame.flush();
    expect(paints()).toEqual([]);
    expect(notShown()).toHaveLength(1);
  });

  it('a scheduled acknowledgement is cancelled when an overlay covers the result before its frame', () => {
    const { frame, acks, paints, notShown } = harness();
    const a = shot('ts-1', 120);
    noteShotReceived(a, 'shot', tag(1), 10);
    acks.commit(a, true);
    acks.commit(a, false);
    frame.flush();
    expect(paints()).toEqual([]);
    expect(notShown().map((p) => p.reason)).toEqual(['covered']);
  });

  it('a hidden document at commit is reported, not acknowledged', () => {
    const { frame, acks, paints, notShown } = harness({ visible: () => false });
    const a = shot('ts-1', 120);
    noteShotReceived(a, 'shot', tag(1), 10);
    acks.commit(a, true);
    frame.flush();
    expect(paints()).toEqual([]);
    expect(notShown().map((p) => p.reason)).toEqual(['document_hidden']);
  });

  it('a result received while the live view was not shown is never acknowledged', () => {
    const { frame, acks, paints, notShown } = harness();
    const a = shot('ts-1', 120);
    setLiveViewShown(false);
    noteShotReceived(a, 'shot', tag(1), 10);
    setLiveViewShown(true);
    acks.commit(a, true);
    frame.flush();
    expect(paints()).toEqual([]);
    expect(notShown().map((p) => p.reason)).toEqual(['live_view_not_shown']);
  });

  it('the live view leaving before the frame withholds the revision, even without an unmount', () => {
    const { frame, acks, paints, notShown } = harness();
    const a = shot('ts-1', 120);
    noteShotReceived(a, 'shot', tag(1), 10);
    acks.commit(a, true);
    setLiveViewShown(false); // the view switched; React has not unmounted the panel yet
    frame.flush();
    expect(paints()).toEqual([]);
    expect(notShown().map((p) => p.reason)).toEqual(['live_view_not_shown']);
  });

  it('a revision scheduled after the live view was left is withheld at its frame', () => {
    const { frame, acks, paints, notShown } = harness();
    const a = shot('ts-1', 120);
    noteShotReceived(a, 'shot', tag(1), 10);
    acks.commit(a, true);
    const b = shot('ts-1', 121);
    noteShotReceived(b, 'shot_update', tag(2, true, 'late_update'), 20); // not in pending's flag sweep
    setLiveViewShown(false);
    frame.flush();
    expect(paints()).toEqual([]);
    expect(notShown().map((p) => [p.revision, p.reason])).toEqual([[1, 'live_view_not_shown']]);
  });

  it('unmounting before the frame reports unmounted_before_paint and never acknowledges', () => {
    const { frame, acks, paints, notShown } = harness();
    const a = shot('ts-1', 120);
    noteShotReceived(a, 'shot', tag(1), 10);
    acks.commit(a, true);
    acks.dispose();
    frame.flush();
    acks.commit(a, true); // nothing is left to acknowledge
    frame.flush();
    expect(paints()).toEqual([]);
    expect(notShown().map((p) => p.reason)).toEqual(['unmounted_before_paint']);
  });

  it('a disposed scheduler stays silent when another scheduler now owns the revision (2b)', () => {
    const first = harness();
    const second = harness();
    const a = shot('ts-1', 120);
    noteShotReceived(a, 'shot', tag(1), 10);
    first.acks.commit(a, true);
    first.acks.dispose(); // key remount: the old panel unmounts...
    second.acks.commit(a, true); // ...and the new one shows the same revision
    first.frame.flush();
    second.frame.flush();
    expect(first.sent).toEqual([]);
    expect(second.paints().map((p) => p.revision)).toEqual([1]);
    expect(second.notShown()).toEqual([]);
  });

  it('notifies subscribers when a revision is noted, so the live panel re-sweeps', () => {
    let calls = 0;
    const unsubscribe = subscribePendingRevisions(() => {
      calls += 1;
    });
    const before = pendingRevisionsVersion();
    noteShotReceived(shot('ts-1', 120), 'shot', tag(1), 10);
    noteShotReceived(shot('ts-1', 120), 'shot', tag(9, false, 'status'), 11); // nothing to rebind
    unsubscribe();
    noteShotReceived(shot('ts-2', 120), 'shot', tag(1), 12);
    expect(calls).toBe(1);
    expect(pendingRevisionsVersion()).toBe(before + 2);
  });

  it('a scheduler that commits again after dispose still acknowledges (StrictMode remount)', () => {
    const { frame, acks, paints, notShown } = harness();
    const a = shot('ts-1', 120);
    noteShotReceived(a, 'shot', tag(1), 10);
    acks.commit(a, true);
    acks.dispose();
    acks.commit(a, true);
    frame.flush();
    expect(paints()).toHaveLength(1);
    expect(notShown()).toEqual([]);
  });

  it('another shot on screen before the frame reports the first not displayed', () => {
    const { frame, acks, paints, notShown } = harness();
    const a = shot('ts-1', 120);
    noteShotReceived(a, 'shot', tag(1), 10);
    acks.commit(a, true);
    acks.commit(shot('ts-0', 90), true); // e.g. a profile switch shows an older, untraced shot
    frame.flush();
    expect(paints()).toEqual([]);
    expect(notShown().map((p) => [p.revision, p.reason])).toEqual([[1, 'not_displayed']]);
  });

  it('a revision the profile filter hides is reported at the next commit and never acknowledged later', () => {
    const { frame, acks, paints, notShown } = harness();
    const shown = shot('ts-0', 90);
    const hidden = shot('ts-1', 120, { profile_id: 'other' });
    noteShotReceived(hidden, 'shot', tag(1), 10);
    acks.commit(shown, true); // the live result still shows this profile's last shot
    frame.flush();
    acks.commit(hidden, true); // seconds later: the user switches to the other profile
    frame.flush();
    expect(paints()).toEqual([]);
    expect(notShown().map((p) => p.reason)).toEqual(['not_displayed']);
  });

  it('a different object for the same shot is not the revision received', () => {
    const { frame, acks, paints, notShown } = harness();
    noteShotReceived(shot('ts-1', 120), 'shot', tag(1), 10);
    acks.commit(shot('ts-1', 120), true); // e.g. the session was reloaded from the server
    frame.flush();
    expect(paints()).toEqual([]);
    expect(notShown().map((p) => p.reason)).toEqual(['not_displayed']);
  });

  it('an untraced shot is never acknowledged', () => {
    const { frame, acks, sent } = harness();
    acks.commit(shot('ts-9', 120), true);
    frame.flush();
    expect(sent).toEqual([]);
  });

  it('the same revision received twice keeps its first receive time, on the object now shown', () => {
    const { frame, acks, paints, notShown } = harness();
    const again = shot('ts-1', 120);
    noteShotReceived(shot('ts-1', 120), 'shot', tag(1), 10);
    noteShotReceived(again, 'shot', tag(1), 30);
    acks.commit(again, true);
    frame.flush();
    expect(paints().map((p) => p.received_ms)).toEqual([10]);
    expect(notShown()).toEqual([]);
  });

  it('a malformed tag or shot is ignored without throwing', () => {
    const { frame, acks, sent } = harness();
    const a = shot('ts-1', 120);
    expect(() =>
      noteShotReceived(a, 'shot', { attempt_id: 't-1', revision: 1 } as unknown as TraceTag, 10)
    ).not.toThrow();
    expect(() => noteShotReceived(a, 'shot', null as unknown as TraceTag, 10)).not.toThrow();
    expect(() => noteShotReceived(undefined, 'shot', tag(1), 10)).not.toThrow();
    acks.commit(a, true);
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
    const a = shot('ts-1', 120);
    const b = shot('ts-2', 120);
    noteShotReceived(a, 'shot', tag(1), 10);
    expect(() => acks.commit(a, false)).not.toThrow();
    noteShotReceived(b, 'shot', tag(1), 10);
    expect(() => acks.commit(b, true)).not.toThrow();
    expect(() => frame.flush()).not.toThrow();
  });

  it('a shot whose fields cannot be read never throws out of commit', () => {
    const { frame, acks, sent } = harness();
    const hostile = {
      timestamp: 'ts-1',
      get ball_speed_mph(): number {
        throw new Error('bad getter');
      },
    } as unknown as Shot;
    noteShotReceived(hostile, 'shot', tag(1), 10);
    expect(() => acks.commit(hostile, true)).not.toThrow();
    frame.flush();
    expect(sent.filter(([event]) => event === 'trace_paint')).toEqual([]);
  });

  it('keeps at most a bounded number of unacknowledged revisions', () => {
    const { frame, acks, paints, notShown } = harness();
    const shots = Array.from({ length: 100 }, (_, i) => shot(`ts-${i}`, 120));
    shots.forEach((s, i) => noteShotReceived(s, 'shot', tag(1), i));
    acks.commit(shots[99], true);
    frame.flush();
    expect(paints().map((p) => p.received_ms)).toEqual([99]);
    expect(notShown()).toHaveLength(63); // the 36 oldest were forgotten unreported
  });

  it('exports exactly the reasons a not-shown report may carry', () => {
    expect([...NOT_SHOWN_REASONS].sort()).toEqual([
      'covered',
      'document_hidden',
      'live_view_not_shown',
      'not_displayed',
      'superseded',
      'unmounted_before_paint',
    ]);
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
