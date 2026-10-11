/**
 * Stage 1b Pi trace, browser side: when each traced shot revision was received, and a
 * post-render acknowledgement from the live result component that shows it.
 *
 * `post_render_task` = a requestAnimationFrame callback (before that frame's paint), then
 * a MessageChannel task (after the frame is produced). It is a post-render PROXY, not
 * proof of physical frame presentation: rendering can be skipped, and compositing and
 * presentation are separate steps (HTML rendering algorithm; Chromium cc).
 *
 * Owner rule: none of this may delay, hide or drop a rendered result. Every entry point
 * is guarded, so a trace failure costs only the trace.
 */
import type { Shot } from '../types/shot';

export interface TraceTag {
  attempt_id: string;
  revision: number;
  final: boolean;
  /** `status` notices are never acknowledged as a result paint (R-F5). */
  update_kind: 'first' | 'late_update' | 'status';
  core_eligible: boolean;
  core_fields: string[];
}

/**
 * Why a shot revision was not acknowledged. The server accepts exactly this list
 * (trace_routes.NOT_SHOWN_REASONS; a Python test reads this one).
 * - covered: an overlay drew over the live result;
 * - document_hidden: the page was not visible;
 * - live_view_not_shown: received or due while the live view was off screen;
 * - unmounted_before_paint: the live result component unmounted before its frame;
 * - superseded: a later revision of the same shot replaced it before its frame;
 * - not_displayed: the live result showed another shot (e.g. the profile filter).
 */
export const NOT_SHOWN_REASONS = [
  'covered',
  'document_hidden',
  'live_view_not_shown',
  'unmounted_before_paint',
  'superseded',
  'not_displayed',
] as const;
export type NotShownReason = (typeof NOT_SHOWN_REASONS)[number];

type Emit = (event: string, payload: unknown) => void;
type Schedule = (callback: () => void, isCurrent: () => boolean) => void;

const SEMANTICS = 'post_render_task';
const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]', '::1']);
/** Unacknowledged revisions kept; older ones are forgotten (never acknowledged). */
const MAX_PENDING = 64;

export function clientKindFor(hostname: string | undefined): 'local_kiosk' | 'remote' {
  return hostname !== undefined && LOCAL_HOSTS.has(hostname) ? 'local_kiosk' : 'remote';
}

/** The Chromium version in a user agent (Chrome/x.y.z.w), or null for other renderers. */
export function rendererVersionFrom(userAgent: string | undefined): string | null {
  const match = userAgent ? /\bChrome\/([\d.]+)/.exec(userAgent) : null;
  return match ? match[1] : null;
}

