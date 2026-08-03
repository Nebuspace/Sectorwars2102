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
      setError(e?.message || 'trade_refresh_failed');
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
        if (!cancelled) setError(e?.message || 'trade_open_failed');
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
    if (session.status === 'SETTLED' || session.status === 'CANCELLED' || session.status === 'EXPIRED' || session.status === 'DECLINED') {
      return;
    }
    const t = window.setInterval(() => {
      refresh(session.id);
    }, 2000);
    return () => window.clearInterval(t);
  }, [session?.id, session?.status, refresh]);

  const amInitiator = session?.initiator_id === myPlayerId;
  const amTarget = session?.target_id === myPlayerId;
  const myOffer = amInitiator ? session?.initiator_offer : session?.target_offer;
  const theirOffer = amInitiator ? session?.target_offer : session?.initiator_offer;

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
      setError(e?.message || 'trade_action_failed');
    } finally {
      setBusy(false);
    }
  };

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
            Status: <strong>{session.status}</strong> · v{session.version}
          </p>

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
                  disabled={busy}
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
