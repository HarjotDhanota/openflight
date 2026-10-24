import { useLayoutEffect, useMemo, useSyncExternalStore } from 'react';
import type { LiveResultProps } from '../extensions';
import type { Shot } from '../types/shot';
import { createAckScheduler, pendingRevisionsVersion, subscribePendingRevisions } from '../services/paintTrace';
import { socketService } from '../services/socketService';

/**
 * Stage 1b trace: the live result component acknowledges the traced revision it renders,
 * after its frame (`post_render_task`). Runs only for traced shots, never delays the
 * render, and a trace failure is logged and ignored (owner rule).
 */
export function usePostRenderAck(shot: Shot | null, acknowledgeable: boolean): void {
  const scheduler = useMemo(() => createAckScheduler((event, payload) => socketService.emit(event, payload)), []);
  // Every noted revision re-runs the commit, so one for a shot not shown here (e.g. a
  // hidden profile's late update) is swept as not displayed at once, not acked later.
  const pendingVersion = useSyncExternalStore(
    subscribePendingRevisions,
    pendingRevisionsVersion,
    pendingRevisionsVersion // server snapshot: renderToString must keep working
  );
  useLayoutEffect(() => {
    scheduler.commit(shot, acknowledgeable);
  }, [scheduler, shot, acknowledgeable, pendingVersion]);
  // A layout cleanup runs in the same commit as the unmount, before any frame callback.
  useLayoutEffect(() => () => scheduler.dispose(), [scheduler]);
}

/** The trace's live-result slot: acknowledges what the live result renders; draws nothing. */
export function PostRenderAck({ shot, visible }: LiveResultProps): null {
  usePostRenderAck(shot, visible);
  return null;
}
