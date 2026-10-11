import { useLayoutEffect, useMemo } from 'react';
import type { Shot } from '../types/shot';
import { createAckScheduler } from '../services/paintTrace';
import { socketService } from '../services/socketService';

/**
 * Stage 1b trace: the live result component acknowledges the traced revision it renders,
 * after its frame (`post_render_task`). Runs only for traced shots, never delays the
 * render, and a trace failure is logged and ignored (owner rule).
 */
export function usePostRenderAck(shot: Shot | null, acknowledgeable: boolean): void {
  const scheduler = useMemo(() => createAckScheduler((event, payload) => socketService.emitTrace(event, payload)), []);
  useLayoutEffect(() => {
    scheduler.commit(shot, acknowledgeable);
  }, [scheduler, shot, acknowledgeable]);
  // A layout cleanup runs in the same commit as the unmount, before any frame callback.
  useLayoutEffect(() => () => scheduler.dispose(), [scheduler]);
}
