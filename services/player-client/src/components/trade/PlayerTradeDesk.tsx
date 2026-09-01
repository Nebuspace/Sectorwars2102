import React, { useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { GameContext } from '../../contexts/GameContext';
import { tradeAPI } from '../../services/api';
import { formatCredits } from '../../utils/formatters';
import './PlayerTradeDesk.css';

type TradeOffer = {
  credits?: number;
  commodities?: Record<string, number>;
  ship_id?: string | null;
  ships?: string[];
};

type TradeSession = {
  id: string;
  initiator_id: string;
  target_id: string;
  status: string;
  version: number;
  initiator_offer?: TradeOffer;
  target_offer?: TradeOffer;
  initiator_confirmed_version?: number | null;
  target_confirmed_version?: number | null;
};

type ShipOption = {
  id: string;
  name: string;
  cargo?: Record<string, number>;
};

type Props = {
  /** When set, opens a fresh initiate toward this player (if no open session). */
  targetPlayerId?: string | null;
  myPlayerId: string;
  onClose: () => void;
  /** Optional owned ships for cargo source + ship-offer pickers (tests inject). */
  ships?: ShipOption[];
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

const isTradeNetworkCollapseMessage = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed) ||
    /^networkerror$/i.test(trimmed)
  );
};

function httpStatus(err: unknown): number | undefined {
  if (err && typeof err === 'object') {
    const direct = (err as { status?: number }).status;
    if (typeof direct === 'number') return direct;
    const resp = (err as { response?: { status?: number } }).response;
    if (typeof resp?.status === 'number') return resp.status;
  }
  return undefined;
}

const hasTradeServerDetail = (raw: unknown, message: string | undefined): boolean =>
  !(raw instanceof TypeError) &&
  typeof message === 'string' &&
  message.trim().length > 0 &&
  !isTradeNetworkCollapseMessage(message) &&
  !/^API Error: \d+$/.test(message.trim());

export function formatTradeError(raw: unknown, fallback: string): string {
  const mappedFallback = TRADE_REASON_COPY[fallback] || fallback;
  if (raw instanceof TypeError) return mappedFallback;
  const msg = typeof raw === 'string' ? raw : (raw as any)?.message;
  if (!msg || typeof msg !== 'string') return mappedFallback;
  const key = msg.trim();

  const status = httpStatus(raw);
  if (status === 403) {
    if (hasTradeServerDetail(raw, msg)) {
      if (TRADE_REASON_COPY[key]) return TRADE_REASON_COPY[key];
      return key;
    }
    return 'Access denied — you cannot trade right now.';
  }

  if (isTradeNetworkCollapseMessage(key)) return mappedFallback;
  if (TRADE_REASON_COPY[key]) return TRADE_REASON_COPY[key];
  // Throttle / antirmt reasons may be longer free-form — pass through if already prose.
  if (key.includes(' ') && !/^[a-z0-9_]+$/i.test(key)) return key;
  // Snake_case unknown: soften underscores for display rather than dump the code raw.
  if (/^[a-z][a-z0-9_]*$/i.test(key)) {
    return key.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase()) + '.';
  }
  return key;
}

