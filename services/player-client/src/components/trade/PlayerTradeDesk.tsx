import React, { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { tradeAPI } from '../../services/api';
import { formatCredits } from '../../utils/formatters';
import './PlayerTradeDesk.css';

type TradeSession = {
  id: string;
  initiator_id: string;
  target_id: string;
  status: string;
  version: number;
  initiator_offer?: { credits?: number; commodities?: Record<string, number> };
  target_offer?: { credits?: number; commodities?: Record<string, number> };
  initiator_confirmed_version?: number | null;
  target_confirmed_version?: number | null;
};

type Props = {
  /** When set, opens a fresh initiate toward this player (if no open session). */
  targetPlayerId?: string | null;
  myPlayerId: string;
  onClose: () => void;
};

const TERMINAL = new Set(['SETTLED', 'CANCELLED', 'EXPIRED', 'DECLINED']);

/** Map server reason codes (HTTP detail) to cockpit prose. Unknown codes stay readable. */
const TRADE_REASON_COPY: Record<string, string> = {
  cannot_trade_self: 'You cannot trade with yourself.',
  player_not_found: 'That captain is not available.',
  already_in_trade: 'One of you already has an open trade.',
  not_co_located: 'You must be in the same location to trade.',
  session_not_found: 'Trade session not found.',
  not_target: 'Only the invited captain can do that.',
  not_pending_accept: 'This invite is no longer waiting for accept.',
  not_open: 'This trade is not open for staging.',
  not_party: 'You are not a party to this trade.',
  already_terminal: 'This trade has already ended.',
  not_fully_confirmed: 'Both captains must confirm the current offers first.',
  initiator_insufficient_credits: 'You do not have enough credits for this deal.',
  target_insufficient_credits: 'They do not have enough credits for this deal.',
  ship_id_required_for_commodities: 'Pick a ship before offering commodities.',
  trade_refresh_failed: 'Could not refresh the trade desk.',
  trade_open_failed: 'Could not open a trade.',
  trade_action_failed: 'That trade action failed.',
};

const STATUS_COPY: Record<string, string> = {
  SETTLED: 'Deal complete.',
  CANCELLED: 'Trade cancelled.',
  EXPIRED: 'Trade invite expired.',
  DECLINED: 'Trade declined.',
  PENDING_ACCEPT: 'Awaiting accept',
  OPEN: 'Open',
};

function formatTradeError(raw: unknown, fallback: string): string {
  const msg = typeof raw === 'string' ? raw : (raw as any)?.message;
  if (!msg || typeof msg !== 'string') return TRADE_REASON_COPY[fallback] || fallback;
  const key = msg.trim();
  if (TRADE_REASON_COPY[key]) return TRADE_REASON_COPY[key];
  // Throttle / antirmt reasons may be longer free-form — pass through if already prose.
  if (key.includes(' ') && !/^[a-z0-9_]+$/i.test(key)) return key;
  // Snake_case unknown: soften underscores for display rather than dump the code raw.
  if (/^[a-z][a-z0-9_]*$/i.test(key)) {
    return key.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase()) + '.';
  }
  return key;
}

/**
 * Thin P2P trade desk (ADR-0089) — credits-first staging + confirm/cancel.
 * Mounted from TACTICAL TARGET TRADE action.
 */
