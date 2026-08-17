import React, { useState } from 'react';
import { shipRegistryAPI } from '../../services/api';
import './ship-registry-panel.css';

/**
 * ShipRegistryPanel — WO-WIRE-SHIP-REGISTRY-UI + LEG-329/330.
 *
 * Hangar actions for report-stolen / retract / abandon / claim / transfer-claim,
 * plus registry eject / board (pin when required) and same-sector salvage-break.
 * Abandon/claim/transfer require a docked port_id. Salvage-break / board eligibility
 * are enforced server-side (same sector, Drifting, pin, etc.) — UI surfaces honest errors.
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
  | 'eject'
  | 'board'
  | 'salvage'
  | null;

type SalvageBreakResult = {
  ship_id?: string;
  started_at?: string;
  duration_seconds?: number;
  completes_at?: string;
};

const ShipRegistryPanel: React.FC<ShipRegistryPanelProps> = ({
  shipId,
  shipName,
  portId,
  onDone,
}) => {
  const [busy, setBusy] = useState<Busy>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [claimTargetId, setClaimTargetId] = useState(shipId);
  const [boardPin, setBoardPin] = useState('');
  const [salvageProgress, setSalvageProgress] = useState<SalvageBreakResult | null>(null);

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

  const handleEject = async () => {
    if (!window.confirm('Eject from your current ship into Drifting? Ownership is unchanged.')) {
      return;
    }
    setBusy('eject');
    setFeedback(null);
    try {
      await shipRegistryAPI.eject();
      setFeedback('Ejected — hull is Drifting.');
      onDone?.();
    } catch (err: unknown) {
      setFeedback(err instanceof Error ? err.message : 'Eject failed');
    } finally {
      setBusy(null);
    }
  };

  const handleBoard = async () => {
    const target = claimTargetId.trim();
    if (!target) return;
    const pin = boardPin.trim() || null;
    const pinNote = pin ? ' (with pin)' : '';
    if (!window.confirm(`Board ship ${target}${pinNote}?`)) return;
    setBusy('board');
    setFeedback(null);
    try {
      await shipRegistryAPI.board(target, pin);
      setFeedback(`Boarded ${target}.`);
      setBoardPin('');
      onDone?.();
    } catch (err: unknown) {
      setFeedback(err instanceof Error ? err.message : 'Board failed');
    } finally {
      setBusy(null);
    }
  };

  const handleSalvageBreak = async () => {
    const target = claimTargetId.trim();
    if (!target) return;
    if (
      !window.confirm(
        `Start a salvage break on ${target}? You must be in the same sector as a Drifting target.`
      )
    ) {
      return;
    }
    setBusy('salvage');
    setFeedback(null);
    setSalvageProgress(null);
    try {
      const result = (await shipRegistryAPI.salvageBreak(target)) as SalvageBreakResult;
      setSalvageProgress(result);
      const eta = result.completes_at
        ? ` ETA ${result.completes_at}`
        : result.duration_seconds != null
          ? ` (~${result.duration_seconds}s)`
          : '';
      setFeedback(`Salvage break started.${eta}`);
      onDone?.();
    } catch (err: unknown) {
      // In-progress / sector / not-drifting refusals — surface server message as-is (includes ETA when present).
      setFeedback(err instanceof Error ? err.message : 'Salvage break failed');
      const data = err && typeof err === 'object' ? (err as { data?: { detail?: SalvageBreakResult } }).data : undefined;
      const detail = data?.detail;
      if (detail && typeof detail === 'object' && (detail.completes_at || detail.duration_seconds)) {
        setSalvageProgress(detail);
      }
    } finally {
      setBusy(null);
    }
  };

  const docked = Boolean(portId);
  const label = shipName ? `"${shipName}"` : 'this ship';
  const targetReady = Boolean(claimTargetId.trim());

  return (
    <div className="ship-registry-panel" data-testid="ship-registry-panel">
      <div className="ship-registry-title">Registry</div>
      <div className="ship-registry-actions">
        <button
          type="button"
          data-testid="ship-registry-eject"
          disabled={Boolean(busy)}
          onClick={() => void handleEject()}
        >
          {busy === 'eject' ? 'Ejecting…' : 'Eject (drift)'}
        </button>
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
          aria-label="Ship id to claim, board, or salvage-break"
          placeholder="Ship id (claim / board / salvage)"
          value={claimTargetId}
          onChange={(e) => setClaimTargetId(e.target.value.trim())}
          disabled={Boolean(busy)}
        />
        <input
          type="password"
          data-testid="ship-registry-board-pin"
          aria-label="Hatch pin for boarding (optional for owner / post-break)"
          placeholder="Pin (if required)"
          value={boardPin}
          onChange={(e) => setBoardPin(e.target.value)}
          disabled={Boolean(busy)}
          autoComplete="off"
        />
        <button
          type="button"
          data-testid="ship-registry-board"
          disabled={Boolean(busy) || !targetReady}
          onClick={() => void handleBoard()}
        >
          {busy === 'board' ? 'Boarding…' : 'Board'}
        </button>
        <button
          type="button"
          data-testid="ship-registry-salvage-break"
          disabled={Boolean(busy) || !targetReady}
          title="Same-sector Drifting target only — server enforces"
          onClick={() => void handleSalvageBreak()}
        >
          {busy === 'salvage' ? 'Breaking…' : 'Salvage break'}
        </button>
        <button
          type="button"
          data-testid="ship-registry-claim"
          disabled={Boolean(busy) || !docked || !targetReady}
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
          disabled={Boolean(busy) || !docked || !targetReady}
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
      {salvageProgress && (salvageProgress.completes_at || salvageProgress.duration_seconds != null) && (
        <p
          className="ship-registry-salvage-progress"
          role="status"
          data-testid="ship-registry-salvage-progress"
        >
          In progress
          {salvageProgress.duration_seconds != null
            ? ` · ${salvageProgress.duration_seconds}s`
            : ''}
          {salvageProgress.completes_at ? ` · completes ${salvageProgress.completes_at}` : ''}
        </p>
      )}
      {feedback && (
        <p className="ship-registry-feedback" role="status" data-testid="ship-registry-feedback">
          {feedback}
        </p>
      )}
    </div>
  );
};

export default ShipRegistryPanel;