function OfferSummary({ offer, shipNames }: { offer?: TradeOffer; shipNames: Map<string, string> }) {
  const credits = offer?.credits ?? 0;
  const commodities = offer?.commodities ?? {};
  const commodityLines = Object.entries(commodities).filter(([, qty]) => qty > 0);
  const ships = offer?.ships ?? [];

  return (
    <div className="p2p-trade-desk__offer-body" data-testid="offer-summary">
      <p>{formatCredits(credits)}</p>
      {commodityLines.length > 0 && (
        <ul className="p2p-trade-desk__offer-list" aria-label="Commodity lines">
          {commodityLines.map(([name, qty]) => (
            <li key={name}>
              {name} × {qty}
            </li>
          ))}
        </ul>
      )}
      {ships.length > 0 && (
        <ul className="p2p-trade-desk__offer-list" aria-label="Ship lines">
          {ships.map((id) => (
            <li key={id}>Ship: {shipNames.get(id) ?? id.slice(0, 8)}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * P2P trade desk (ADR-0089) — credits + commodity/ship staging via tradeAPI.offer.
 * Mounted from TACTICAL TARGET TRADE action.
 */
const PlayerTradeDesk: React.FC<Props> = ({ targetPlayerId, myPlayerId, onClose, ships: shipsProp }) => {
  const game = useContext(GameContext);
  const ownedShips: ShipOption[] = useMemo(() => {
    if (shipsProp) return shipsProp;
    return (game?.ships ?? []).map((s) => ({
      id: s.id,
      name: s.name,
      cargo: s.cargo,
    }));
  }, [shipsProp, game?.ships]);

  const shipNames = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of ownedShips) m.set(s.id, s.name);
    return m;
  }, [ownedShips]);

  const [session, setSession] = useState<TradeSession | null>(null);
  const [creditsOffer, setCreditsOffer] = useState(0);
  const [commodityDraft, setCommodityDraft] = useState<Record<string, number>>({});
  const [commodityKey, setCommodityKey] = useState('');
  const [commodityQty, setCommodityQty] = useState(0);
  const [cargoShipId, setCargoShipId] = useState('');
  const [offeredShipIds, setOfferedShipIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [fuelAmount, setFuelAmount] = useState(1);
  const [fuelPayment, setFuelPayment] = useState(0);

  const cargoShip = ownedShips.find((s) => s.id === cargoShipId);
  const cargoKeys = Object.keys(cargoShip?.cargo ?? {}).filter((k) => (cargoShip?.cargo?.[k] ?? 0) > 0);

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

  // Default cargo ship to current ship when available.
  useEffect(() => {
    if (cargoShipId) return;
    const currentId = game?.currentShip?.id;
    if (currentId && ownedShips.some((s) => s.id === currentId)) {
      setCargoShipId(currentId);
      return;
    }
    if (ownedShips.length === 1) setCargoShipId(ownedShips[0].id);
  }, [cargoShipId, game?.currentShip?.id, ownedShips]);

  const amInitiator = session?.initiator_id === myPlayerId;
  const amTarget = session?.target_id === myPlayerId;
  const fuelRecipientId =
    targetPlayerId ||
    (session
      ? session.initiator_id === myPlayerId
        ? session.target_id
        : session.initiator_id
      : null);

  const deliverFuel = async () => {
    if (!fuelRecipientId) return;
    setBusy(true);
    setError(null);
    try {
      await tradeAPI.deliverFuel({
        recipientPlayerId: fuelRecipientId,
        fuelAmount: Math.max(1, Math.floor(Number(fuelAmount)) || 1),
        paymentCredits: Math.max(0, Math.floor(Number(fuelPayment)) || 0),
      });
      setInfo('Fuel delivered to their ship.');
    } catch (e: unknown) {
      setError(formatTradeError(e, 'trade_action_failed'));
    } finally {
      setBusy(false);
    }
  };
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

  const addCommodityLine = () => {
    const key = commodityKey.trim();
    const qty = Math.max(0, Math.floor(commodityQty) || 0);
    if (!key || qty <= 0) return;
    setCommodityDraft((prev) => ({ ...prev, [key]: (prev[key] ?? 0) + qty }));
    setCommodityQty(0);
  };

  const removeCommodityLine = (key: string) => {
    setCommodityDraft((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  };

  const toggleOfferedShip = (id: string) => {
    setOfferedShipIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  const stageOffer = () => {
    if (!session) return;
    const commodities = { ...commodityDraft };
    const hasCommodities = Object.values(commodities).some((q) => q > 0);
    run(
      () =>
        tradeAPI.offer(session.id, {
          credits: creditsOffer,
          commodities: hasCommodities ? commodities : {},
          ship_id: hasCommodities ? cargoShipId || null : null,
          ships: offeredShipIds,
        }),
      'Offer staged (confirms reset).'
    );
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

      {fuelRecipientId && (
        <fieldset className="p2p-trade-desk__fuel" data-testid="deliver-fuel">
          <legend>Deliver fuel</legend>
          <p className="p2p-trade-desk__muted">
            Same-sector handoff onto their ship. Not a contract. They still fly their own Slipdrive.
          </p>
          <label className="p2p-trade-desk__field">
            Fuel amount
            <input
              type="number"
              min={1}
              data-testid="fuel-amount"
              value={fuelAmount}
              disabled={busy}
              aria-label="Fuel amount to deliver"
              onChange={(e) => setFuelAmount(Math.max(1, Number(e.target.value) || 1))}
            />
          </label>
          <label className="p2p-trade-desk__field">
            Payment (their credits)
            <input
              type="number"
              min={0}
              data-testid="fuel-payment"
              value={fuelPayment}
              disabled={busy}
              aria-label="Payment credits charged to recipient"
              onChange={(e) => setFuelPayment(Math.max(0, Number(e.target.value) || 0))}
            />
          </label>
          <div className="p2p-trade-desk__row">
            <button type="button" disabled={busy} data-testid="deliver-fuel-submit" onClick={deliverFuel}>
              Deliver fuel
            </button>
          </div>
        </fieldset>
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
                  <OfferSummary offer={myOffer} shipNames={shipNames} />
                </div>
                <div>
                  <h3>Their offer</h3>
                  <OfferSummary offer={theirOffer} shipNames={shipNames} />
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
                  data-testid="credits-offer"
                  value={creditsOffer}
                  disabled={busy}
                  onChange={(e) => setCreditsOffer(Math.max(0, Number(e.target.value) || 0))}
                />
              </label>

              <label className="p2p-trade-desk__field">
                Cargo ship (required for commodities)
                <select
                  data-testid="cargo-ship-select"
                  value={cargoShipId}
                  disabled={busy || ownedShips.length === 0}
                  onChange={(e) => setCargoShipId(e.target.value)}
                  aria-label="Cargo ship for commodities"
                >
                  <option value="">— select ship —</option>
                  {ownedShips.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </label>

              <div className="p2p-trade-desk__commodity-stage" data-testid="commodity-stage">
                <label className="p2p-trade-desk__field">
                  Commodity
                  {cargoKeys.length > 0 ? (
                    <select
                      data-testid="commodity-key"
                      value={commodityKey}
                      disabled={busy}
                      onChange={(e) => setCommodityKey(e.target.value)}
                      aria-label="Commodity to offer"
                    >
                      <option value="">— pick cargo —</option>
                      {cargoKeys.map((k) => (
                        <option key={k} value={k}>
                          {k} ({cargoShip?.cargo?.[k] ?? 0})
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      type="text"
                      data-testid="commodity-key"
                      value={commodityKey}
                      disabled={busy}
                      placeholder="e.g. ore"
                      aria-label="Commodity to offer"
                      onChange={(e) => setCommodityKey(e.target.value)}
                    />
                  )}
                </label>
                <label className="p2p-trade-desk__field">
                  Quantity
                  <input
                    type="number"
                    min={0}
                    data-testid="commodity-qty"
                    value={commodityQty}
                    disabled={busy}
                    onChange={(e) => setCommodityQty(Math.max(0, Number(e.target.value) || 0))}
                  />
                </label>
                <button type="button" disabled={busy} data-testid="add-commodity" onClick={addCommodityLine}>
                  Add commodity
                </button>
              </div>

              {Object.keys(commodityDraft).length > 0 && (
                <ul className="p2p-trade-desk__draft-list" data-testid="commodity-draft" aria-label="Staged commodities">
                  {Object.entries(commodityDraft).map(([name, qty]) => (
                    <li key={name}>
                      {name} × {qty}{' '}
                      <button type="button" disabled={busy} onClick={() => removeCommodityLine(name)} aria-label={`Remove ${name}`}>
                        Remove
                      </button>
                    </li>
                  ))}
                </ul>
              )}

              {ownedShips.length > 0 && (
                <fieldset className="p2p-trade-desk__ships" data-testid="ships-offer">
                  <legend>Ships to offer</legend>
                  {ownedShips.map((s) => (
                    <label key={s.id} className="p2p-trade-desk__check">
                      <input
                        type="checkbox"
                        checked={offeredShipIds.includes(s.id)}
                        disabled={busy}
                        onChange={() => toggleOfferedShip(s.id)}
                        aria-label={`Offer ship ${s.name}`}
                      />
                      {s.name}
                    </label>
                  ))}
                </fieldset>
              )}

              <div className="p2p-trade-desk__row">
                <button type="button" disabled={busy} data-testid="stage-offer" onClick={stageOffer}>
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
