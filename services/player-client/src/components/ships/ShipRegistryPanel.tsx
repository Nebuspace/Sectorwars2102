import React, { useState } from 'react';
import { shipRegistryAPI } from '../../services/api';
import './ship-registry-panel.css';

/**
 * ShipRegistryPanel — WO-WIRE-SHIP-REGISTRY-UI.
 *
 * Hangar actions for report-stolen / retract / abandon / claim / transfer-claim.
 * Abandon/claim/transfer require a docked port_id.
 */
export interface ShipRegistryPanelProps {
  shipId: string;
  shipName?: string;
  /** Required for abandon / claim / file-transfer-claim. */
  portId?: string | null;
  onDone?: () => void;
}

type Busy =
  | 'report'
  | 'retract'
  | 'abandon'
  | 'claim'
  | 'transfer'
  | 'approve'
  | null;

const ShipRegistryPanel: React.FC<ShipRegistryPanelProps> = ({
  shipId,
  shipName,
  portId,
  onDone,
}) => {
  const [busy, setBusy] = useState<Busy>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [claimTargetId, setClaimTargetId] = useState(shipId);

  const run = async (kind: Exclude<Busy, null>, fn: () => Promise<unknown>, confirmMsg: string) => {
    if (!window.confirm(confirmMsg)) return;
    setBusy(kind);
    setFeedback(null);
    try {
      await fn();
      setFeedback('Registry update accepted.');
      onDone?.();
    } catch (err: unknown) {
      setFeedback(err instanceof Error ? err.message : 'Registry action failed');
    } finally {
      setBusy(null);
    }
  };

  const docked = Boolean(portId);
  const label = shipName ? `"${shipName}"` : 'this ship';

  return (
    <div className="ship-registry-panel" data-testid="ship-registry-panel">
      <div className="ship-registry-title">Registry</div>
      <div className="ship-registry-actions">
        <button
          type="button"
          data-testid="ship-registry-report-stolen"
          disabled={Boolean(busy)}
          onClick={() =>
            run(
              'report',
              () => shipRegistryAPI.reportStolen(shipId),
              `Report ${label} stolen? This flags the hull for recovery.`
            )
          }
        >
          {busy === 'report' ? 'Reporting…' : 'Report stolen'}
        </button>
        <button
          type="button"
          data-testid="ship-registry-retract-stolen"
          disabled={Boolean(busy)}
          onClick={() =>
            run(
              'retract',
              () => shipRegistryAPI.retractStolenReport(shipId),
              `Retract the stolen report on ${label}?`
            )
          }
        >
          {busy === 'retract' ? 'Retracting…' : 'Retract stolen'}
        </button>
        <button
          type="button"
          data-testid="ship-registry-abandon"
          disabled={Boolean(busy) || !docked}
          title={docked ? undefined : 'Dock at a station to abandon'}
          onClick={() =>
            run(
              'abandon',
              () => shipRegistryAPI.abandon(shipId, portId!),
              `Abandon ${label} at this port? You lose registered ownership.`
            )
          }
        >
          {busy === 'abandon' ? 'Abandoning…' : 'Abandon at port'}
        </button>
        <button
          type="button"
          data-testid="ship-registry-approve-transfer"
          disabled={Boolean(busy)}
          onClick={() =>
            run(
              'approve',
              () => shipRegistryAPI.approveTransferClaim(shipId),
              `Approve a pending transfer claim on ${label}?`
            )
          }
        >
          {busy === 'approve' ? 'Approving…' : 'Approve transfer'}
        </button>
      </div>
      <div className="ship-registry-claim-row">
        <input
          type="text"
          data-testid="ship-registry-claim-id"
          aria-label="Ship id to claim or file transfer on"
          placeholder="Ship id (claim / transfer)"
          value={claimTargetId}
          onChange={(e) => setClaimTargetId(e.target.value.trim())}
          disabled={Boolean(busy)}
        />
        <button
          type="button"
          data-testid="ship-registry-claim"
          disabled={Boolean(busy) || !docked || !claimTargetId}
          title={docked ? undefined : 'Dock at a station to claim'}
          onClick={() =>
            run(
              'claim',
              () => shipRegistryAPI.claim(claimTargetId, portId!),
              `Claim abandoned ship ${claimTargetId} at this port?`
            )
          }
        >
          {busy === 'claim' ? 'Claiming…' : 'Claim'}
        </button>
        <button
          type="button"
          data-testid="ship-registry-transfer-claim"
          disabled={Boolean(busy) || !docked || !claimTargetId}
          title={docked ? undefined : 'Dock at a station to file a transfer claim'}
          onClick={() =>
            run(
              'transfer',
              () => shipRegistryAPI.fileTransferClaim(claimTargetId, portId!),
              `File a contested transfer claim on ${claimTargetId}?`
            )
          }
        >
          {busy === 'transfer' ? 'Filing…' : 'Transfer claim'}
        </button>
      </div>
      {feedback && (
        <p className="ship-registry-feedback" role="status" data-testid="ship-registry-feedback">
          {feedback}
        </p>
      )}
    </div>
  );
};

export default ShipRegistryPanel;
