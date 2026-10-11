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

/** Why a shot revision was not acknowledged; the server keeps the same list. */
export type NotShownReason =
  | 'covered'
  | 'document_hidden'
  | 'live_view_not_shown'
  /** A later revision, or another shot, replaced it on screen before its frame. */
  | 'superseded';

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

function isShotRevisionTag(tag: unknown): tag is TraceTag {
  if (!tag || typeof tag !== 'object') return false;
  const t = tag as Partial<TraceTag>;
  return (
    typeof t.attempt_id === 'string' &&
    t.attempt_id.length > 0 &&
    Number.isInteger(t.revision) &&
    (t.revision as number) >= 1 &&
    typeof t.final === 'boolean' &&
    (t.update_kind === 'first' || t.update_kind === 'late_update') &&
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

/** Remember a traced shot revision's receive time, keyed by the shot it updates. */
export function noteShotReceived(
  shotTimestamp: string | undefined,
  event: 'shot' | 'shot_update',
  tag: TraceTag | undefined,
  now: number = performance.now()
): void {
  try {
    if (typeof shotTimestamp !== 'string' || !isShotRevisionTag(tag)) return;
    const previous = pending.get(shotTimestamp);
    if (previous && previous.tag.attempt_id === tag.attempt_id && previous.tag.revision === tag.revision) {
      return; // a repeat of the same revision: its first receive time stands
    }
    const superseded = previous ? [...previous.superseded, previous] : [];
    pending.delete(shotTimestamp); // re-insert last, so the oldest is evicted first
    pending.set(shotTimestamp, {
      timestamp: shotTimestamp,
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
  let scheduled: { entry: PendingRevision; key: string } | null = null;

  const safeEmit = (event: string, payload: Record<string, unknown>) => {
    try {
      emit(event, { ...payload, ...DOCUMENT_IDENTITY });
    } catch (error) {
      warn(`emitting ${event}`, error);
    }
  };

  /** Never acknowledged: say so once, and forget the revision. */
  const withhold = (entry: PendingRevision, reason: NotShownReason) => {
    if (entry.done) return;
    entry.done = true;
    if (pending.get(entry.timestamp) === entry) pending.delete(entry.timestamp);
    safeEmit('trace_not_shown', { ...revisionFields(entry), reported_ms: now(), reason });
  };

  const cancelScheduled = (keep?: PendingRevision) => {
    generation += 1;
    const previous = scheduled;
    scheduled = null;
    if (previous && previous.entry !== keep && pending.get(previous.entry.timestamp) === previous.entry) {
      withhold(previous.entry, 'superseded');
    }
  };

  const notShownReason = (entry: PendingRevision, acknowledgeable: boolean): NotShownReason | null => {
    if (!entry.liveViewAtReceipt || entry.liveViewLeft) return 'live_view_not_shown';
    if (!acknowledgeable) return 'covered';
    if (!visible()) return 'document_hidden';
    return null;
  };

  const acknowledge = (entry: PendingRevision, rendered: Record<string, unknown>) => {
    entry.done = true;
    if (pending.get(entry.timestamp) === entry) pending.delete(entry.timestamp);
    safeEmit('trace_paint', {
      ...revisionFields(entry),
      painted_ms: now(),
      rendered_core: rendered,
      semantics: SEMANTICS,
    });
  };

  return {
    commit(shot: Shot | null, acknowledgeable: boolean): void {
      try {
        const entry = shot ? pending.get(shot.timestamp) : undefined;
        if (!shot || !entry) {
          cancelScheduled();
          return;
        }
        entry.superseded.splice(0).forEach((old) => withhold(old, 'superseded'));
        const reason = notShownReason(entry, acknowledgeable);
        if (reason) {
          cancelScheduled(entry);
          withhold(entry, reason);
          return;
        }
        const rendered = renderedCore(shot, entry.tag.core_fields);
        const key = JSON.stringify(rendered);
        if (scheduled && scheduled.entry === entry && scheduled.key === key) return; // R-F6
        cancelScheduled(entry);
        const mine = generation;
        scheduled = { entry, key };
        schedule(
          () => {
            try {
              if (mine !== generation) return;
              scheduled = null;
              if (!visible()) withhold(entry, 'document_hidden');
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
    dispose(): void {
      // Cancels only: a StrictMode remount commits again on this same scheduler.
      generation += 1;
      scheduled = null;
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
