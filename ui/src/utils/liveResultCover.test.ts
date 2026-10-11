import { describe, expect, it } from 'vitest';
import { renderToString } from 'react-dom/server';
import { createElement } from 'react';
import { ShotProcessingArea } from '../components/ShotProcessingArea';
import { shotProcessingCoversResult, type ShotProcessingPhase } from '../stores/useShotStore';
import { isLiveResultCovered, type LiveOverlays } from './liveResultCover';

const NONE: LiveOverlays = {
  replayOpen: false,
  shutdownOpen: false,
  pickerOpen: false,
  menuOpen: false,
  profileDialogOpen: false,
  clearSessionOpen: false,
  processingPhase: null,
};
const PHASES: ShotProcessingPhase[] = [
  'capturing',
  'calculating',
  'iwr_dump',
  'camera_processing',
  'hardware_enrichment',
];

describe('isLiveResultCovered', () => {
  it('is uncovered with no overlay open', () => {
    expect(isLiveResultCovered(NONE)).toBe(false);
  });

  it.each(['replayOpen', 'shutdownOpen', 'pickerOpen', 'menuOpen', 'profileDialogOpen', 'clearSessionOpen'] as const)(
    'is covered while %s',
    (overlay) => {
      expect(isLiveResultCovered({ ...NONE, [overlay]: true })).toBe(true);
    }
  );

  it('is covered by the processing dialog, not by the inline enrichment status', () => {
    expect(PHASES.filter((phase) => isLiveResultCovered({ ...NONE, processingPhase: phase }))).toEqual([
      'capturing',
      'calculating',
    ]);
  });

  it('names as covering exactly the phases the processing area draws as an overlay', () => {
    for (const phase of PHASES) {
      const html = renderToString(createElement(ShotProcessingArea, { phase, children: null }));
      expect(shotProcessingCoversResult(phase)).toBe(html.includes('shot-processing-overlay'));
    }
    expect(shotProcessingCoversResult(null)).toBe(false);
  });
});
