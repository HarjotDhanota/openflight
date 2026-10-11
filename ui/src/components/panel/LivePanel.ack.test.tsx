// @vitest-environment jsdom
/**
 * Integration: the live result component itself acknowledges the revision it renders
 * (stage 1b trace), through the real rAF + MessageChannel path, with the values on screen.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Shot } from '../../types/shot';
import { formatSpeed, NO_VALUE } from '../../utils/units';
import { clearPendingPaints, noteShotReceived, setLiveViewShown, type TraceTag } from '../../services/paintTrace';
import { socketService } from '../../services/socketService';
import { LivePanel } from './LivePanel';

vi.mock('../../services/socketService', () => ({ socketService: { emitTrace: vi.fn() } }));

// Node 25 ships its own (method-less without --localstorage-file) localStorage global,
// which shadows jsdom's; the stores read it at import, so give them a working one first.
vi.hoisted(() => {
  const store = new Map<string, string>();
  const storage = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, String(value)),
    removeItem: (key: string) => void store.delete(key),
    clear: () => store.clear(),
    key: (index: number) => [...store.keys()][index] ?? null,
    get length() {
      return store.size;
    },
  };
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: storage });
});

const emitTrace = vi.mocked(socketService.emitTrace);
const TS = '2026-10-11T10:00:00Z';
const CORE_FIELDS = [
  'ball_speed_mph',
  'launch_angle_vertical',
  'launch_angle_vertical_source',
  'launch_angle_horizontal',
  'launch_angle_horizontal_source',
  'launch_angle_horizontal_status',
];

function makeShot(overrides: Partial<Shot> = {}): Shot {
  return {
    ball_speed_mph: 121.4,
    club_speed_mph: 88,
    smash_factor: 1.38,
    estimated_carry_yards: 210,
    carry_range: [205, 215],
    club: 'driver',
    timestamp: TS,
    peak_magnitude: 100,
    launch_angle_vertical: 13.4,
    launch_angle_horizontal: -1.2,
    launch_angle_vertical_source: 'radar',
    launch_angle_horizontal_source: 'radar',
    launch_angle_horizontal_status: 'measured',
    launch_angle_confidence: 0.8,
    angle_source: 'radar',
    club_angle_deg: 2.1,
    club_path_deg: -0.6,
    spin_axis_deg: 3.4,
    spin_rpm: 2650,
    spin_confidence: 0.9,
    spin_quality: 'high',
    spin_source: 'measured',
    spin_method: null,
    carry_spin_adjusted: 214,
    profile_id: 'james',
    profile_name: 'James',
    ...overrides,
  } as Shot;
}

const tag = (revision: number, updateKind: TraceTag['update_kind'] = 'first'): TraceTag => ({
  attempt_id: 't-00007',
  revision,
  final: revision > 1,
  update_kind: updateKind,
  core_eligible: false,
  core_fields: CORE_FIELDS,
});

let container: HTMLDivElement;
let root: Root;

function render(shot: Shot | null, acknowledgeable = true, shots: Shot[] = shot ? [shot] : [], profileId = 'james') {
  act(() => {
    root.render(
      <LivePanel
        shot={shot}
        shots={shots}
        profileId={profileId}
        profileName="James"
        clubLabel="DR"
        onSelectMetric={() => {}}
        acknowledgeable={acknowledgeable}
      />
    );
  });
}

/** The value text of the ball speed tile, as the user sees it. */
function ballSpeedOnScreen(): string {
  const card = [...container.querySelectorAll('.metric-card')].find((el) =>
    el.querySelector('.metric-card__label')?.textContent?.startsWith('Ball speed')
  );
  return card?.querySelector('.metric-card__value')?.textContent ?? '';
}

const calls = (event: string) =>
  emitTrace.mock.calls.filter(([name]) => name === event).map(([, payload]) => payload as Record<string, unknown>);

/** Let the real rAF callback and the MessageChannel task after it run. */
async function nextFrames(count = 3) {
  for (let i = 0; i < count; i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
  }
}

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  // jsdom has no layout observers or font loading; the live grid's font fitting uses them.
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    }
  );
  Object.defineProperty(document, 'fonts', { configurable: true, value: { ready: Promise.resolve() } });
});

afterAll(() => vi.unstubAllGlobals());

beforeEach(() => {
  emitTrace.mockReset();
  setLiveViewShown(true);
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  clearPendingPaints();
});

