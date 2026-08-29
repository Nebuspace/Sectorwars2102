/**
 * OwnershipTransferControl — voluntary planet ownership transfer (LEG-514).
 *
 * Canon: FEATURES/planets/colonization.md Ownership controls — owner offers,
 * recipient accepts, fee is server-computed. Display tip `fee_credits` only;
 * never multiply a client-side 5%.
 *
 * Recipient reject: GS has no reject route. Only the owner can cancel;
 * otherwise the offer expires (expires_at from GET offer).
 */
import React, { useCallback, useEffect, useState } from 'react';
import { planetaryAPI } from '../../services/api';
import './ownership-transfer-control.css';

export interface OwnershipTransferOffer {
  from_player_id: string;
  to_player_id: string;
  fee_credits: number;
  fee_base?: number;
  offered_at?: string;
  expires_at?: string;
}

export interface OwnershipTransferControlProps {
  planetId: string;
  isOwned: boolean;
  /** playerState.id — used to detect the pending-offer recipient. */
  currentPlayerId: string;
  onChanged?: () => void;
}

function feeLabel(credits: unknown): string | null {
  if (typeof credits !== 'number' || !Number.isFinite(credits)) return null;
  return `${credits} credits`;
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

/** Surface GS ownership-transfer detail; hide bare API Error status blobs (LEG-2954). */
export function formatOwnershipTransferError(
  err: unknown,
  fallback = 'Transfer request failed.',
): string {
  const status = httpStatus(err);
  const message = err instanceof Error ? err.message : undefined;
  const hasServerDetail =
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim());

  if (status === 403) {
    if (hasServerDetail) return message!;
    return 'You do not have permission for this ownership transfer action.';
  }

  if (status === 429) {
    if (hasServerDetail) return message!;
    return 'Ownership-transfer rate limit exceeded — wait a moment and try again.';
  }

  if (hasServerDetail) return message!;
  return fallback;
}

export const OwnershipTransferControl: React.FC<OwnershipTransferControlProps> = ({
  planetId,
  isOwned,
  currentPlayerId,
  onChanged,
}) => {
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [offer, setOffer] = useState<OwnershipTransferOffer | null>(null);
  const [recipientId, setRecipientId] = useState('');

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const status = await planetaryAPI.getOwnershipTransfer(planetId);
      const nextOffer = status?.offer && status.pending ? status.offer : null;
      setPending(Boolean(status?.pending && nextOffer));
      setOffer(nextOffer);
    } catch (err) {
      setError(formatOwnershipTransferError(err, 'Failed to load transfer status.'));
      setPending(false);
      setOffer(null);
    } finally {
      setLoading(false);
    }
  }, [planetId]);

  useEffect(() => {
    setRecipientId('');
    setNotice(null);
    void refresh();
  }, [planetId, refresh]);

  const isRecipient = Boolean(
    pending && offer && currentPlayerId && offer.to_player_id === currentPlayerId,
  );

  // Non-owners: fetch silently so random landings don't flash a transfer panel.
  if (!isOwned && (loading || !isRecipient)) {
    return null;
  }

  const run = async (fn: () => Promise<unknown>, successNotice: string) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await fn();
      setNotice(successNotice);
      onChanged?.();
      await refresh();
    } catch (err) {
      setError(formatOwnershipTransferError(err));
    } finally {
      setBusy(false);
    }
  };

  const onOffer = () => {
    const trimmed = recipientId.trim();
    if (!trimmed) {
      setError('Enter the recipient player id.');
      return;
    }
    void run(
      () => planetaryAPI.offerOwnershipTransfer(planetId, trimmed),
      'Transfer offer sent. The recipient must accept.',
    );
  };

  const onCancel = () => {
    void run(
      () => planetaryAPI.cancelOwnershipTransfer(planetId),
      'Transfer offer cancelled. No fee charged.',
    );
  };

  const onAccept = () => {
    void run(
      () => planetaryAPI.acceptOwnershipTransfer(planetId),
      'Ownership transfer accepted.',
    );
  };

  const displayedFee = feeLabel(offer?.fee_credits);

  return (
    <div
      className="ownership-transfer-control"
      data-testid="ownership-transfer-control"
      role="group"
      aria-label="Planet ownership transfer"
    >
      <span className="ownership-transfer-label">Ownership transfer</span>
      {loading && (
        <span className="ownership-transfer-status" data-testid="ownership-transfer-loading">
          Loading…
        </span>
      )}
      {!loading && isOwned && !pending && (
        <div className="ownership-transfer-offer-row">
          <label className="ownership-transfer-field-label" htmlFor={`ownership-transfer-recipient-${planetId}`}>
            Recipient player id
          </label>
          <input
            id={`ownership-transfer-recipient-${planetId}`}
            className="ownership-transfer-input"
            data-testid="ownership-transfer-recipient"
            value={recipientId}
            disabled={busy}
            autoComplete="off"
            onChange={(e) => setRecipientId(e.target.value)}
          />
          <button
            type="button"
            className="ownership-transfer-btn"
            data-testid="ownership-transfer-offer"
            disabled={busy}
            onClick={onOffer}
          >
            Offer transfer
          </button>
        </div>
      )}
      {!loading && isOwned && pending && offer && (
        <div className="ownership-transfer-pending" data-testid="ownership-transfer-pending-owner">
          <span data-testid="ownership-transfer-fee">
            Pending offer to {offer.to_player_id}
            {displayedFee ? ` — fee on accept: ${displayedFee}` : ''}
            {offer.expires_at ? ` (expires ${offer.expires_at})` : ''}
          </span>
          <button
            type="button"
            className="ownership-transfer-btn"
            data-testid="ownership-transfer-cancel"
            disabled={busy}
            onClick={onCancel}
          >
            Cancel offer
          </button>
        </div>
      )}
      {!loading && isRecipient && offer && (
        <div className="ownership-transfer-pending" data-testid="ownership-transfer-pending-recipient">
          <span data-testid="ownership-transfer-fee">
            You have been offered this planet
            {displayedFee ? ` — current owner pays ${displayedFee} on accept` : ''}
            {offer.expires_at ? ` (expires ${offer.expires_at})` : ''}
          </span>
          <p className="ownership-transfer-hint" data-testid="ownership-transfer-no-reject">
            Only the current owner can cancel this offer. There is no reject action.
          </p>
          <button
            type="button"
            className="ownership-transfer-btn"
            data-testid="ownership-transfer-accept"
            disabled={busy}
            onClick={onAccept}
          >
            Accept transfer
          </button>
        </div>
      )}
      {busy && (
        <span className="ownership-transfer-status" data-testid="ownership-transfer-busy">
          Working…
        </span>
      )}
      {error && (
        <span className="ownership-transfer-error" role="alert" data-testid="ownership-transfer-error">
          {error}
        </span>
      )}
      {notice && !error && (
        <span className="ownership-transfer-notice" role="status" data-testid="ownership-transfer-notice">
          {notice}
        </span>
      )}
    </div>
  );
};

export default OwnershipTransferControl;
