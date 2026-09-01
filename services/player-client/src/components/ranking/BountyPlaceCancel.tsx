/**
 * BountyPlaceCancel — player place + placer-only cancel UI (LEG-2553).
 *
 * Canon: FEATURES/gameplay/bounties.md § Placing a bounty / § Cancellation.
 * Invent=0: binds tip bountyAPI.place / getOnTarget / cancel only. Does not
 * remint BountyBoard browse. No portrait invent. Fee (10%) and refund
 * (principal only) disclosed from canon, not invented endpoints.
 */
import React, { useCallback, useMemo, useState } from 'react';
import { bountyAPI } from '../../services/api';
import { useAuth } from '../../contexts/AuthContext';
import './bounty-place-cancel.css';

/** GS BOUNTY_MIN_AMOUNT — client hint only; server remains authoritative. */
export const BOUNTY_MIN_AMOUNT = 1000;
/** Canon placement fee fraction (FEATURES/gameplay/bounties.md). */
export const BOUNTY_PLACEMENT_FEE = 0.1;

export interface PlayerBountyEntry {
  id?: string;
  placed_by?: string;
  placed_by_name?: string;
  amount?: number;
  type?: string;
  placed_at?: string;
  expires_at?: string | null;
}

export interface BountiesOnTargetResponse {
  success?: boolean;
  target_id?: string;
  target_name?: string;
  player_bounties?: PlayerBountyEntry[];
  system_bounties?: PlayerBountyEntry[];
  total_value?: number;
  message?: string;
}

export interface BountyPlaceCancelProps {
  placeBounty?: (targetId: string, amount: number) => Promise<unknown>;
  cancelBounty?: (bountyId: string, targetId: string) => Promise<unknown>;
  getOnTarget?: (playerId: string) => Promise<BountiesOnTargetResponse>;
  /** Override authenticated placer id (tests). */
  currentPlayerId?: string | null;
}

function feeFor(amount: number): number {
  return Math.floor(amount * BOUNTY_PLACEMENT_FEE);
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

/** Transport collapse copy is not gameserver detail (network-collapse densify). */
const isNetworkCollapseMessage = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed)
  );
};

/** Surface gameserver detail when inspect-on-target load fails. */
export function formatBountyInspectLoadError(err: unknown): string {
  const status = httpStatus(err);
  const message = err instanceof Error ? err.message : undefined;
  // Network collapse (fetch TypeError / axios transport) is not gameserver copy.
  const hasServerDetail =
    !(err instanceof TypeError) &&
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !isNetworkCollapseMessage(message) &&
    !/^API Error: \d+$/.test(message.trim());

  if (status === 404) {
    if (hasServerDetail) return message!;
    return 'Target player not found.';
  }

  if (status === 403) {
    if (hasServerDetail) return message!;
    return 'Access denied — you cannot inspect bounties on this target right now.';
  }

  if (hasServerDetail) return message!;
  return 'Failed to load bounties on target';
}

function bountyActionDetail(err: unknown): string | undefined {
  // Network collapse (fetch TypeError / axios transport) is not gameserver copy.
  if (err instanceof TypeError) return undefined;
  const message = err instanceof Error ? err.message : undefined;
  if (typeof message === 'string' && isNetworkCollapseMessage(message)) return undefined;
  if (
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim())
  ) {
    return message.trim();
  }
  return undefined;
}

export function formatBountyPlaceError(err: unknown): string {
  const detail = bountyActionDetail(err);
  if (detail) return detail;
  return 'Failed to place bounty';
}

export function formatBountyCancelError(err: unknown): string {
  const detail = bountyActionDetail(err);
  if (detail) return detail;
  return 'Failed to cancel bounty';
}

