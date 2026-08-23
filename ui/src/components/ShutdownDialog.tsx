import { ProgressIndicator } from './ProgressIndicator';

export type ShutdownState = 'confirm' | 'pending' | 'error';

interface ShutdownDialogProps {
  state: ShutdownState;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ShutdownDialog({ state, onConfirm, onCancel }: ShutdownDialogProps) {
  if (state === 'pending') {
    return (
      <div className="shutdown-overlay">
        <div
          className="shutdown-dialog shutdown-dialog--pending"
          role="dialog"
          aria-modal="true"
          aria-label="Shutting down OpenFlight"
        >
          <ProgressIndicator
            variant="dialog"
            title="Shutting down OpenFlight…"
            detail="Safely stopping radar services"
          />
        </div>
      </div>
    );
  }

  const hasError = state === 'error';
  return (
    <div className="shutdown-overlay">
      <div className="shutdown-dialog" role="dialog" aria-modal="true" aria-labelledby="shutdown-dialog-title">
        <p id="shutdown-dialog-title">{hasError ? 'Could not start shutdown' : 'Shut down OpenFlight?'}</p>
        {hasError ? <span className="shutdown-dialog__error">Check the server and try again.</span> : null}
        <div className="shutdown-dialog__buttons">
          <button className="shutdown-dialog__confirm" onClick={onConfirm} autoFocus>
            {hasError ? 'Try Again' : 'Shut Down'}
          </button>
          <button className="shutdown-dialog__cancel" onClick={onCancel}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
