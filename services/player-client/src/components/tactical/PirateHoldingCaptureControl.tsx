import React from 'react';
import { pirateHoldingsAPI, type PirateHoldingCaptureResponse } from '../../services/api';

interface Props {
  holdingId: string;
  onCaptured: () => void;
}

function httpStatus(err: unknown): number | undefined {
  if (err && typeof err === 'object') {
    const direct = (err as { status?: number }).status;
    if (typeof direct === 'number') return direct;
    const resp = (err as { response?: { status?: number } }).response;
    if (typeof resp?.status === 'number') return resp.status;
  }
  return undefined;
}

function formatCaptureError(err: unknown): string {
  const status = httpStatus(err);
  if (status === 409) {
    const msg = err instanceof Error ? err.message : '';
    if (msg && msg.trim() && !/^API Error: \d+$/.test(msg.trim())) return msg;
    return 'Combat lock lost — holding may already be captured.';
  }
  if (err instanceof Error && err.message && !/^API Error: \d+$/.test(err.message)) {
    return err.message;
  }
  return 'Capture failed — please try again.';
}

/**
 * Pirate-holding capture control (LEG-4154).
 * Mounted by PirateHoldingRaidControl when lock_applied=true after raid initiation.
 * Only renders when the player holds an active combat lock on the holding.
 */
const PirateHoldingCaptureControl: React.FC<Props> = ({ holdingId, onCaptured }) => {
  const [busy, setBusy] = React.useState(false);
  const [result, setResult] = React.useState<PirateHoldingCaptureResponse | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const handleCapture = async () => {
    if (busy || result) return;
    setBusy(true);
    setError(null);
    try {
      const res = await pirateHoldingsAPI.captureHolding(holdingId);
      setResult(res);
      onCaptured();
    } catch (e: unknown) {
      setError(formatCaptureError(e));
    } finally {
      setBusy(false);
    }
  };

  if (result) {
    return (
      <div
        className="threat-msg ok"
        role="status"
        data-testid="pirate-holding-capture-success"
      >
        Holding captured.{result.owner_team_id ? ' Now owned by your team.' : ''}
      </div>
    );
  }

  return (
    <div className="threat-section" data-testid="pirate-holding-capture-control">
      <button
        type="button"
        className="threat-btn"
        data-testid="pirate-holding-capture-btn"
        onClick={() => void handleCapture()}
        disabled={busy}
        aria-busy={busy}
        title="Capture this pirate holding"
      >
        {busy ? '…' : 'CAPTURE HOLDING ▸'}
      </button>
      {error && (
        <div
          className="threat-msg err"
          role="status"
          data-testid="pirate-holding-capture-err"
        >
          {error}
        </div>
      )}
    </div>
  );
};

export default PirateHoldingCaptureControl;
