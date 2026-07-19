import React, { useState, useEffect, useRef, useCallback } from 'react';
import { haggleAPI, tradingAPI } from '../../services/api';
import { formatCredits } from '../../utils/formatters';
import './haggle-desk.css';

/* ──────────────────────────────────────────────────────────────────────────
   HaggleDesk — numerical price negotiation (ADR-0079).

   Rendered INSIDE the trade modal body (which is a portal at document.body,
   z-index 10001) so it escapes the cockpit stacking context — the buy/sell
   resource grid stays fully visible beneath the overlay (Scroll Law honored).

   Lifecycle:
     mount → GET /haggle/status (surface lock / cooldown / resumed session)
     OPEN  → POST /haggle/open (quantity is FIXED at open; parent locks it)
     ROUND → POST /haggle/offer per offer until accept | reject | timeout
     ACCEPT→ call onAccepted() so the parent fires the normal buy/sell call;
             the server consumes the stored agreed price transparently.

   `commodity` is the resource_type KEY (e.g. 'Ore') — it must match the
   string the buy/sell call passes, since the agreed price is keyed by it.
   ────────────────────────────────────────────────────────────────────────── */

type Side = 'buy' | 'sell';
type Verdict = 'accept' | 'counter' | 'reject' | 'timeout';

interface Band {
  fair_price: number;
  accept_threshold: number;
  reject_threshold: number;
  side: Side;
}

interface OpeningCard {
  status: 'open';
  commodity: string;
  side: Side;
  quantity: number;
  round: number;
  max_rounds: number;
  personality_type?: string | null;
  haggling_difficulty?: number;
  band: Band;
  price_clamp: { min: number; max: number };
}

interface OfferResult {
  verdict: Verdict;
  round: number;
  max_rounds: number;
  commodity: string;
  side: Side;
  status: 'accepted' | 'rejected' | 'closed' | 'open';
  fair_price: number;
  agreed_price?: number;
  counter_price?: number;
  next_round?: number;
  next_band?: Band;
}

interface StatusResult {
  commodity: string;
  side: Side;
  locked: boolean;
  cooldown_remaining_seconds: number;
  session: {
    status: 'open' | 'accepted' | 'consumed' | 'rejected' | 'closed' | null;
    round: number | null;
    max_rounds: number;
    agreed_price: number | null;
  } | null;
}

/** POST /api/v1/trading/quote's response shape (WO-API-B1) — the
 *  tax-inclusive total the real buy/sell commit will charge/pay. */
interface TradeQuoteResult {
  unit_price: number;
  subtotal: number;
  tax_rate: number;
  tax: number;
  total: number;
}

interface HaggleDeskProps {
  stationId: string;
  /** resource_type KEY — must match the buy/sell resource_type. */
  commodity: string;
  /** Player's direction: 'buy' = buying from station, 'sell' = selling. */
  side: Side;
  /** Quantity is FIXED at open; the parent locks the slider while haggling. */
  quantity: number;
  /** Station's EFFECTIVE tax rate (0 at unowned/untaxed stations — same
   *  field TradingInterface reads off marketInfo.port.tax_rate). Used to
   *  show a tax-INCLUSIVE live-offer preview (mack HIGH-2) before any
   *  session is accepted; the "Deal struck" total instead re-quotes the
   *  server directly once accepted, so this is only the pre-accept path. */
  taxRate: number;
  /** Display name + icon passed through for header parity with the modal. */
  commodityLabel: string;
  /** Optional trader temperament chip label (purely informational). */
  personalityLabel?: string | null;
  /** Close the haggle sub-view and return to the quantity/summary view. */
  onBack: () => void;
  /** Fired once a price is accepted; parent fires the actual buy/sell. */
  onAccepted: (agreedPrice: number) => void;
}