const PlayerTradeDesk: React.FC<Props> = ({ targetPlayerId, myPlayerId, onClose }) => {
  const [session, setSession] = useState<TradeSession | null>(null);
  const [creditsOffer, setCreditsOffer] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const [fuelAmount, setFuelAmount] = useState(1);
  const [paymentCredits, setPaymentCredits] = useState(0);

  const refresh = useCallback(async (sessionId?: string) => {
    try {
      if (sessionId) {
        const res = await tradeAPI.get(sessionId);
        setSession(res.session ?? null);
        return;
      }
      const res = await tradeAPI.getOpen();
      setSession(res.session ?? null);
    } catch (e: any) {
      setError(formatTradeError(e, 'trade_refresh_failed'));
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setBusy(true);
      setError(null);
      try {
        const open = await tradeAPI.getOpen();
        if (cancelled) return;
        if (open.session) {
          setSession(open.session);
        } else if (targetPlayerId) {
          const res = await tradeAPI.initiate(targetPlayerId);
          if (cancelled) return;
          setSession(res.session ?? null);
          setInfo('Trade invite sent — waiting for accept.');
        }
      } catch (e: any) {
        if (!cancelled) setError(formatTradeError(e, 'trade_open_failed'));
      } finally {
        if (!cancelled) setBusy(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [targetPlayerId]);

  useEffect(() => {
    if (!session?.id) return;
    if (TERMINAL.has(session.status)) return;
    const t = window.setInterval(() => {
      refresh(session.id);
    }, 2000);
    return () => window.clearInterval(t);
  }, [session?.id, session?.status, refresh]);

  const amInitiator = session?.initiator_id === myPlayerId;
  const amTarget = session?.target_id === myPlayerId;
  const deliverRecipientId =
    targetPlayerId ||
    (session
      ? session.initiator_id === myPlayerId
        ? session.target_id
        : session.initiator_id
      : '');
  const myOffer = amInitiator ? session?.initiator_offer : session?.target_offer;
  const theirOffer = amInitiator ? session?.target_offer : session?.initiator_offer;

  const myConfirmed =
    !!session &&
    (amInitiator
      ? session.initiator_confirmed_version === session.version
      : session.target_confirmed_version === session.version);
  const theyConfirmed =
    !!session &&
    (amInitiator
      ? session.target_confirmed_version === session.version
      : session.initiator_confirmed_version === session.version);

  const run = async (fn: () => Promise<any>, okMsg?: string) => {
    setBusy(true);
    setError(null);
    try {
      const res = await fn();
      if (res.session) setSession(res.session);
      else await refresh(session?.id);
      if (res.settled) setInfo(`Deal settled. Tax ${formatCredits(res.tax_paid ?? 0)}.`);
      else if (okMsg) setInfo(okMsg);
    } catch (e: any) {
      setError(formatTradeError(e, 'trade_action_failed'));
    } finally {
      setBusy(false);
    }
  };

  const statusLabel = session ? STATUS_COPY[session.status] || session.status : '';
  const isTerminal = session ? TERMINAL.has(session.status) : false;

  const body = (
    <div className="p2p-trade-desk" role="dialog" aria-modal="true" aria-label="Player trade desk">
      <header className="p2p-trade-desk__header">
        <h2>TRADE DESK</h2>
        <button type="button" className="p2p-trade-desk__close" onClick={onClose} aria-label="Close trade desk">
          ✕
        </button>
      </header>

      {error && (
        <p className="p2p-trade-desk__err" role="alert">
          {error}
        </p>
      )}
      {info && (
        <p className="p2p-trade-desk__info" role="status">
          {info}
        </p>
      )}

      {!session && !busy && <p className="p2p-trade-desk__muted">No open trade session.</p>}

      {session && (
        <>
          <p className="p2p-trade-desk__status">
            Status: <strong>{statusLabel}</strong> · v{session.version}
          </p>

          {isTerminal && (
            <div className="p2p-trade-desk__row">
              <p className="p2p-trade-desk__muted">{STATUS_COPY[session.status] || 'Trade ended.'}</p>
              <button type="button" onClick={onClose}>
                Close
              </button>
            </div>
          )}

          {session.status === 'PENDING_ACCEPT' && amTarget && (
            <div className="p2p-trade-desk__row">
              <button type="button" disabled={busy} onClick={() => run(() => tradeAPI.accept(session.id), 'Trade open.')}>
                Accept
              </button>
              <button type="button" disabled={busy} onClick={() => run(() => tradeAPI.decline(session.id), 'Declined.')}>
                Decline
              </button>
            </div>
          )}

          {session.status === 'PENDING_ACCEPT' && amInitiator && (
            <p className="p2p-trade-desk__muted">Waiting for the other captain to accept…</p>
          )}

          {session.status === 'OPEN' && (
            <>
              <div className="p2p-trade-desk__offers">
                <div>
                  <h3>Your offer</h3>
                  <p>{formatCredits(myOffer?.credits ?? 0)}</p>
                </div>
                <div>
                  <h3>Their offer</h3>
                  <p>{formatCredits(theirOffer?.credits ?? 0)}</p>
                </div>
              </div>

              <p className="p2p-trade-desk__muted" role="status">
                {myConfirmed && theyConfirmed
                  ? 'Both confirmed — settling…'
                  : myConfirmed
                    ? 'You confirmed — waiting for them.'
                    : theyConfirmed
                      ? 'They confirmed — confirm when ready.'
                      : 'Neither captain has confirmed this version yet.'}
              </p>

              <label className="p2p-trade-desk__field">
                Credits to offer
                <input
                  type="number"
                  min={0}
                  value={creditsOffer}
                  disabled={busy}
                  onChange={(e) => setCreditsOffer(Math.max(0, Number(e.target.value) || 0))}
                />
              </label>

              <div className="p2p-trade-desk__row">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    run(
                      () => tradeAPI.offer(session.id, { credits: creditsOffer }),
                      'Offer staged (confirms reset).'
                    )
                  }
                >
                  Stage offer
                </button>
                <button
                  type="button"
                  disabled={busy || myConfirmed}
                  onClick={() => run(() => tradeAPI.confirm(session.id), 'Confirmed.')}
                >
                  Confirm
                </button>
                <button type="button" disabled={busy} onClick={() => run(() => tradeAPI.cancel(session.id), 'Cancelled.')}>
                  Cancel
                </button>
              </div>
            </>
          )}
        </>
      )}

      {deliverRecipientId && (
        <section className="p2p-trade-desk__fuel" data-testid="deliver-fuel">
          <h3>Deliver fuel</h3>
          <p className="p2p-trade-desk__muted">
            Same-sector fuel-for-credits. Fuel goes onto their ship; they still fly Slipdrive.
            Payment comes from their credits.
          </p>
          <label className="p2p-trade-desk__field">
            Fuel amount
            <input
              data-testid="deliver-fuel-amount"
              type="number"
              min={1}
              value={fuelAmount}
              disabled={busy}
              onChange={(e) => setFuelAmount(Math.max(1, Number(e.target.value) || 1))}
            />
          </label>
          <label className="p2p-trade-desk__field">
            Payment (credits from recipient)
            <input
              data-testid="deliver-fuel-payment"
              type="number"
              min={0}
              value={paymentCredits}
              disabled={busy}
              onChange={(e) => setPaymentCredits(Math.max(0, Number(e.target.value) || 0))}
            />
          </label>
          <button
            type="button"
            data-testid="deliver-fuel-submit"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                const res = await tradeAPI.deliverFuel({
                  recipient_player_id: deliverRecipientId,
                  fuel_amount: fuelAmount,
                  payment_credits: paymentCredits,
                });
                const bits = [
                  typeof res?.fuel_delivered === 'number' ? `${res.fuel_delivered} fuel delivered` : 'Fuel delivered.',
                  typeof res?.deliverer_credits === 'number'
                    ? `you ${formatCredits(res.deliverer_credits)}`
                    : null,
                  typeof res?.recipient_credits === 'number'
                    ? `them ${formatCredits(res.recipient_credits)}`
                    : null,
                ].filter(Boolean);
                setInfo(bits.join(' · '));
              } catch (e: unknown) {
                setError(formatTradeError(e, 'trade_action_failed'));
              } finally {
                setBusy(false);
              }
            }}
          >
            Deliver fuel
          </button>
        </section>
      )}
    </div>
  );

  return createPortal(
    <div className="p2p-trade-desk-backdrop" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()}>{body}</div>
    </div>,
    document.body
  );
};

export default PlayerTradeDesk;
