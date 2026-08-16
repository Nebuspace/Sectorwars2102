import React from 'react';
import { bountyAPI } from '../../../services/api';
import { useGame } from '../../../contexts/GameContext';
import { useWebSocket } from '../../../contexts/WebSocketContext';
import { MFDEmpty } from '../atoms';

/**
 * Bounty board (LEG-32) — place / browse / cancel player bounties via the
 * already-defined bountyAPI. Live-refreshes on bountyEventSignal.
 */

const MIN_AMOUNT = 1000;

type BountyRow = {
  id?: string;
  bounty_id?: string;
  target_id?: string;
  target_name?: string;
  amount?: number;
  placed_by?: string;
  placed_by_name?: string;
  type?: string;
};

const errDetail = (e: unknown, fallback: string): string => {
  if (e && typeof e === 'object') {
    const resp = (e as { response?: { data?: unknown } }).response;
    const data = resp?.data ?? (e as { data?: unknown }).data;
    if (data && typeof data === 'object') {
      const detail = (data as Record<string, unknown>).detail;
      if (typeof detail === 'string' && detail) return detail;
    }
    const msg = (e as { message?: string }).message;
    if (typeof msg === 'string' && msg) return msg;
  }
  return fallback;
};

const rowId = (row: BountyRow): string => String(row.bounty_id || row.id || '');