function newDocumentId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  return `doc-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** One per page load: a reload or a second kiosk window is a different document. */
export const DOCUMENT_ID: string = newDocumentId();

const userAgent = typeof navigator !== 'undefined' ? navigator.userAgent : undefined;
/**
 * Sent with every pong and report so clock samples are mapped per document, and so
 * runs in different renderers (Chromium kiosk, a packaged shell) are never mixed.
 */
const DOCUMENT_IDENTITY = {
  document_id: DOCUMENT_ID,
  time_origin_ms: typeof performance !== 'undefined' ? performance.timeOrigin : null,
  client_kind: clientKindFor(typeof location !== 'undefined' ? location.hostname : undefined),
  user_agent: userAgent ?? null,
  renderer_version: rendererVersionFrom(userAgent),
};

interface PendingRevision {
  timestamp: string;
  /** The exact object the store shows for this revision (identity, not timestamp). */
  shot: Shot;
  tag: TraceTag;
  event: string;
  receivedMs: number;
  /** The live view was shown when it arrived, and has not been left since. */
  liveViewAtReceipt: boolean;
  liveViewLeft: boolean;
  /** Earlier revisions of this shot it replaced; reported once, unless acknowledged. */
  superseded: PendingRevision[];
  done: boolean;
}

const pending = new Map<string, PendingRevision>();
let liveViewShown = false;

function warn(what: string, error: unknown): void {
  console.warn(`[trace] ${what} failed; ignored`, error);
}

function isTraceTag(tag: unknown): tag is TraceTag {
  if (!tag || typeof tag !== 'object') return false;
  const t = tag as Partial<TraceTag>;
  return (
    typeof t.attempt_id === 'string' &&
    t.attempt_id.length > 0 &&
    Number.isInteger(t.revision) &&
    typeof t.final === 'boolean' &&
    (t.update_kind === 'first' || t.update_kind === 'late_update' || t.update_kind === 'status') &&
    Array.isArray(t.core_fields) &&
    t.core_fields.every((field) => typeof field === 'string')
  );
}

/** The app says whether the live result view is the one on screen. */
export function setLiveViewShown(shown: boolean): void {
  liveViewShown = shown;
  if (!shown) {
    for (const entry of pending.values()) entry.liveViewLeft = true;
  }
}

/**
 * Remember a traced shot revision's receive time and the shot object the store will
 * show for it. Call it with the message's own `shot` before the store takes it.
 *
 * A status notice (R-F5) is never acknowledged itself; it carries the latest revision's
 * values in a new object, so it only moves that revision onto the object now shown.
 */
export function noteShotReceived(
  shot: Shot | undefined,
  event: 'shot' | 'shot_update',
  tag: TraceTag | undefined,
  now: number = performance.now()
): void {
  try {
    if (!shot || typeof shot.timestamp !== 'string' || !isTraceTag(tag)) return;
    const timestamp = shot.timestamp;
    const previous = pending.get(timestamp);
    const sameRevision =
      previous !== undefined && previous.tag.attempt_id === tag.attempt_id && previous.tag.revision === tag.revision;
    if (tag.update_kind === 'status' || tag.revision < 1) {
      if (sameRevision) previous.shot = shot;
      return;
    }
    if (sameRevision) {
      previous.shot = shot; // a repeat: its first receive time stands, on the object now shown
      return;
    }
    const superseded = previous ? [...previous.superseded, previous] : [];
    if (previous) previous.superseded = [];
    pending.delete(timestamp); // re-insert last, so the oldest is evicted first
    pending.set(timestamp, {
      timestamp,
      shot,
      tag,
      event,
      receivedMs: now,
      liveViewAtReceipt: liveViewShown,
      liveViewLeft: false,
      superseded,
      done: false,
    });
    while (pending.size > MAX_PENDING) {
      pending.delete(pending.keys().next().value as string);
    }
  } catch (error) {
    warn('noting a received shot', error);
  }
}

/** rAF runs before the frame's paint; the MessageChannel task runs after it. Both re-check. */
export function afterNextPaint(callback: () => void, isCurrent: () => boolean = () => true): void {
  if (typeof requestAnimationFrame !== 'function' || typeof MessageChannel !== 'function') return;
  requestAnimationFrame(() => {
    if (!isCurrent()) return;
    const channel = new MessageChannel();
    channel.port1.onmessage = () => {
      channel.port1.close();
      channel.port2.close();
      if (isCurrent()) callback();
    };
    channel.port2.postMessage(null);
  });
}

function renderedCore(shot: Shot, fields: string[]): Record<string, unknown> {
  const record = shot as unknown as Record<string, unknown>;
  return Object.fromEntries(fields.map((key) => [key, record[key] ?? null]));
}

function revisionFields(entry: PendingRevision) {
  return {
    attempt_id: entry.tag.attempt_id,
    revision: entry.tag.revision,
    final: entry.tag.final,
    update_kind: entry.tag.update_kind,
    core_eligible: entry.tag.core_eligible,
    event: entry.event,
    received_ms: entry.receivedMs,
  };
}

/**
 * One per mounted live result component. commit() after every render commit with the
 * shot it shows and whether nothing covers it; dispose() on unmount.
 */
export function createAckScheduler(
  emit: Emit,
  schedule: Schedule = afterNextPaint,
  now: () => number = () => performance.now(),
  visible: () => boolean = () => typeof document === 'undefined' || document.visibilityState === 'visible'
) {
  let generation = 0;
  let disposed = false;
  let scheduled: { entry: PendingRevision; key: string } | null = null;

  const safeEmit = (event: string, payload: Record<string, unknown>) => {
    try {
      emit(event, { ...payload, ...DOCUMENT_IDENTITY });
    } catch (error) {
      warn(`emitting ${event}`, error);
    }
  };

  const forget = (entry: PendingRevision) => {
    entry.done = true;
    if (pending.get(entry.timestamp) === entry) pending.delete(entry.timestamp);
  };

  /** Older revisions this one replaced were never shown: say so once each. */
  const reportSuperseded = (entry: PendingRevision) => {
    entry.superseded.splice(0).forEach((old) => withhold(old, 'superseded'));
  };

  /** Never acknowledged: say so once, and forget the revision. */
  function withhold(entry: PendingRevision, reason: NotShownReason): void {
    reportSuperseded(entry);
    if (entry.done) return;
    forget(entry);
    safeEmit('trace_not_shown', { ...revisionFields(entry), reported_ms: now(), reason });
  }

  const acknowledge = (entry: PendingRevision, rendered: Record<string, unknown>) => {
    reportSuperseded(entry);
    forget(entry);
    safeEmit('trace_paint', {
      ...revisionFields(entry),
      painted_ms: now(),
      rendered_core: rendered,
      semantics: SEMANTICS,
    });
  };

  const cancelScheduled = () => {
    generation += 1;
    scheduled = null;
  };

  const notShownAtCommit = (entry: PendingRevision, acknowledgeable: boolean): NotShownReason | null => {
    if (!entry.liveViewAtReceipt || entry.liveViewLeft || !liveViewShown) return 'live_view_not_shown';
    if (!acknowledgeable) return 'covered';
    if (!visible()) return 'document_hidden';
    return null;
  };

  const notShownAtFrame = (entry: PendingRevision): NotShownReason | null => {
    if (disposed) return 'unmounted_before_paint';
    if (entry.liveViewLeft || !liveViewShown) return 'live_view_not_shown';
    if (!visible()) return 'document_hidden';
    return null;
  };

  return {
    commit(shot: Shot | null, acknowledgeable: boolean): void {
      try {
        disposed = false; // a StrictMode remount commits again on this same scheduler
        const candidate = shot ? pending.get(shot.timestamp) : undefined;
        const entry = candidate && candidate.shot === shot ? candidate : undefined;
        // Every other traced revision is not what the live result shows now.
        for (const other of [...pending.values()]) {
          if (other !== entry) withhold(other, 'not_displayed');
        }
        if (!shot || !entry) {
          cancelScheduled();
          return;
        }
        reportSuperseded(entry);
        const reason = notShownAtCommit(entry, acknowledgeable);
        if (reason) {
          cancelScheduled();
          withhold(entry, reason);
          return;
        }
        const rendered = renderedCore(shot, entry.tag.core_fields);
        const key = JSON.stringify(rendered);
        if (scheduled && scheduled.entry === entry && scheduled.key === key) return; // R-F6
        cancelScheduled();
        const mine = generation;
        scheduled = { entry, key };
        schedule(
          () => {
            try {
              if (mine !== generation || entry.done) return;
              scheduled = null;
              const late = notShownAtFrame(entry);
              if (late) withhold(entry, late);
              else acknowledge(entry, rendered);
            } catch (error) {
              warn('post-render acknowledgement', error);
            }
          },
          () => mine === generation
        );
      } catch (error) {
        warn('scheduling a post-render acknowledgement', error);
      }
    },
    /**
     * Unmount. The scheduled callback still runs and reports unmounted_before_paint,
     * unless a StrictMode remount commits again on this scheduler first.
     */
    dispose(): void {
      disposed = true;
    },
  };
}

/** Answer a server clock ping at once with this document's monotonic time. */
export function answerClockPing(data: { ping_seq: number }, emit: Emit, now: number = performance.now()): void {
  try {
    if (!Number.isInteger(data?.ping_seq)) return;
    emit('trace_clock_pong', { ping_seq: data.ping_seq, browser_ms: now, ...DOCUMENT_IDENTITY });
  } catch (error) {
    warn('answering a clock ping', error);
  }
}

/** Test helper: forget every unacknowledged revision. */
export function clearPendingPaints(): void {
  pending.clear();
}