describe('LivePanel post-render acknowledgement', () => {
  it('acknowledges the shown revision with exactly the values on screen', async () => {
    const shot = makeShot();
    noteShotReceived(shot, 'shot', tag(1), performance.now());
    render(shot);
    expect(calls('trace_paint')).toEqual([]); // never inside the render commit
    await nextFrames();

    const paints = calls('trace_paint');
    expect(paints).toHaveLength(1);
    const rendered = paints[0].rendered_core as Record<string, unknown>;
    expect(paints[0]).toMatchObject({ attempt_id: 't-00007', revision: 1, semantics: 'post_render_task' });
    expect(rendered).toEqual({
      ball_speed_mph: 121.4,
      launch_angle_vertical: 13.4,
      launch_angle_vertical_source: 'radar',
      launch_angle_horizontal: -1.2,
      launch_angle_horizontal_source: 'radar',
      launch_angle_horizontal_status: 'measured',
    });
    expect(ballSpeedOnScreen()).toBe(formatSpeed(rendered.ball_speed_mph as number, 'imperial', 1));
    expect(paints[0].painted_ms as number).toBeGreaterThanOrEqual(paints[0].received_ms as number);
  });

  it('a late update replacing the revision before its frame: only the new one is acknowledged', async () => {
    const first = makeShot();
    const late = makeShot({ ball_speed_mph: 122.6 });
    noteShotReceived(first, 'shot', tag(1), performance.now());
    render(first);
    noteShotReceived(late, 'shot_update', tag(2, 'late_update'), performance.now());
    render(late);
    await nextFrames();

    expect(calls('trace_paint').map((p) => p.revision)).toEqual([2]);
    expect((calls('trace_paint')[0].rendered_core as Record<string, unknown>).ball_speed_mph).toBe(122.6);
    expect(ballSpeedOnScreen()).toBe('122.6');
    expect(calls('trace_not_shown').map((p) => [p.revision, p.reason])).toEqual([[1, 'superseded']]);
  });

  it('a re-render of the same result before the frame keeps the acknowledgement', async () => {
    const shot = makeShot();
    noteShotReceived(shot, 'shot', tag(1), performance.now());
    render(shot);
    render(shot, true, [shot]); // e.g. the parent re-rendered for another reason
    await nextFrames();

    expect(calls('trace_paint')).toHaveLength(1);
  });

  it('a status notice re-render keeps the acknowledgement of its revision', async () => {
    const shot = makeShot();
    const status = makeShot(); // the server's status notice: a new object, same values
    noteShotReceived(shot, 'shot', tag(1), performance.now());
    render(shot);
    noteShotReceived(status, 'shot_update', tag(1, 'status'), performance.now());
    render(status);
    await nextFrames();

    expect(calls('trace_paint').map((p) => [p.revision, p.update_kind])).toEqual([[1, 'first']]);
    expect(calls('trace_not_shown')).toEqual([]);
  });

  it('a covered result (club picker open) is reported, never acknowledged', async () => {
    const shot = makeShot();
    noteShotReceived(shot, 'shot', tag(1), performance.now());
    render(shot, false);
    await nextFrames();
    render(shot, true); // the picker closes
    await nextFrames();

    expect(calls('trace_paint')).toEqual([]);
    expect(calls('trace_not_shown').map((p) => p.reason)).toEqual(['covered']);
  });

  it('a shot the profile filter hides is reported, and not acknowledged when its profile is chosen later', async () => {
    const shown = makeShot({ timestamp: '2026-10-11T09:59:00Z' });
    const other = makeShot({ profile_id: 'sam', ball_speed_mph: 99.9 });
    noteShotReceived(other, 'shot', tag(1), performance.now());
    render(shown, true, [shown, other]); // james's panel still shows james's last shot
    await nextFrames();
    render(other, true, [shown, other], 'sam'); // seconds later the user switches to sam
    await nextFrames();

    expect(calls('trace_paint')).toEqual([]);
    expect(calls('trace_not_shown').map((p) => p.reason)).toEqual(['not_displayed']);
  });

  it('unmounting before the frame reports unmounted_before_paint, never an ack', async () => {
    const shot = makeShot();
    noteShotReceived(shot, 'shot', tag(1), performance.now());
    render(shot);
    act(() => root.render(<div />));
    await nextFrames();

    expect(calls('trace_paint')).toEqual([]);
    expect(calls('trace_not_shown').map((p) => p.reason)).toEqual(['unmounted_before_paint']);
  });

  it('a status notice alone is never acknowledged', async () => {
    const shot = makeShot();
    noteShotReceived(shot, 'shot_update', tag(1, 'status'), performance.now());
    render(shot);
    await nextFrames();

    expect(emitTrace).not.toHaveBeenCalled();
  });

  it('a failing trace emit never stops the result from rendering', async () => {
    emitTrace.mockImplementation(() => {
      throw new Error('socket gone');
    });
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const shot = makeShot();
    noteShotReceived(shot, 'shot', tag(1), performance.now());
    render(shot);
    await nextFrames();

    expect(ballSpeedOnScreen()).toBe('121.4');
    expect(emitTrace).toHaveBeenCalled();
    warn.mockRestore();
  });

  it('a nulled ball speed renders the no-value mark and is echoed as null', async () => {
    const shot = makeShot({ ball_speed_mph: null });
    noteShotReceived(shot, 'shot', tag(1), performance.now());
    render(shot);
    await nextFrames();

    expect(ballSpeedOnScreen()).toBe(NO_VALUE);
    expect((calls('trace_paint')[0].rendered_core as Record<string, unknown>).ball_speed_mph).toBeNull();
  });
});