export const BountyBoardPanel: React.FC = () => {
  const { playerState, refreshPlayerState } = useGame();
  const { bountyEventSignal } = useWebSocket();

  const [available, setAvailable] = React.useState<BountyRow[] | null>(null);
  const [onTarget, setOnTarget] = React.useState<BountyRow[] | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [actionError, setActionError] = React.useState<string | null>(null);
  const [notice, setNotice] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  const [targetId, setTargetId] = React.useState('');
  const [amount, setAmount] = React.useState(String(MIN_AMOUNT));
  const [lookupId, setLookupId] = React.useState('');

  const feePreview = (() => {
    const n = parseInt(amount, 10);
    if (Number.isNaN(n) || n < MIN_AMOUNT) return null;
    return Math.floor(n * 0.1);
  })();

  const loadAvailable = React.useCallback(async () => {
    try {
      const data = await bountyAPI.getAvailable(20);
      const list = Array.isArray(data)
        ? data
        : data && typeof data === 'object' && Array.isArray((data as { bounties?: unknown }).bounties)
          ? ((data as { bounties: BountyRow[] }).bounties)
          : [];
      setAvailable(list as BountyRow[]);
      setLoadError(null);
    } catch (e) {
      setLoadError(errDetail(e, 'Failed to load bounty board'));
      setAvailable([]);
    }
  }, []);

  React.useEffect(() => {
    loadAvailable();
  }, [loadAvailable, bountyEventSignal]);

  const handlePlace = async () => {
    if (busy) return;
    const tid = targetId.trim();
    const amt = parseInt(amount, 10);
    if (!tid) {
      setActionError('Enter a target player id');
      return;
    }
    if (Number.isNaN(amt) || amt < MIN_AMOUNT) {
      setActionError(`Minimum bounty is ${MIN_AMOUNT} credits`);
      return;
    }
    setBusy(true);
    setActionError(null);
    try {
      await bountyAPI.place(tid, amt);
      setNotice(`Bounty placed on ${tid} for ${amt.toLocaleString()} cr (+10% fee).`);
      setTargetId('');
      await Promise.allSettled([loadAvailable(), refreshPlayerState()]);
    } catch (e) {
      setActionError(errDetail(e, 'Place bounty rejected'));
    } finally {
      setBusy(false);
    }
  };

  const handleLookup = async () => {
    const tid = lookupId.trim();
    if (!tid) return;
    setBusy(true);
    setActionError(null);
    try {
      const data = await bountyAPI.getOnTarget(tid);
      const list = Array.isArray(data)
        ? data
        : data && typeof data === 'object' && Array.isArray((data as { bounties?: unknown }).bounties)
          ? ((data as { bounties: BountyRow[] }).bounties)
          : [];
      setOnTarget(list as BountyRow[]);
    } catch (e) {
      setActionError(errDetail(e, 'Lookup failed'));
      setOnTarget([]);
    } finally {
      setBusy(false);
    }
  };

  const handleCancel = async (row: BountyRow) => {
    const bid = rowId(row);
    const tid = String(row.target_id || lookupId.trim() || '');
    if (!bid || !tid || busy) return;
    setBusy(true);
    setActionError(null);
    try {
      await bountyAPI.cancel(bid, tid);
      setNotice(`Cancelled bounty ${bid} (fee non-refundable).`);
      await Promise.allSettled([loadAvailable(), handleLookup(), refreshPlayerState()]);
    } catch (e) {
      setActionError(errDetail(e, 'Cancel rejected'));
    } finally {
      setBusy(false);
    }
  };

  const selfId = playerState?.id ? String(playerState.id) : '';

  return (
    <section className="mfd-bounty-board" data-testid="bounty-board-panel">
      <h3 className="mfd-ops-subtitle">BOUNTY BOARD</h3>
      <p className="mfd-ops-hint">
        Place a bounty (min {MIN_AMOUNT} cr + 10% fee). Escrowed principal refunds on cancel; fee does
        not.
      </p>

      <div className="mfd-bounty-form">
        <label>
          Target player id
          <input
            data-testid="bounty-place-target"
            value={targetId}
            onChange={(e) => setTargetId(e.target.value)}
            disabled={busy}
          />
        </label>
        <label>
          Amount (cr)
          <input
            data-testid="bounty-place-amount"
            type="number"
            min={MIN_AMOUNT}
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            disabled={busy}
          />
        </label>
        {feePreview != null && (
          <span data-testid="bounty-fee-preview">Fee: {feePreview.toLocaleString()} cr</span>
        )}
        <button
          type="button"
          data-testid="bounty-place-btn"
          disabled={busy}
          onClick={handlePlace}
        >
          {busy ? '…' : 'PLACE BOUNTY'}
        </button>
      </div>

      <div className="mfd-bounty-form">
        <label>
          Inspect target id
          <input
            data-testid="bounty-lookup-target"
            value={lookupId}
            onChange={(e) => setLookupId(e.target.value)}
            disabled={busy}
          />
        </label>
        <button type="button" data-testid="bounty-lookup-btn" disabled={busy} onClick={handleLookup}>
          LOOK UP
        </button>
      </div>

      {actionError && <div className="mfd-ops-error" data-testid="bounty-action-error">{actionError}</div>}
      {notice && <div className="mfd-ops-hint" data-testid="bounty-notice">{notice}</div>}
      {loadError && <div className="mfd-ops-error">{loadError}</div>}

      <h4 className="mfd-ops-subtitle">Available</h4>
      {available == null ? (
        <MFDEmpty text="Loading board…" />
      ) : available.length === 0 ? (
        <MFDEmpty text="No open bounties" />
      ) : (
        <ul className="mfd-bounty-list" data-testid="bounty-available-list">
          {available.map((row) => (
            <li key={rowId(row) || `${row.target_id}-${row.amount}`}>
              <span>
                {(row.target_name || row.target_id || '?') +
                  ` — ${(row.amount ?? 0).toLocaleString()} cr`}
              </span>
              {selfId && row.placed_by === selfId && (
                <button
                  type="button"
                  data-testid={`bounty-cancel-${rowId(row)}`}
                  disabled={busy}
                  onClick={() => handleCancel(row)}
                >
                  CANCEL
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {onTarget && (
        <>
          <h4 className="mfd-ops-subtitle">On target</h4>
          {onTarget.length === 0 ? (
            <MFDEmpty text="No bounties on that target" />
          ) : (
            <ul className="mfd-bounty-list" data-testid="bounty-on-target-list">
              {onTarget.map((row) => (
                <li key={rowId(row) || `${row.placed_by}-${row.amount}`}>
                  <span>
                    {(row.placed_by_name || row.placed_by || '?') +
                      ` — ${(row.amount ?? 0).toLocaleString()} cr`}
                  </span>
                  {selfId && row.placed_by === selfId && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => handleCancel(row)}
                    >
                      CANCEL
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
};

export default BountyBoardPanel;