const HaggleDesk: React.FC<HaggleDeskProps> = ({
  stationId,
  commodity,
  side,
  quantity,
  taxRate,
  commodityLabel,
  personalityLabel,
  onBack,
  onAccepted,
}) => {
  // Phase: pre-open status check → open card → round-by-round → terminal
  const [card, setCard] = useState<OpeningCard | null>(null);
  const [lastResult, setLastResult] = useState<OfferResult | null>(null);
  // The live band the player is negotiating against (advances on counter).
  const [activeBand, setActiveBand] = useState<Band | null>(null);
  const [round, setRound] = useState<number>(1);

  const [offerInput, setOfferInput] = useState<string>('');
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // Pre-open gate state (reject hard-lock / cooldown), checked on mount.
  const [locked, setLocked] = useState<boolean>(false);
  const [cooldown, setCooldown] = useState<number>(0);
  const [checking, setChecking] = useState<boolean>(true);

  // Terminal outcome flags so we can render the right closing card.
  const accepted = lastResult?.verdict === 'accept';
  const rejected = lastResult?.verdict === 'reject';
  const timedOut = lastResult?.verdict === 'timeout';
  const terminal = accepted || rejected || timedOut;

  const cooldownTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (cooldownTimer.current) clearInterval(cooldownTimer.current);
    };
  }, []);

  // mack HIGH-2: the "Deal struck" card used to show agreed_price*quantity
  // with NO tax, while buy_resource/sell_resource apply the station's
  // tax_rate on the haggled price exactly like any other trade — shown !=
  // charged at any taxed station, deterministically. Once accepted, the
  // agreed price is stored server-side (accepted, not yet consumed), so
  // POST /trading/quote's haggle-peek + compute_buy_totals/
  // compute_sell_totals reuse gives the EXACT tax-inclusive number the
  // charge will use — single source of truth, no client-side tax math.
  //
  // mack MEDIUM (rev-3): a failed fetch used to fall back to the tax-less
  // estimate behind only a muted note — easy to miss at exactly the
  // "Deal struck!" moment, so the player could commit expecting the WRONG
  // number even though the actual charge is (and always was) correct.
  // acceptedQuoteError now drives a PROMINENT warning + Retry instead
  // (see the render below); acceptedQuoteRetryNonce re-runs this fetch
  // with identical params, mirroring the plain-quote surface's own
  // quoteRetryNonce pattern in TradingInterface.tsx. Confirm stays
  // ENABLED through the error state (deliberately NOT added to its
  // disabled condition below) — the haggle session is single-use, so
  // blocking Confirm on a persistently flaky connection would strand an
  // already-negotiated deal with zero corresponding safety benefit (the
  // charge is server-authoritative regardless of what this preview
  // shows); the warning gives informed consent to proceed without a
  // retry instead.
  const [acceptedQuote, setAcceptedQuote] = useState<TradeQuoteResult | null>(null);
  const [acceptedQuoteLoading, setAcceptedQuoteLoading] = useState(false);
  const [acceptedQuoteError, setAcceptedQuoteError] = useState(false);
  const [acceptedQuoteRetryNonce, setAcceptedQuoteRetryNonce] = useState(0);
  useEffect(() => {
    if (!(accepted && lastResult?.agreed_price != null)) {
      return;
    }
    let cancelled = false;
    setAcceptedQuoteLoading(true);
    setAcceptedQuoteError(false);
    tradingAPI
      .quote(stationId, commodity, quantity, side)
      .then((data: TradeQuoteResult) => {
        if (cancelled || !mountedRef.current) return;
        setAcceptedQuote(data);
        setAcceptedQuoteError(false);
      })
      .catch(() => {
        if (cancelled || !mountedRef.current) return;
        setAcceptedQuoteError(true);
      })
      .finally(() => {
        if (!cancelled && mountedRef.current) setAcceptedQuoteLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [accepted, lastResult?.agreed_price, stationId, commodity, quantity, side, acceptedQuoteRetryNonce]);

  // carry-forward LOW (mack, accepted as a follow-on, not fixed here): this
  // effect and the eventual buy/sell commit both read the station's
  // tax_rate fresh, independently, with no re-check between them — an
  // owner moving the tax lever mid-negotiation could still show one rate
  // here and charge a different one a moment later. Same class as the
  // Phase-2 server-side price/version-token follow-on tracked by the hub.

  // Tick the cooldown down to zero so the player sees it clear in real time.
  useEffect(() => {
    if (cooldown <= 0) {
      if (cooldownTimer.current) {
        clearInterval(cooldownTimer.current);
        cooldownTimer.current = null;
      }
      return;
    }
    if (cooldownTimer.current) return;
    cooldownTimer.current = setInterval(() => {
      setCooldown(prev => {
        const next = prev - 1;
        return next > 0 ? next : 0;
      });
    }, 1000);
    return () => {
      if (cooldownTimer.current) {
        clearInterval(cooldownTimer.current);
        cooldownTimer.current = null;
      }
    };
  }, [cooldown]);

  const errMsg = (e: any): string =>
    e?.message || e?.response?.data?.detail || e?.response?.data?.message ||
    'The trader turned away.';

  // On mount: ask the desk whether this commodity is locked / on cooldown
  // (a prior reject hard-locks for the docking session; accept/timeout sets a
  // 5-min cooldown). Surfacing this BEFORE opening avoids a guaranteed 400.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const s: StatusResult = await haggleAPI.status(stationId, commodity, side);
        if (cancelled || !mountedRef.current) return;
        setLocked(!!s.locked);
        setCooldown(Math.max(0, s.cooldown_remaining_seconds || 0));
        // A previously-accepted-but-unconsumed session means a price is already
        // waiting — let the player complete the trade rather than re-haggle.
        if (s.session?.status === 'accepted' && s.session.agreed_price != null) {
          setLastResult({
            verdict: 'accept',
            round: s.session.round ?? 1,
            max_rounds: s.session.max_rounds,
            commodity,
            side,
            status: 'accepted',
            fair_price: 0,
            agreed_price: s.session.agreed_price,
          });
        }
      } catch (e) {
        // A status failure is non-fatal — the player can still attempt to open;
        // the open call will surface the real lock/cooldown error if any.
        if (!cancelled && mountedRef.current) setError(errMsg(e));
      } finally {
        if (!cancelled && mountedRef.current) setChecking(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [stationId, commodity, side]);

  const handleOpen = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const c: OpeningCard = await haggleAPI.open(stationId, commodity, side, quantity);
      if (!mountedRef.current) return;
      setCard(c);
      setActiveBand(c.band);
      setRound(c.round);
      // Seed the offer with the fair price as a sensible starting figure.
      setOfferInput(String(Math.round(c.band.fair_price)));
    } catch (e: any) {
      if (!mountedRef.current) return;
      const m = errMsg(e);
      setError(m);
      // The open call is where lock/cooldown become authoritative — reflect
      // them so the UI shows the gated state instead of a bare error.
      if (/lock/i.test(m)) setLocked(true);
      const cdMatch = m.match(/cooldown for (\d+)s/i);
      if (cdMatch) setCooldown(parseInt(cdMatch[1], 10));
    } finally {
      if (mountedRef.current) setBusy(false);
    }
  }, [busy, stationId, commodity, side, quantity]);

  const handleSubmitOffer = useCallback(async () => {
    if (busy || !card) return;
    const offer = parseFloat(offerInput);
    if (!Number.isFinite(offer) || offer <= 0) {
      setError('Enter a per-unit offer above zero.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const r: OfferResult = await haggleAPI.offer(stationId, commodity, side, offer);
      if (!mountedRef.current) return;
      setLastResult(r);
      setRound(r.round);
      if (r.verdict === 'counter') {
        // Advance to the next round's band; pre-fill the offer with the
        // trader's counter so the player can accept it or push back.
        if (r.next_band) setActiveBand(r.next_band);
        if (r.next_round != null) setRound(r.next_round);
        if (r.counter_price != null) setOfferInput(String(Math.round(r.counter_price)));
      } else if (r.verdict === 'accept' && r.agreed_price != null) {
        // Session now holds a single-use agreed price; parent fires the trade.
        // (No auto-fire — the player explicitly confirms below.)
      }
    } catch (e) {
      if (mountedRef.current) setError(errMsg(e));
    } finally {
      if (mountedRef.current) setBusy(false);
    }
  }, [busy, card, offerInput, stationId, commodity, side]);

  const clamp = card?.price_clamp;
  const offerNum = parseFloat(offerInput);
  const offerOutOfRange =
    !!clamp &&
    Number.isFinite(offerNum) &&
    (offerNum < clamp.min || offerNum > clamp.max);

  // mack HIGH-2: this is a SPECULATIVE preview of "what if this exact offer
  // gets accepted" — no haggle session exists yet for /trading/quote to
  // peek (the trader might counter instead), so this can't be re-quoted
  // server-side the way the accepted total below is. It CAN still be tax-
  // INCLUSIVE though: Math.floor mirrors compute_buy_totals/
  // compute_sell_totals' int() truncation exactly for non-negative inputs,
  // and buy ADDS tax / sell WITHHOLDS it, same as the server.
  const offerSubtotal = Number.isFinite(offerNum) ? offerNum * quantity : 0;
  const offerTax = Math.floor(offerSubtotal * taxRate);
  const offerTotal = side === 'buy' ? offerSubtotal + offerTax : offerSubtotal - offerTax;

  // ── Render: pre-open gates ──────────────────────────────────────────────
  if (checking) {
    return (
      <div className="haggle-desk">
        <div className="haggle-status-line">Reading the trader…</div>
      </div>
    );
  }

  const directionWord = side === 'buy' ? 'pay' : 'accept';

  return (
    <div className="haggle-desk">
      <div className="haggle-desk-head">
        <div className="haggle-title-block">
          <span className="haggle-title">HAGGLE · {commodityLabel}</span>
          <span className="haggle-side-chip">
            {side === 'buy' ? 'You buy' : 'You sell'} · ×{quantity}
          </span>
          {personalityLabel && (
            <span className="haggle-personality-chip" title="Trader temperament">
              {personalityLabel}
            </span>
          )}
        </div>
        {card && !terminal && (
          <span className="haggle-round-pill">
            Round {round} / {card.max_rounds}
          </span>
        )}
      </div>

      {/* Locked (reject hard-lock) — no further haggling this docking session */}
      {locked && !accepted && (
        <div className="haggle-gate haggle-gate-locked" role="alert">
          <span className="haggle-gate-icon">🔒</span>
          <div>
            <strong>Negotiations closed</strong>
            <p>
              This trader won't haggle {commodityLabel} again until you undock.
              You can still trade at the posted price.
            </p>
          </div>
        </div>
      )}

      {/* Cooldown — a prior accept/timeout cools the desk for a few minutes */}
      {!locked && cooldown > 0 && !card && !accepted && (
        <div className="haggle-gate haggle-gate-cooldown" role="status">
          <span className="haggle-gate-icon">⏳</span>
          <div>
            <strong>The trader needs a moment</strong>
            <p>Try again in {cooldown}s — or trade at the posted price.</p>
          </div>
        </div>
      )}

      {/* Pre-open: invite to start the negotiation */}
      {!card && !locked && cooldown <= 0 && !accepted && (
        <div className="haggle-intro">
          <p className="haggle-intro-copy">
            Negotiate a per-unit price over up to 4 rounds. The quantity (×
            {quantity}) is fixed for this negotiation. A rejection locks this
            commodity until you undock.
          </p>
          <button
            className="haggle-open-btn"
            onClick={handleOpen}
            disabled={busy}
          >
            {busy ? 'Opening…' : 'Open Negotiation'}
          </button>
        </div>
      )}

      {/* Active negotiation band + offer input */}
      {card && activeBand && !terminal && (
        <>
          <div className="haggle-band-card">
            <div className="haggle-band-row">
              <span>Fair price</span>
              <span className="haggle-band-val">{formatCredits(activeBand.fair_price)}</span>
            </div>
            <div className="haggle-band-row">
              <span>They accept {side === 'buy' ? 'from' : 'up to'}</span>
              <span className="haggle-band-val accept">
                {formatCredits(activeBand.accept_threshold)}
              </span>
            </div>
            <div className="haggle-band-row">
              <span>They walk {side === 'buy' ? 'below' : 'above'}</span>
              <span className="haggle-band-val reject">
                {formatCredits(activeBand.reject_threshold)}
              </span>
            </div>
            {clamp && (
              <div className="haggle-clamp-note">
                Offers accepted between {formatCredits(clamp.min)}–{formatCredits(clamp.max)}/unit
              </div>
            )}
          </div>

          {/* Trader's counter (previous round) */}
          {lastResult?.verdict === 'counter' &&
            lastResult.counter_price != null && (
              <div className="haggle-counter-line" role="status">
                The trader counters at{' '}
                <strong>{formatCredits(lastResult.counter_price)}/unit</strong>.
              </div>
            )}

          <div className="haggle-offer-section">
            <label htmlFor="haggle-offer-input">Your offer (per unit)</label>
            <div className="haggle-offer-input-row">
              <input
                id="haggle-offer-input"
                type="number"
                min={clamp?.min}
                max={clamp?.max}
                step="1"
                value={offerInput}
                onChange={e => setOfferInput(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter' && !busy && !offerOutOfRange) {
                    e.preventDefault();
                    handleSubmitOffer();
                  }
                }}
                className={`haggle-offer-input${offerOutOfRange ? ' out-of-range' : ''}`}
                disabled={busy}
              />
              <span className="haggle-offer-unit">cr/unit</span>
            </div>
            {offerOutOfRange && clamp && (
              <div className="haggle-offer-warn">
                Offer must be between {formatCredits(clamp.min)} and {formatCredits(clamp.max)}.
              </div>
            )}
            <div className="haggle-offer-total">
              You'd {directionWord}{' '}
              <strong>
                {Number.isFinite(offerNum) ? formatCredits(offerTotal) : '—'}
              </strong>{' '}
              total for ×{quantity}
              {taxRate > 0 && Number.isFinite(offerNum) && (
                <span className="haggle-tax-note"> (incl. {(taxRate * 100).toFixed(1)}% station tax)</span>
              )}
              .
            </div>
            <button
              className="haggle-submit-btn"
              onClick={handleSubmitOffer}
              disabled={busy || offerOutOfRange || !Number.isFinite(offerNum)}
            >
              {busy ? 'Negotiating…' : 'Make Offer'}
            </button>
          </div>
        </>
      )}

      {/* ── Terminal: ACCEPT ── */}
      {accepted && lastResult?.agreed_price != null && (
        <div className="haggle-outcome haggle-outcome-accept" role="status">
          <span className="haggle-outcome-icon">🤝</span>
          <h4>Deal struck</h4>
          <p className="haggle-agreed">
            Agreed at <strong>{formatCredits(lastResult.agreed_price)}/unit</strong> ·
            total{' '}
            <strong>
              {acceptedQuote
                ? formatCredits(acceptedQuote.total)
                : acceptedQuoteError
                  ? formatCredits(lastResult.agreed_price * quantity)
                  : '…'}
            </strong>{' '}
            for ×{quantity}
            {acceptedQuote && acceptedQuote.tax_rate > 0 && (
              <span className="haggle-tax-note"> (incl. {(acceptedQuote.tax_rate * 100).toFixed(1)}% station tax)</span>
            )}
            .
          </p>
          {/* mack MEDIUM (rev-3): PROMINENT — not the muted haggle-tax-note
              style — because a missed caveat here means committing to a
              number that isn't what gets charged. The charge itself is
              always correct regardless of this fetch's outcome. */}
          {acceptedQuoteError && (
            <div className="haggle-quote-error" role="alert">
              <span>
                Couldn't confirm the tax-inclusive total shown above — you'll
                still be charged the correct amount (incl. station tax)
                regardless. Retry to preview it first.
              </span>
              <button
                type="button"
                className="haggle-quote-retry-btn"
                onClick={() => setAcceptedQuoteRetryNonce(n => n + 1)}
              >
                Retry
              </button>
            </div>
          )}
          <p className="haggle-agreed-note">
            Confirm the trade to lock it in — this price is single-use and is
            forfeit if you cancel.
          </p>
          <button
            className="haggle-confirm-trade-btn"
            onClick={() => onAccepted(lastResult.agreed_price!)}
            disabled={busy || acceptedQuoteLoading}
          >
            {side === 'buy' ? 'Buy' : 'Sell'} at Agreed Price
          </button>
        </div>
      )}

      {/* ── Terminal: REJECT ── */}
      {rejected && (
        <div className="haggle-outcome haggle-outcome-reject" role="alert">
          <span className="haggle-outcome-icon">🚫</span>
          <h4>Walked away</h4>
          <p>
            The trader rejected your offer and won't haggle {commodityLabel}{' '}
            again this docking session. You can still trade at the posted price.
          </p>
          <button className="haggle-back-btn" onClick={onBack}>
            Back to posted price
          </button>
        </div>
      )}

      {/* ── Terminal: TIMEOUT (rounds exhausted) ── */}
      {timedOut && (
        <div className="haggle-outcome haggle-outcome-timeout" role="status">
          <span className="haggle-outcome-icon">⌛</span>
          <h4>No deal</h4>
          <p>
            Negotiations ran out of rounds
            {lastResult?.counter_price != null && (
              <>
                {' '}— their last word was{' '}
                <strong>{formatCredits(lastResult.counter_price)}/unit</strong>
              </>
            )}
            . The desk is briefly on cooldown; trade at the posted price for now.
          </p>
          <button className="haggle-back-btn" onClick={onBack}>
            Back to posted price
          </button>
        </div>
      )}

      {error && !terminal && (
        <div className="haggle-error" role="alert">
          {error}
        </div>
      )}

      {/* Persistent escape hatch back to the standard quantity/summary view,
          except on accept (where confirming the trade is the path forward). */}
      {!accepted && (
        <button className="haggle-back-link" onClick={onBack} disabled={busy}>
          ← Back to posted price
        </button>
      )}
    </div>
  );
};

export default HaggleDesk;