const BountyPlaceCancel: React.FC<BountyPlaceCancelProps> = ({
  placeBounty,
  cancelBounty,
  getOnTarget,
  currentPlayerId: currentPlayerIdProp,
}) => {
  const { user } = useAuth();
  const placerId =
    typeof currentPlayerIdProp === 'string' || currentPlayerIdProp === null
      ? currentPlayerIdProp
      : user?.id ?? null;

  const [targetId, setTargetId] = useState('');
  const [amountText, setAmountText] = useState(String(BOUNTY_MIN_AMOUNT));
  const [inspect, setInspect] = useState<BountiesOnTargetResponse | null>(null);
  const [busy, setBusy] = useState<'place' | 'inspect' | 'cancel' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [cancellingId, setCancellingId] = useState<string | null>(null);

  const amount = Number(amountText);
  const amountValid = Number.isFinite(amount) && amount >= BOUNTY_MIN_AMOUNT;
  const fee = amountValid ? feeFor(amount) : 0;
  const totalCost = amountValid ? amount + fee : 0;

  const mine = useMemo(() => {
    const list = Array.isArray(inspect?.player_bounties) ? inspect!.player_bounties! : [];
    if (!placerId) return [];
    return list.filter((b) => b && String(b.placed_by) === String(placerId) && b.id);
  }, [inspect, placerId]);

  const others = useMemo(() => {
    const list = Array.isArray(inspect?.player_bounties) ? inspect!.player_bounties! : [];
    if (!placerId) return list.filter((b) => b?.id);
    return list.filter((b) => b?.id && String(b.placed_by) !== String(placerId));
  }, [inspect, placerId]);

  const runInspect = useCallback(
    async (id: string) => {
      const trimmed = id.trim();
      if (!trimmed) {
        setError('Enter a target player id to inspect or place.');
        return null;
      }
      setBusy('inspect');
      setError(null);
      try {
        const getter = getOnTarget ?? bountyAPI.getOnTarget;
        const result = await getter(trimmed);
        setInspect(result);
        return result;
      } catch (err) {
        setInspect(null);
        setError(formatBountyInspectLoadError(err));
        return null;
      } finally {
        setBusy(null);
      }
    },
    [getOnTarget],
  );

  const onPlace = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      const trimmed = targetId.trim();
      if (!trimmed) {
        setError('Enter a target player id.');
        return;
      }
      if (!amountValid) {
        setError(`Minimum bounty is ${BOUNTY_MIN_AMOUNT.toLocaleString()} credits.`);
        return;
      }
      setBusy('place');
      setError(null);
      setStatus(null);
      try {
        const placer = placeBounty ?? bountyAPI.place;
        const result = (await placer(trimmed, amount)) as {
          bounty_id?: string;
          total_cost?: number;
          fee?: number;
          message?: string;
        };
        setStatus(
          `Placed bounty${result?.bounty_id ? ` ${result.bounty_id}` : ''} — paid ${(
            result?.total_cost ?? totalCost
          ).toLocaleString()} (includes ${(result?.fee ?? fee).toLocaleString()} non-refundable fee).`,
        );
        await runInspect(trimmed);
      } catch (err) {
        setError(formatBountyPlaceError(err));
      } finally {
        setBusy(null);
      }
    },
    [amount, amountValid, fee, placeBounty, runInspect, targetId, totalCost],
  );

  const onCancel = useCallback(
    async (bountyId: string) => {
      const trimmed = targetId.trim();
      if (!trimmed || !bountyId) return;
      setBusy('cancel');
      setCancellingId(bountyId);
      setError(null);
      setStatus(null);
      try {
        const cancelFn = cancelBounty ?? bountyAPI.cancel;
        const result = (await cancelFn(bountyId, trimmed)) as {
          refund?: number;
          message?: string;
        };
        setStatus(
          `Cancelled bounty ${bountyId} — refunded ${(
            typeof result?.refund === 'number' ? result.refund : 0
          ).toLocaleString()} principal (placement fee is non-refundable).`,
        );
        await runInspect(trimmed);
      } catch (err) {
        setError(formatBountyCancelError(err));
      } finally {
        setBusy(null);
        setCancellingId(null);
      }
    },
    [cancelBounty, runInspect, targetId],
  );

  return (
    <div
      className="bounty-place-cancel"
      data-testid="bounty-place-cancel"
      role="region"
      aria-label="Place or cancel a bounty"
    >
      <div className="bounty-place-cancel-header">
        <h3 className="bounty-place-cancel-title">Place / Cancel Bounty</h3>
        <span className="bounty-place-cancel-hint">
          Fee {Math.round(BOUNTY_PLACEMENT_FEE * 100)}% non-refundable · min{' '}
          {BOUNTY_MIN_AMOUNT.toLocaleString()}
        </span>
      </div>

      <form className="bounty-place-cancel-form" onSubmit={onPlace} data-testid="bounty-place-form">
        <label className="bpc-field">
          <span>Target player id</span>
          <input
            type="text"
            name="target_id"
            autoComplete="off"
            value={targetId}
            onChange={(ev) => setTargetId(ev.target.value)}
            data-testid="bounty-place-target"
            disabled={busy != null}
          />
        </label>
        <label className="bpc-field">
          <span>Amount (credits)</span>
          <input
            type="number"
            name="amount"
            min={BOUNTY_MIN_AMOUNT}
            step={1}
            value={amountText}
            onChange={(ev) => setAmountText(ev.target.value)}
            data-testid="bounty-place-amount"
            disabled={busy != null}
          />
        </label>
        <p className="bpc-cost" data-testid="bounty-place-cost">
          {amountValid
            ? `You pay ${totalCost.toLocaleString()} (${amount.toLocaleString()} + ${fee.toLocaleString()} fee)`
            : `Enter at least ${BOUNTY_MIN_AMOUNT.toLocaleString()} credits`}
        </p>
        <div className="bpc-actions">
          <button
            type="submit"
            className="bpc-btn bpc-btn-place"
            data-testid="bounty-place-submit"
            disabled={busy != null || !amountValid || !targetId.trim()}
          >
            {busy === 'place' ? 'Placing…' : 'Place bounty'}
          </button>
          <button
            type="button"
            className="bpc-btn bpc-btn-inspect"
            data-testid="bounty-inspect-submit"
            disabled={busy != null || !targetId.trim()}
            onClick={() => void runInspect(targetId)}
          >
            {busy === 'inspect' ? 'Loading…' : 'Inspect target'}
          </button>
        </div>
      </form>

      {error && (
        <div className="bpc-alert bpc-error" role="alert" data-testid="bounty-place-cancel-error">
          {error}
        </div>
      )}
      {status && (
        <div className="bpc-alert bpc-status" data-testid="bounty-place-cancel-status">
          {status}
        </div>
      )}

      {inspect && (
        <div className="bpc-inspect" data-testid="bounty-on-target">
          <div className="bpc-inspect-header">
            <strong>{inspect.target_name || 'Target'}</strong>
            <span className="bpc-muted">
              total{' '}
              {typeof inspect.total_value === 'number'
                ? inspect.total_value.toLocaleString()
                : '—'}
            </span>
          </div>

          {mine.length === 0 && others.length === 0 && (
            <p className="bpc-muted" data-testid="bounty-on-target-empty">
              No player-placed bounties on this target.
            </p>
          )}

          {mine.length > 0 && (
            <ul className="bpc-list" data-testid="bounty-mine-list">
              {mine.map((b) => (
                <li key={String(b.id)} className="bpc-row" data-testid="bounty-mine-row">
                  <span className="bpc-row-main">
                    Your offer · {(b.amount ?? 0).toLocaleString()} cr
                    {b.expires_at ? ` · expires ${b.expires_at}` : ''}
                  </span>
                  <button
                    type="button"
                    className="bpc-btn bpc-btn-cancel"
                    data-testid="bounty-cancel-submit"
                    disabled={busy != null}
                    onClick={() => void onCancel(String(b.id))}
                  >
                    {cancellingId === b.id ? 'Cancelling…' : 'Cancel (refund principal)'}
                  </button>
                </li>
              ))}
            </ul>
          )}

          {others.length > 0 && (
            <ul className="bpc-list bpc-list-others" data-testid="bounty-others-list">
              {others.map((b) => (
                <li key={String(b.id)} className="bpc-row" data-testid="bounty-other-row">
                  <span className="bpc-row-main">
                    {b.placed_by_name || 'Another placer'} · {(b.amount ?? 0).toLocaleString()} cr
                  </span>
                  <span className="bpc-muted">Not yours — cannot cancel</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
};

export default BountyPlaceCancel;
