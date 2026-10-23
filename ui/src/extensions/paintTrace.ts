import { PostRenderAck } from '../hooks/usePostRenderAck';
import { answerClockPing, noteShotReceived, setLiveViewShown, type TraceTag } from '../services/paintTrace';
import type { UiExtension } from './index';

const paintTrace: UiExtension = {
  LiveResult: PostRenderAck,
  onSocket(socket) {
    socket.on('trace_clock_ping', (data: { ping_seq: number }) => {
      answerClockPing(data, (event, payload) => socket.emit(event, payload));
    });
  },
  onConnect(socket) {
    // ask for a clock ping at once (a traced server answers this socket only)
    socket.emit('trace_clock_hello');
  },
  onShotMessage(event, message) {
    // the receive time first; noteShotReceived never throws, so the shot is always handled
    const { shot, trace } = (message ?? {}) as { shot?: unknown; trace?: TraceTag };
    noteShotReceived(shot as Parameters<typeof noteShotReceived>[0], event, trace);
  },
  onLiveViewShown: setLiveViewShown,
};

export default paintTrace;
