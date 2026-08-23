import type { ReactNode } from 'react';
import type { ShotProcessingPhase } from '../stores/useShotStore';
import { ProgressIndicator } from './ProgressIndicator';

interface ShotProcessingAreaProps {
  phase: ShotProcessingPhase | null;
  children: ReactNode;
}

export function ShotProcessingArea({ phase, children }: ShotProcessingAreaProps) {
  return (
    <>
      {phase ? (
        <ProgressIndicator
          variant="inline"
          title={phase === 'capturing' ? 'Impact detected' : 'Shot captured'}
          detail={phase === 'capturing' ? 'Capturing radar data…' : 'Calculating metrics…'}
        />
      ) : null}
      {children}
    </>
  );
}
