import { renderToString } from 'react-dom/server';
import { afterEach, describe, expect, it } from 'vitest';
import { useSystemStore } from '../stores/useSystemStore';
import type { SimShotNotSent } from '../types/socket';
import { SimNotSentNotice } from './SimNotSentNotice';

// D31: a fusion unit sends a simulator only what it measured. A shot missing a value
// the simulator requires is not sent, and the screen says why.
const notSent: SimShotNotSent = {
  reason: 'spin not measured',
  missing: ['spin'],
  shot_timestamp: '2026-10-11T10:00:00',
  targets: ['gspro'],
};

describe('SimNotSentNotice', () => {
  afterEach(() => {
    useSystemStore.setState({ simShotNotSent: null });
  });

  it('says why the latest shot was not sent to the simulator', () => {
    const html = renderToString(<SimNotSentNotice notSent={notSent} latestShotTimestamp={notSent.shot_timestamp} />);

    expect(html).toContain('Not sent to simulator: spin not measured');
    expect(html).toContain('role="status"');
  });

  it('shows nothing for an earlier shot or when every shot was sent', () => {
    expect(renderToString(<SimNotSentNotice notSent={notSent} latestShotTimestamp="2026-10-11T10:01:00" />)).toBe('');
    expect(renderToString(<SimNotSentNotice notSent={null} latestShotTimestamp={notSent.shot_timestamp} />)).toBe('');
    expect(renderToString(<SimNotSentNotice notSent={notSent} latestShotTimestamp={null} />)).toBe('');
  });

  it('keeps the latest not-sent shot in the system store', () => {
    useSystemStore.getState().setSimShotNotSent(notSent);

    expect(useSystemStore.getState().simShotNotSent).toEqual(notSent);
  });
});
