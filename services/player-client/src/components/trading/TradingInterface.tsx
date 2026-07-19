import React, { useState, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useGame, StationSlips } from '../../contexts/GameContext';
import { useWebSocket } from '../../contexts/WebSocketContext';
import CockpitInstrument from '../cockpit/CockpitInstrument';
import { StationClassBadge, getTraderPersonality } from '../common/stationIdentity';
import { formatCredits } from '../../utils/formatters';
import { resourceIcon } from '../../services/resourceCatalog';
import apiClient from '../../services/apiClient';
import marketStreamService from '../../services/marketStream';
import HaggleDesk from './HaggleDesk';
import RoutePlannerPanel from './RoutePlannerPanel';
import './trading-interface.css';

/* TRADE LEDGER shell (Law 3) — module-level so the frame keeps its
   identity across not-docked/market renders. Used only when the component
   is a standalone route; embedded usage (GameDashboard docked monitor,
   SpaceDockInterface) stays bare via Fragment. */
const TradeLedgerShell: React.FC<{ children?: React.ReactNode }> = ({ children }) => (
  <CockpitInstrument title="TRADE LEDGER" accent="#FFB000" subtitle="COMMODITY EXCHANGE">
    {children}
  </CockpitInstrument>
);

interface Resource {
  name: string;
  quantity: number;
  buy_price: number;
  sell_price: number;
  station_buys: boolean;
  station_sells: boolean;
  /** Player-trade demand signal (ADR-0062 E-V4) — NPC trader activity
   *  feeds a separate field and never skews this indicator. */
  player_demand_score?: number;
  last_updated?: string;
  /** WO-ECON-MKT-TIMESERIES: computed server-side on every reprice
   *  (TradingService.update_market_prices) — positive = rising, negative =
   *  falling, raw fraction (0.01 = 1%), not rank-adjusted. */
  price_trend?: number;
  previous_buy_price?: number | null;
  previous_sell_price?: number | null;
}

/** One PriceHistory snapshot, as served by GET /trading/market/{id}/history. */
interface PriceHistoryPoint {
  snapshot_date: string;
  snapshot_type: string;
  buy_price: number;
  sell_price: number;
  quantity: number;
}

interface TradeCalculation {
  resourceType: string;
  quantity: number;
  unitPrice: number;
  totalCost: number;
  isAffordable: boolean;
  fitsInCargo: boolean;
}

/** Server-authoritative price/tax/total preview from POST /trading/quote
 *  (WO-API-B1) — the single source of truth the trade modal renders instead
 *  of recomputing this math client-side. resourceType/quantity/action echo
 *  back the params the quote was computed for, so a response that lands
 *  after the player has already changed the quantity/mode/commodity can be
 *  told apart from a fresh one (see quoteIsCurrent below). */
interface TradeQuote {
  resourceType: string;
  quantity: number;
  action: 'buy' | 'sell';
  unitPrice: number;
  subtotal: number;
  taxRate: number;
  tax: number;
  total: number;
}

interface BumpableOccupant {
  player_id: string;
  name: string;
  tenure_hours: number;
}

// Payload surfaced by GameContext.dockAtStation when the station's
// transient slips are all occupied (HTTP 409 — requester auto-enqueued)
interface DockFullInfo {
  stationId: string;
  stationName: string;
  detail?: string;
  slips: { capacity: number; occupied: number };
  queue_position?: number | null;
  bumpable: BumpableOccupant[];
  bump_cost: number;
}

const formatName = (name: string) => name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

// Mirrors PRICE_TREND_EPSILON in npc_scheduler_service.py (WO-ECON-MKT-
// TIMESERIES, NO-CANON — proposed to DECISIONS alongside the retention
// window): a move within +/-0.5% reads as flat rather than noise-flickering
// an arrow every reprice.
const TREND_EPSILON = 0.005;

const trendGlyph = (trend?: number | null): { glyph: string; label: string; cls: string } => {
  if (trend === undefined || trend === null) {
    return { glyph: '–', label: 'No trend data yet', cls: 'flat' };
  }
  if (trend > TREND_EPSILON) {
    return { glyph: '▲', label: `Price rising ${(trend * 100).toFixed(1)}%`, cls: 'up' };
  }
  if (trend < -TREND_EPSILON) {
    return { glyph: '▼', label: `Price falling ${(Math.abs(trend) * 100).toFixed(1)}%`, cls: 'down' };
  }
  return { glyph: '–', label: 'Price steady', cls: 'flat' };
};

/** Inline SVG sparkline from a commodity's PriceHistory series — midpoint of
 *  buy/sell per snapshot, normalized to the series' own min/max so a flat
 *  market still draws a visible (if straight) line. */
const PriceSparkline: React.FC<{ points: PriceHistoryPoint[] }> = ({ points }) => {
  const width = 100;
  const height = 28;
  const mids = points.map(p => (p.buy_price + p.sell_price) / 2);
  const min = Math.min(...mids);
  const max = Math.max(...mids);
  const range = max - min || 1;
  const coords = mids
    .map((v, i) => {
      const x = mids.length > 1 ? (i / (mids.length - 1)) * width : width / 2;
      const y = height - ((v - min) / range) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
  const rising = mids.length > 1 && mids[mids.length - 1] >= mids[0];

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="price-sparkline-svg"
      preserveAspectRatio="none"
      role="img"
      aria-label={`Price history sparkline, ${points.length} snapshot${points.length === 1 ? '' : 's'}, trending ${rising ? 'up' : 'down'}`}
    >
      <polyline points={coords} className={`sparkline-line ${rising ? 'up' : 'down'}`} fill="none" />
    </svg>
  );
};

interface TradingInterfaceProps {
  onClose?: () => void;
}

const TradingInterface: React.FC<TradingInterfaceProps> = ({ onClose }) => {
  // Wrap in the cockpit shell + TRADE LEDGER instrument when standalone
  // (no onClose prop = used as a route). When embedded (onClose provided —
  // GameDashboard docked monitor, SpaceDockInterface), render bare so the
  // parent's shell isn't duplicated.
  const isStandalone = !onClose;
  const Wrapper = isStandalone ? TradeLedgerShell : React.Fragment;

  const {
    playerState,
    currentShip,
    marketInfo,
    getMarketInfo,
    buyResource,
    sellResource,
    dockAtStation,
    getStationSlips,
    bumpDockOccupant,
    stationsInSector,
    isLoading,
    error
  } = useGame();

  // Use stationsInSector as ports (they're the same concept in this game)
  const portsInSector = stationsInSector || [];

  const { addNotification, isConnected } = useWebSocket();

  // All hooks must be called before any early returns
  const [selectedPort, setSelectedPort] = useState<string>('');
  const [selectedResource, setSelectedResource] = useState<string>('');
  const [tradeQuantity, setTradeQuantity] = useState<number>(1);
  // Local in-flight guard: the global isLoading no longer flips on trades
  // (initial-hydration-only semantics), so double-submit protection lives here.
  const [isExecuting, setIsExecuting] = useState<boolean>(false);
  // mack MEDIUM: isExecuting (state) only disables the button AFTER a
  // re-render, which is too late to stop a fast double-click firing
  // executeTrade twice before the first render lands (the 2nd call finds a
  // haggle already consumed and silently re-buys at the full posted price).
  // This ref is checked+set synchronously before the first await, closing
  // that window; isExecuting stays purely to drive the disabled/label UI.
  // Scope (mack, accepted as a follow-on, not fixed here): this is a
  // SINGLE-TAB guard — a second browser tab/window has its own React tree
  // and its own executingRef, so the same posted-price double-charge race
  // isn't closed across tabs. That needs server-side idempotency (a
  // client-supplied request key the commit path dedupes on), tracked by
  // the hub as a follow-on to this WO, not attempted here.
  const executingRef = useRef(false);
  const [tradeMode, setTradeMode] = useState<'buy' | 'sell'>('buy');
  const [tradeCalculation, setTradeCalculation] = useState<TradeCalculation | null>(null);
  // WO-API-B1: server-authoritative price/tax/total preview, replacing the
  // client's own duplicate formula. `quote` is cleared (and `quoteLoading`
  // set) whenever the params it's stale for don't match the current
  // selection — see the fetch effect and quoteIsCurrent below.
  const [quote, setQuote] = useState<TradeQuote | null>(null);
  const [quoteLoading, setQuoteLoading] = useState(false);
  // mack LOW: a swallowed quote-fetch failure used to leave the previous
  // (mismatched) quote in place forever with no message and no way to
  // retry short of nudging a param — Confirm just silently stayed disabled.
  // quoteError drives a visible retry affordance; quoteRetryNonce is bumped
  // by that button to force the fetch effect to re-run with IDENTICAL
  // params (its dependency array wouldn't otherwise change).
  const [quoteError, setQuoteError] = useState(false);
  const [quoteRetryNonce, setQuoteRetryNonce] = useState(0);
  const [showConfirmDialog, setShowConfirmDialog] = useState(false);
  // ADR-0079: when true, the trade modal shows the numerical haggle desk in
  // place of the quantity/summary body. Quantity is FROZEN while haggling (the
  // session is opened against a fixed quantity), so we capture it on entry.
  const [haggleMode, setHaggleMode] = useState(false);
  const [haggleQuantity, setHaggleQuantity] = useState<number>(1);
  const [dockingStationId, setDockingStationId] = useState<string | null>(null);

  // Transient slip availability per station (lazy, fetched when the
  // undocked port list renders)
  const [slipsByStation, setSlipsByStation] = useState<Record<string, StationSlips>>({});
  const [dockFull, setDockFull] = useState<DockFullInfo | null>(null);
  const [bumpConfirming, setBumpConfirming] = useState(false);
  const [bumping, setBumping] = useState(false);
  const [bumpError, setBumpError] = useState<string | null>(null);

  // WO-ECON-MKT-TIMESERIES: which commodity's sparkline is expanded (one at a
  // time), and the fetched history per "station:commodity" key so switching
  // stations never shows a stale series under the same commodity name.
  const [expandedSparkline, setExpandedSparkline] = useState<string | null>(null);
  const [historyByKey, setHistoryByKey] = useState<Record<string, PriceHistoryPoint[] | 'loading' | 'error'>>({});

  // WO-RT-MARKET-STREAM-CLIENT: live price overlay on top of the REST
  // snapshot in marketInfo.resources, keyed by commodity. Only buy_price/
  // sell_price are tracked — everything else (quantity, station_sells/buys,
  // trend) still comes from the REST snapshot, refreshed after each trade.
  const [liveOverrides, setLiveOverrides] = useState<Record<string, { buy_price?: number; sell_price?: number }>>({});
  // Which commodities are mid-flash and in which direction, for a brief
  // highlight on the price cell. Duration/styling is NO-CANON (see
  // trading-interface.css price-flash-* rules) — propose+flag.
  const [priceFlash, setPriceFlash] = useState<Record<string, 'up' | 'down'>>({});
  const flashTimers = useRef<Record<string, number>>({});

  // Track which port we've already fetched market info for
  const lastFetchedPortId = useRef<string | null>(null);

  // Track which stations we've already requested slip info for
  const slipsFetchedFor = useRef<Set<string>>(new Set());

  // Lazily load slip availability for each port shown in the undocked list
  useEffect(() => {
    if (playerState?.is_docked) {
      // Occupancy changes while we're docked; refetch fresh after undocking
      slipsFetchedFor.current.clear();
      return;
    }
    portsInSector.forEach(port => {
      if (slipsFetchedFor.current.has(port.id)) return;
      getStationSlips(port.id).then(info => {
        if (info) {
          // Mark fetched only on success so a transient failure retries
          slipsFetchedFor.current.add(port.id);
          setSlipsByStation(prev => ({ ...prev, [port.id]: info }));
        }
      });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playerState?.is_docked, portsInSector]);

  // Auto-select first port if only one available (run once on mount)
  const firstPortId = portsInSector.length === 1 ? portsInSector[0].id : null;
  useEffect(() => {
    if (firstPortId && !selectedPort) {
      setSelectedPort(firstPortId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [firstPortId]); // Only react to actual port ID changes, not array reference changes

  // Load market info when port is selected - with deduplication
  useEffect(() => {
    if (selectedPort && selectedPort !== lastFetchedPortId.current) {
      lastFetchedPortId.current = selectedPort;
      getMarketInfo(selectedPort);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedPort]); // Only trigger when selectedPort changes

  const triggerFlash = (commodity: string, direction: 'up' | 'down') => {
    setPriceFlash(prev => ({ ...prev, [commodity]: direction }));
    if (flashTimers.current[commodity]) {
      window.clearTimeout(flashTimers.current[commodity]);
    }
    flashTimers.current[commodity] = window.setTimeout(() => {
      setPriceFlash(prev => {
        const next = { ...prev };
        delete next[commodity];
        return next;
      });
      delete flashTimers.current[commodity];
    }, 900); // NO-CANON: flash duration — propose+flag
  };

  // Subscribe to the live market stream for exactly the commodities this
  // docked port trades (the server has no per-port filter, only
  // per-commodity — see marketStream.ts's docstring), scoped to the
  // resource keys the REST snapshot already told us about. Recomputed only
  // when the docked port or its commodity set changes, not on every
  // marketInfo refresh (e.g. after a trade) — the commodity set at a given
  // port is stable within a dock session.
  const commodityKey = marketInfo ? Object.keys(marketInfo.resources).sort().join(',') : '';
  useEffect(() => {
    if (!playerState?.is_docked || !selectedPort || !commodityKey) {
      marketStreamService.disconnect();
      setLiveOverrides({});
      return;
    }

    setLiveOverrides({});
    const commodities = commodityKey.split(',');

    const unsubscribe = marketStreamService.onUpdate((message) => {
      const { commodity, data } = message;
      if (!commodity) return;
      // Scope to the docked port: a commodity channel spans every station
      // trading that commodity, not just this one.
      if (data.station_id && data.station_id !== selectedPort) return;
      if (data.buy_price === undefined && data.sell_price === undefined) return;

      setLiveOverrides(prev => {
        const priorBuy = prev[commodity]?.buy_price ?? marketInfo?.resources[commodity]?.buy_price;
        const nextBuy = data.buy_price ?? prev[commodity]?.buy_price;
        const nextSell = data.sell_price ?? prev[commodity]?.sell_price;

        // Flash direction keyed off buy_price (mirrors the sparkline's own
        // buy/sell-midpoint convention) — NO-CANON, propose+flag.
        if (priorBuy !== undefined && nextBuy !== undefined && nextBuy !== priorBuy) {
          triggerFlash(commodity, nextBuy > priorBuy ? 'up' : 'down');
        }

        return { ...prev, [commodity]: { buy_price: nextBuy, sell_price: nextSell } };
      });
    });

    marketStreamService.connect(commodities);

    return () => {
      unsubscribe();
      marketStreamService.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playerState?.is_docked, selectedPort, commodityKey]);

  // Belt-and-braces: clear any in-flight flash timers on unmount (undock
  // already tears them down implicitly via the effect above re-running with
  // fresh state, but a hard unmount mid-flash should not leak timers).
  useEffect(() => {
    return () => {
      Object.values(flashTimers.current).forEach(id => window.clearTimeout(id));
    };
  }, []);

  // Fetch price history for the expanded sparkline — keyed by
  // "station:commodity" so a cached series never bleeds across stations, and
  // never refetched once cached (the sweep is hourly; a mid-session refetch
  // buys nothing). GET returns [] rather than 404/500 pre-sweep, so an empty
  // array is a legitimate cached result, not a retry trigger.
  useEffect(() => {
    if (!expandedSparkline || !selectedPort) return;
    const key = `${selectedPort}:${expandedSparkline}`;
    if (historyByKey[key] !== undefined) return;

    setHistoryByKey(prev => ({ ...prev, [key]: 'loading' }));
    apiClient
      .get(`/api/v1/trading/market/${selectedPort}/history`, {
        params: { commodity: expandedSparkline, hours: 24 * 7 }
      })
      .then(response => {
        setHistoryByKey(prev => ({ ...prev, [key]: response.data?.history ?? [] }));
      })
      .catch(() => {
        setHistoryByKey(prev => ({ ...prev, [key]: 'error' }));
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expandedSparkline, selectedPort]);

  const toggleSparkline = (resourceType: string, e: React.MouseEvent) => {
    // Sparkline toggle sits inside the (sometimes-clickable) resource card —
    // never let it also fire the card's own select/open-modal handler.
    e.stopPropagation();
    setExpandedSparkline(prev => (prev === resourceType ? null : resourceType));
  };

  // Helper to safely get cargo used space
  const getCargoUsed = (): number => {
    if (!currentShip?.cargo) return 0;
    // Cargo can be {used, capacity, contents} OR {resource: amount}
    if (typeof currentShip.cargo === 'object' && 'used' in currentShip.cargo) {
      return Number(currentShip.cargo.used) || 0;
    }
    // Legacy format: sum up resource amounts, but only numbers
    return Object.values(currentShip.cargo)
      .filter((v): v is number => typeof v === 'number')
      .reduce((a, b) => a + b, 0);
  };

  // Helper to safely get cargo capacity
  const getCargoCapacity = (): number => {
    if (!currentShip) return 0;
    // Check cargo.capacity first, then cargo_capacity
    if (currentShip.cargo && typeof currentShip.cargo === 'object' && 'capacity' in currentShip.cargo) {
      return Number(currentShip.cargo.capacity) || 0;
    }
    return currentShip.cargo_capacity || 0;
  };

  // Helper to get player's quantity of a resource
  const getPlayerResourceAmount = (resourceType: string): number => {
    if (!currentShip?.cargo) return 0;
    // Check contents field first (new format)
    if (typeof currentShip.cargo === 'object' && 'contents' in currentShip.cargo) {
      const contents = currentShip.cargo.contents as unknown as Record<string, number>;
      return Number(contents[resourceType]) || 0;
    }
    // Legacy format
    return Number((currentShip.cargo as Record<string, number>)[resourceType]) || 0;
  };

  // Helper to get total units of cargo the player is carrying
  const getPlayerCargoCount = (): number => {
    if (!currentShip?.cargo) return 0;
    // Check contents field first (new format)
    if (typeof currentShip.cargo === 'object' && 'contents' in currentShip.cargo) {
      const contents = currentShip.cargo.contents as unknown as Record<string, number>;
      return Object.values(contents)
        .filter((v): v is number => typeof v === 'number')
        .reduce((a, b) => a + b, 0);
    }
    // Legacy format
    return Object.values(currentShip.cargo as Record<string, number>)
      .filter((v): v is number => typeof v === 'number')
      .reduce((a, b) => a + b, 0);
  };

  // WO-API-B1: fetch the server-authoritative price/tax/total preview
  // instead of recomputing it locally. Debounced (250ms) so dragging the
  // quantity slider doesn't fire a request per pixel; the in-flight
  // request is cancelled (via the `cancelled` flag) if params change again
  // before it resolves, so a slow/stale response can never clobber a
  // fresher one. Skipped entirely while haggling — the haggle desk shows
  // its own live negotiation state, not this preview.
  useEffect(() => {
    if (!selectedPort || !selectedResource || tradeQuantity <= 0 || haggleMode) {
      return;
    }
    let cancelled = false;
    setQuoteLoading(true);
    setQuoteError(false);
    const timer = window.setTimeout(() => {
      apiClient
        .post('/api/v1/trading/quote', {
          station_id: selectedPort,
          resource_type: selectedResource,
          quantity: tradeQuantity,
          action: tradeMode,
        })
        .then(response => {
          if (cancelled) return;
          const data = response.data;
          setQuote({
            resourceType: data.resource_type,
            quantity: data.quantity,
            action: data.action,
            unitPrice: data.unit_price,
            subtotal: data.subtotal,
            taxRate: data.tax_rate,
            tax: data.tax,
            total: data.total,
          });
          setQuoteError(false);
        })
        .catch(() => {
          if (cancelled) return;
          // mack LOW: surface the failure (a banner + Retry button in the
          // render below) instead of silently leaving the previous
          // (mismatched) quote in place forever. canExecuteTrade's
          // freshness check still keeps Confirm gated on a CURRENT quote,
          // so this is purely "tell the player and let them retry."
          setQuoteError(true);
        })
        .finally(() => {
          if (!cancelled) setQuoteLoading(false);
        });
    }, 250); // NO-CANON: debounce window — propose+flag (mirrors the price-flash duration precedent above)

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [selectedPort, selectedResource, tradeQuantity, tradeMode, haggleMode, quoteRetryNonce]);

  // Derive affordability/cargo-fit from the server quote — ONLY once it's
  // current for the exact params on screen (see quoteIsCurrent below); a
  // stale or in-flight quote must never gate Confirm on the WRONG total.
  useEffect(() => {
    const quoteIsCurrent =
      !!quote &&
      quote.resourceType === selectedResource &&
      quote.action === tradeMode &&
      quote.quantity === tradeQuantity;

    if (selectedResource && marketInfo && tradeQuantity > 0 && quoteIsCurrent && quote) {
      const isAffordable = tradeMode === 'buy'
        ? (playerState?.credits || 0) >= quote.total
        : true; // Can always sell if you have the resource

      const currentCargo = getCargoUsed();
      const cargoCapacity = getCargoCapacity();
      const fitsInCargo = tradeMode === 'buy'
        ? (currentCargo + tradeQuantity) <= cargoCapacity
        : true; // Selling always frees up cargo space

      setTradeCalculation({
        resourceType: selectedResource,
        quantity: tradeQuantity,
        unitPrice: quote.unitPrice,
        totalCost: quote.total,
        isAffordable,
        fitsInCargo
      });
    } else {
      setTradeCalculation(null);
    }
  }, [selectedResource, marketInfo, tradeQuantity, tradeMode, playerState, currentShip, quote]);

  // Show loading state if player state isn't loaded yet
  if (!playerState) {
    return (
      <Wrapper>
        <div className="trading-interface">
          <div className="trading-header">
            <h2>Trading Interface</h2>
          </div>
          <div className="not-docked-message">
            <div className="message-icon">⏳</div>
            <h3>Loading...</h3>
            <p>Initializing trading systems...</p>
          </div>
        </div>
      </Wrapper>
    );
  }

  // Re-fetch slip availability for one station (e.g. after a 409)
  const refreshStationSlips = async (stationId: string) => {
    const info = await getStationSlips(stationId);
    if (info) {
      setSlipsByStation(prev => ({ ...prev, [stationId]: info }));
    }
  };

  const handleDock = async (stationId: string) => {
    setDockingStationId(stationId);
    setDockFull(null);
    setBumpConfirming(false);
    setBumpError(null);
    try {
      const result = await dockAtStation(stationId);

      if (result?.full) {
        // All slips occupied — we've been auto-enqueued. Show the inline
        // queue/bump panel instead of a generic error.
        const port = portsInSector.find(p => p.id === stationId);
        setDockFull({
          stationId,
          stationName: port?.name || 'this station',
          detail: result.detail,
          slips: result.slips || { capacity: 0, occupied: 0 },
          queue_position: result.queue_position,
          bumpable: result.bumpable || [],
          bump_cost: result.bump_cost || 0
        });
        refreshStationSlips(stationId);
        return;
      }

      // After successful dock, select this port for trading
      setSelectedPort(stationId);
      addNotification({
        title: 'Docked',
        content: 'Successfully docked at station.',
        level: 'success'
      });
    } catch (err: any) {
      addNotification({
        title: 'Docking Failed',
        content: err.response?.data?.detail || err.response?.data?.message || 'Failed to dock at station.',
        level: 'error'
      });
    } finally {
      setDockingStationId(null);
    }
  };

  // Longest-tenured occupant is the canonical bump target
  const getBumpTarget = (): BumpableOccupant | null => {
    if (!dockFull || dockFull.bumpable.length === 0) return null;
    return dockFull.bumpable.reduce((a, b) => (b.tenure_hours > a.tenure_hours ? b : a));
  };

  const formatTenure = (hours: number): string =>
    hours < 1 ? '<1h' : `${Math.round(hours)}h`;

  // 'pay 5× fee: ₡500' when the multiplier is clean, else just the cost
  const formatBumpCost = (): string => {
    if (!dockFull) return '';
    const fee = slipsByStation[dockFull.stationId]?.fee;
    if (fee && dockFull.bump_cost % fee === 0) {
      return `pay ${dockFull.bump_cost / fee}× fee: ${formatCredits(dockFull.bump_cost)}`;
    }
    return `pay ${formatCredits(dockFull.bump_cost)}`;
  };

  const handleBump = async () => {
    const target = getBumpTarget();
    if (!dockFull || !target || bumping) return;

    setBumping(true);
    setBumpError(null);
    try {
      await bumpDockOccupant(dockFull.stationId, target.player_id);

      // Bump docks us — flow into the normal docked state
      const stationId = dockFull.stationId;
      const stationName = dockFull.stationName;
      setDockFull(null);
      setBumpConfirming(false);
      setSelectedPort(stationId);
      addNotification({
        title: 'Docked',
        content: `Slip secured at ${stationName} — ${target.name} was evicted.`,
        level: 'success'
      });
    } catch (err: any) {
      setBumpError(
        err.response?.data?.detail ||
        err.response?.data?.message ||
        'Bump failed — the slip may have already changed hands.'
      );
      if (dockFull) {
        refreshStationSlips(dockFull.stationId);
      }
    } finally {
      setBumping(false);
    }
  };

  const handlePortChange = (stationId: string) => {
    setSelectedPort(stationId);
    setSelectedResource('');
    setTradeQuantity(1);
    setExpandedSparkline(null);
    // A quote from the OLD port must never linger and display (even
    // dimmed/pending) against a NEW port's commodity list.
    setQuote(null);
    setQuoteError(false);
  };

  const handleResourceChange = (resourceType: string) => {
    setSelectedResource(resourceType);
    setTradeQuantity(1);
    // A fresh commodity opens the standard quantity/summary view, never the
    // haggle desk carried over from a prior selection.
    setHaggleMode(false);
    // Directly open the trade modal so the player can immediately buy/sell
    setShowConfirmDialog(true);
    // See handlePortChange — never show a stale commodity's quote here.
    setQuote(null);
    setQuoteError(false);
  };

  const handleTradeModeChange = (mode: 'buy' | 'sell') => {
    setTradeMode(mode);
    // Mirror handlePortChange: a stale selection (and any open trade modal)
    // from the other mode must not carry across the buy/sell switch
    setSelectedResource('');
    setShowConfirmDialog(false);
    setHaggleMode(false);
    setTradeQuantity(1);
    setQuote(null);
    setQuoteError(false);
  };

  // Close the trade modal AND reset haggle state in one place — used by the
  // overlay click, the × button, Cancel, and on a successful trade.
  const closeTradeModal = () => {
    setShowConfirmDialog(false);
    setHaggleMode(false);
  };

  const getMaxQuantity = (): number => {
    if (!selectedResource || !marketInfo || !playerState || !currentShip) return 0;
    
    const resource = marketInfo.resources[selectedResource];
    if (!resource) return 0;

    if (tradeMode === 'buy') {
      // For buying: limited by credits, cargo space, and port inventory
      // (the station charges sell_price when the player buys, PLUS the
      // station tariff — divide by the tariff-inclusive unit cost so the
      // Max button never picks a quantity the server would reject)
      const taxRate = marketInfo.port.tax_rate ?? 0;
      const affordableQuantity = Math.floor(playerState.credits / (resource.sell_price * (1 + taxRate)));
      const currentCargo = getCargoUsed();
      const cargoSpace = getCargoCapacity() - currentCargo;
      const portInventory = resource.quantity;

      return Math.min(affordableQuantity, cargoSpace, portInventory);
    } else {
      // For selling: limited by what player has
      return getPlayerResourceAmount(selectedResource);
    }
  };

  const setMaxQuantity = () => {
    const maxQty = getMaxQuantity();
    setTradeQuantity(maxQty);
  };

  const canExecuteTrade = (qty: number = tradeQuantity): boolean => {
    if (!tradeCalculation || !playerState?.is_docked) return false;

    const resource = marketInfo?.resources[selectedResource];
    if (!resource) return false;

    if (tradeMode === 'buy') {
      return resource.station_sells && tradeCalculation.isAffordable && tradeCalculation.fitsInCargo && qty <= resource.quantity;
    } else {
      const playerHas = getPlayerResourceAmount(selectedResource);
      return resource.station_buys && qty <= playerHas;
    }
  };

  // `qtyOverride` is supplied by the haggle accept path so the trade fires at
  // the exact quantity the session was opened against, independent of React's
  // async state flush (the slider is hidden during haggling, so they match —
  // this is belt-and-braces against a stale closure). It ALSO doubles as the
  // "came from haggle accept" signal below: that path's price integrity is
  // already guaranteed server-side (the single-use consume_agreed_price), so
  // the pre-commit quote re-check (which re-fetches the POSTED price, not
  // the haggled one) does not apply to it.
  const executeTrade = async (qtyOverride?: number) => {
    const qty = qtyOverride ?? tradeQuantity;
    const viaHaggleAccept = qtyOverride !== undefined;
    if (executingRef.current || !canExecuteTrade(qty) || !selectedPort || !selectedResource) return;
    executingRef.current = true;
    setIsExecuting(true);

    try {
      if (!viaHaggleAccept) {
        // mack HIGH: MarketPrice is a single row shared by every player at
        // this station — ANY player's trade reprices it, and the price
        // stack itself (rank/rep/tariff/lever) can move too. quoteIsCurrent
        // only tracks resource/mode/quantity, not market state, so the
        // total on screen can be stale by the time Confirm is actually
        // clicked (a slow network, or just the player reading the dialog).
        // Re-fetch a fresh quote right here, immediately before charging;
        // if it differs from what's displayed, update the display and STOP
        // short of charging — the player must see the new number and click
        // Confirm again rather than being silently charged something other
        // than what they were looking at. Residual (mack, downgraded to
        // MEDIUM): the market can still move in the sub-RTT gap between
        // THIS re-fetch resolving and buy_resource/sell_resource's own
        // locked recompute a moment later — bounded to one network
        // round-trip, not zero. Fully closing that needs a server-side
        // price/version token (hub follow-on), not done here.
        const freshResponse = await apiClient.post('/api/v1/trading/quote', {
          station_id: selectedPort,
          resource_type: selectedResource,
          quantity: qty,
          action: tradeMode,
        });
        const fresh = freshResponse.data;
        if (!quote || fresh.total !== quote.total) {
          setQuote({
            resourceType: fresh.resource_type,
            quantity: fresh.quantity,
            action: fresh.action,
            unitPrice: fresh.unit_price,
            subtotal: fresh.subtotal,
            taxRate: fresh.tax_rate,
            tax: fresh.tax,
            total: fresh.total,
          });
          addNotification({
            title: 'Price Changed',
            content: `The market moved — the new total is ${formatCredits(fresh.total)}. Review and confirm again.`,
            level: 'warning'
          });
          return; // requires an explicit second Confirm click at the new price
        }
      }

      let result;
      if (tradeMode === 'buy') {
        result = await buyResource(selectedPort, selectedResource, qty);
      } else {
        result = await sellResource(selectedPort, selectedResource, qty);
      }

      // Show success notification
      const defaultMsg = tradeMode === 'buy'
        ? `Bought ${qty} ${selectedResource}`
        : `Sold ${qty} ${selectedResource}`;
      addNotification({
        title: 'Trade Successful',
        content: result?.message || defaultMsg,
        level: 'success'
      });

      // WO-G5 (EC3): the backend sets `price_alert` on a trade whose post-trade
      // reprice crosses this commodity's alert threshold at the station. Surface
      // it as a distinct warning toast so the player knows the market just moved
      // (no client read this flag before).
      if (result?.price_alert === true) {
        addNotification({
          title: '📈 Market Price Alert',
          content: `Your ${formatName(selectedResource)} trade moved prices sharply at this station — the market has shifted. Check the board before your next run.`,
          level: 'warning'
        });
      }

      // Reset form (also drops out of haggle mode if we got here via accept)
      setTradeQuantity(1);
      closeTradeModal();

    } catch (error: any) {
      const serverMessage: string = error.response?.data?.detail || error.response?.data?.message || '';
      let content = serverMessage || 'Failed to execute trade';
      // Map directionality errors to friendlier hints pointing at the other tab
      if (serverMessage.includes('does not sell')) {
        content = `This station doesn't sell ${formatName(selectedResource)} — but it may buy it. Try the Sell Resources tab.`;
      } else if (serverMessage.includes('does not buy')) {
        content = `This station doesn't buy ${formatName(selectedResource)} — but it may sell it. Try the Buy Resources tab.`;
      }
      addNotification({
        title: 'Trade Failed',
        content,
        level: 'error'
      });
    } finally {
      executingRef.current = false;
      setIsExecuting(false);
    }
  };

  // WO-API-B1: the modal renders ONLY server-quoted numbers — no local
  // unit-price/tax/total formula. `quote` may be one generation stale
  // during the debounce window (avoids flashing the card to zero on every
  // keystroke); `quoteIsCurrent` tells the render whether it matches the
  // params on screen right now, and gates the pending visual — Confirm
  // itself is independently gated via tradeCalculation (null until the
  // quote is current, see the effect above).
  const quoteIsCurrent =
    !!quote &&
    quote.resourceType === selectedResource &&
    quote.action === tradeMode &&
    quote.quantity === tradeQuantity;
  const quotePending = quoteLoading || !quoteIsCurrent;

  if (!playerState?.is_docked) {
    return (
      <Wrapper>
      <div className="trading-interface">
        <div className="trading-header">
          <h2>Trading Interface</h2>
          <div className="status-indicator disconnected">Not Docked</div>
        </div>
        <RoutePlannerPanel />
        <div className="not-docked-message">
          <div className="message-icon">🚀</div>
          <h3>Dock at a Station to Trade</h3>
          <p>You must be docked at a port to access trading facilities.</p>
          {portsInSector.length > 0 && (
            <div className="available-ports">
              <h4>Available Ports in Sector:</h4>
              <ul>
                {portsInSector.map(port => {
                  const slips = slipsByStation[port.id];
                  const slipsFull = !!slips && slips.free === 0;
                  return (
                    <li key={port.id} className={`port-item ${slipsFull ? 'slips-full' : ''}`.trim()}>
                      <div className="port-info">
                        <span className="port-name">{port.name}</span>
                        <span className="port-type">{port.type}</span>
                      </div>
                      <button
                        className="dock-button"
                        onClick={() => handleDock(port.id)}
                        disabled={dockingStationId !== null}
                        title={slipsFull
                          ? `All ${slips.capacity} transient slips occupied — docking joins the queue (${slips.queue_length} waiting) or lets you bump a long-tenured occupant.`
                          : undefined}
                      >
                        {dockingStationId === port.id
                          ? 'Docking...'
                          : slipsFull ? 'Dock (Queue)' : 'Dock'}
                      </button>
                      {slips && (
                        <div className={`port-slips-line ${slipsFull ? 'full' : ''}`.trim()}>
                          Transient slips: {slips.occupied}/{slips.capacity} occupied · fee {formatCredits(slips.fee)}
                          {slipsFull && typeof (slips as any).estimated_wait_minutes === 'number' && (
                            <> · Estimated wait: ~{(slips as any).estimated_wait_minutes} min</>
                          )}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>

              {dockFull && (() => {
                const bumpTarget = getBumpTarget();
                return (
                  <div className="dock-full-panel" role="alert">
                    <div className="dock-full-header">
                      <span className="dock-full-title">
                        All {dockFull.slips.capacity} slips occupied
                      </span>
                      {dockFull.queue_position != null && (
                        <span className="queue-badge">
                          Queue position {dockFull.queue_position}
                        </span>
                      )}
                    </div>
                    <p className="dock-full-detail">
                      {dockFull.detail || `${dockFull.stationName} has no free transient slips. You've been added to the docking queue.`}
                    </p>
                    {bumpTarget && (
                      <div className="bump-option">
                        {!bumpConfirming ? (
                          <button
                            className="bump-button"
                            onClick={() => { setBumpConfirming(true); setBumpError(null); }}
                            disabled={bumping}
                          >
                            Evict {bumpTarget.name} — docked {formatTenure(bumpTarget.tenure_hours)} — {formatBumpCost()}
                          </button>
                        ) : (
                          <div className="bump-confirm-row">
                            <span className="bump-confirm-prompt">
                              Evict {bumpTarget.name} and {formatBumpCost()}?
                            </span>
                            <button
                              className="bump-confirm-button"
                              onClick={handleBump}
                              disabled={bumping}
                            >
                              {bumping ? 'Evicting...' : 'Confirm Eviction'}
                            </button>
                            <button
                              className="bump-cancel-button"
                              onClick={() => setBumpConfirming(false)}
                              disabled={bumping}
                            >
                              Cancel
                            </button>
                          </div>
                        )}
                        {bumpError && <div className="bump-error">{bumpError}</div>}
                      </div>
                    )}
                    <button
                      className="dock-full-dismiss"
                      onClick={() => { setDockFull(null); setBumpConfirming(false); setBumpError(null); }}
                    >
                      Dismiss
                    </button>
                  </div>
                );
              })()}
            </div>
          )}
        </div>
      </div>
      </Wrapper>
    );
  }

  // Optional flavor data from the backend; both render-guarded below
  const traderPersonality = getTraderPersonality(marketInfo?.port?.trader_personality_type);

  return (
    <Wrapper>
    <div className="trading-interface">
      <div className="trading-header">
        <h2>Trading Interface</h2>
        <div className="connection-status">
          <div className={`status-indicator ${isConnected ? 'connected' : 'disconnected'}`}>
            {isConnected ? 'Real-time' : 'Offline'}
          </div>
        </div>
      </div>

      {error && (
        <div className="error-message">
          <span className="error-icon">⚠️</span>
          {error}
        </div>
      )}

      <RoutePlannerPanel />

      <div className="trading-content">
        {/* Station Selection — only when there's an actual choice (multiple
            ports in the sector). The lone-port case is just chrome that pushes
            the buy/sell grid below the fold. */}
        {portsInSector.length > 1 && (
          <div className="port-selection">
            <label htmlFor="port-select">Select Station:</label>
            <select
              id="port-select"
              value={selectedPort}
              onChange={(e) => handlePortChange(e.target.value)}
            >
              <option value="">Choose a port...</option>
              {portsInSector.map(port => (
                <option key={port.id} value={port.id}>
                  {port.name} ({port.type})
                </option>
              ))}
            </select>
          </div>
        )}

        {/* Trade Mode Selection */}
        <div className="trade-mode-selection">
          <button 
            className={`mode-button ${tradeMode === 'buy' ? 'active' : ''}`}
            onClick={() => handleTradeModeChange('buy')}
          >
            Buy Resources
          </button>
          <button 
            className={`mode-button ${tradeMode === 'sell' ? 'active' : ''}`}
            onClick={() => handleTradeModeChange('sell')}
          >
            Sell Resources
          </button>
        </div>

        {/* Market Information */}
        {marketInfo && (
          <div className="market-info">
            <div className="market-info-header">
              <h3>Market at {marketInfo.port.name}</h3>
              <StationClassBadge station_class={marketInfo.port.station_class} />
              {traderPersonality && (
                <span
                  className="trader-personality-chip"
                  title={`Trader temperament: ${traderPersonality.label}`}
                >
                  {traderPersonality.label}
                </span>
              )}
            </div>
            <div className="port-details">
              <span>Type: {marketInfo.port.type}</span>
              {marketInfo.port.faction && <span>Faction: {marketInfo.port.faction}</span>}
              {/* Render the server's effective tax rate verbatim — 0 means
                  genuinely untaxed (unowned port); `|| 0.1` used to fabricate
                  a 10% rate out of an honest zero. */}
              <span>Tax Rate: {((marketInfo.port.tax_rate ?? 0) * 100).toFixed(1)}%</span>
            </div>

            <div className="player-status-bar">
              <span className="status-credits">{formatCredits(playerState?.credits || 0)}</span>
              <span className="status-cargo">📦 Cargo: {getCargoUsed()} / {getCargoCapacity()}</span>
            </div>

            {tradeMode === 'sell' && getPlayerCargoCount() === 0 ? (
              <div className="empty-cargo-state">
                <div className="empty-icon">📦</div>
                <h4>Cargo Hold Empty</h4>
                <p>Buy goods first to sell them at this station.</p>
                <button
                  className="switch-mode-button"
                  onClick={() => handleTradeModeChange('buy')}
                >
                  Switch to Buy Mode
                </button>
              </div>
            ) : (() => {
              const entries = Object.entries(marketInfo.resources);
              const stationSellsAny = entries.some(([, r]) => r.station_sells);
              const stationBuysAny = entries.some(([, r]) => r.station_buys);
              // The player holds at least one commodity this station buys
              const sellActionable = entries.some(
                ([type, r]) => r.station_buys && getPlayerResourceAmount(type) > 0
              );
              // "Actionable" in the active mode: Buy needs the station to
              // sell it; Sell needs the station to buy it AND the player to
              // be holding some.
              const actionable = entries.filter(([type, r]) =>
                tradeMode === 'buy'
                  ? r.station_sells
                  : r.station_buys && getPlayerResourceAmount(type) > 0
              );

              if (actionable.length === 0) {
                // Nothing the player can act on in this mode — explain why
                // (from the station_buys/station_sells flags) instead of
                // rendering a grid of dead cards.
                let title: string;
                let body: string;
                if (tradeMode === 'buy') {
                  title = 'THIS STATION SELLS NO COMMODITIES';
                  body = stationBuysAny
                    ? `${marketInfo.port.name} only purchases goods from traders.`
                    : `${marketInfo.port.name} lists no commodities for trade.`;
                } else if (!stationBuysAny) {
                  title = 'THIS STATION BUYS NO COMMODITIES';
                  body = stationSellsAny
                    ? `${marketInfo.port.name} only sells goods to traders.`
                    : `${marketInfo.port.name} lists no commodities for trade.`;
                } else {
                  const buyableNames = entries
                    .filter(([, r]) => r.station_buys)
                    .map(([type]) => formatName(type));
                  title = 'NO BUYERS FOR YOUR CARGO';
                  body = `${marketInfo.port.name} purchases ${buyableNames.join(', ')} — none currently in your hold.`;
                }
                // Only offer the mode switch when the other mode is actionable
                const switchTarget: 'buy' | 'sell' | null =
                  tradeMode === 'buy'
                    ? (sellActionable ? 'sell' : null)
                    : (stationSellsAny ? 'buy' : null);

                return (
                  <div className="empty-market-panel" role="status">
                    <div className="empty-market-badge">Market Notice</div>
                    <h4>{title}</h4>
                    <p>{body}</p>
                    {switchTarget && (
                      <button
                        className="switch-mode-button"
                        onClick={() => handleTradeModeChange(switchTarget)}
                      >
                        Switch to {switchTarget === 'buy' ? 'Buy' : 'Sell'} Mode
                      </button>
                    )}
                  </div>
                );
              }

              return (
            <div className={`resources-grid${entries.length === 1 ? ' single-commodity' : ''}`}>
              {entries.map(([resourceType, resource]) => {
                const playerAmount = getPlayerResourceAmount(resourceType);
                const supportsDirection = tradeMode === 'buy' ? resource.station_sells : resource.station_buys;
                const canTrade = tradeMode === 'buy'
                  ? resource.station_sells && resource.quantity > 0
                  : resource.station_buys && playerAmount > 0;
                const trend = trendGlyph(resource.price_trend);
                const isSparklineOpen = expandedSparkline === resourceType;
                const sparklineData = historyByKey[`${selectedPort}:${resourceType}`];
                // Live overlay from marketStream, falling back to the REST
                // snapshot for any field a given publisher didn't send.
                const liveOverride = liveOverrides[resourceType];
                const displaySellPrice = liveOverride?.sell_price ?? resource.sell_price;
                const displayBuyPrice = liveOverride?.buy_price ?? resource.buy_price;
                const flashDirection = priceFlash[resourceType];

                return (
                  <div
                    key={resourceType}
                    className={`resource-card ${!canTrade ? 'disabled' : ''} ${!supportsDirection ? 'direction-unsupported' : ''} ${selectedResource === resourceType ? 'selected' : ''}`}
                    onClick={() => canTrade && handleResourceChange(resourceType)}
                    role="button"
                    tabIndex={canTrade ? 0 : -1}
                    onKeyDown={(e) => {
                      if (canTrade && (e.key === 'Enter' || e.key === ' ')) {
                        e.preventDefault();
                        handleResourceChange(resourceType);
                      }
                    }}
                  >
                    <div className="resource-icon">{resourceIcon(resourceType)}</div>
                    <div className="resource-name">{formatName(resourceType)}</div>
                    <div className="resource-direction-badges">
                      {resource.station_sells && <span className="direction-badge sells">Station Sells</span>}
                      {resource.station_buys && <span className="direction-badge buys">Station Buys</span>}
                    </div>
                    <div className={`resource-prices ${flashDirection ? `price-flash-${flashDirection}` : ''}`.trim()}>
                      {/* Player buys at the station's sell_price, sells at its buy_price */}
                      {resource.station_sells && <div className="buy-price">You pay: {formatCredits(displaySellPrice)}</div>}
                      {resource.station_buys && <div className="sell-price">You get: {formatCredits(displayBuyPrice)}</div>}
                    </div>
                    <div className="resource-trend-row">
                      <span
                        className={`trend-glyph ${trend.cls}`}
                        aria-label={trend.label}
                        title={trend.label}
                      >
                        {trend.glyph}
                      </span>
                      <button
                        type="button"
                        className="sparkline-toggle"
                        onClick={(e) => toggleSparkline(resourceType, e)}
                        onKeyDown={(e) => e.stopPropagation()}
                        aria-expanded={isSparklineOpen}
                        aria-label={`${isSparklineOpen ? 'Hide' : 'Show'} price history for ${formatName(resourceType)}`}
                        title="Price history"
                      >
                        📊
                      </button>
                    </div>
                    {isSparklineOpen && (
                      <div className="price-sparkline-panel" onClick={(e) => e.stopPropagation()}>
                        {/* undefined covers the one-render gap before the
                            fetch effect commits its first 'loading' state
                            (and the no-selected-port edge case) — never
                            render a blank panel. */}
                        {(sparklineData === 'loading' || sparklineData === undefined) && (
                          <span className="sparkline-status">Loading history…</span>
                        )}
                        {sparklineData === 'error' && (
                          <span className="sparkline-status error">Couldn't load price history.</span>
                        )}
                        {Array.isArray(sparklineData) && sparklineData.length === 0 && (
                          <span className="sparkline-status">No history yet — check back after the next market snapshot.</span>
                        )}
                        {Array.isArray(sparklineData) && sparklineData.length > 0 && (
                          <PriceSparkline points={sparklineData} />
                        )}
                      </div>
                    )}
                    <div className="resource-quantity">
                      {tradeMode === 'buy'
                        ? `Available: ${resource.quantity}`
                        : `You have: ${playerAmount}`
                      }
                    </div>
                    {resource.player_demand_score !== undefined && (
                      <div className="resource-demand">
                        Demand: {
                          resource.player_demand_score >= 1.25 ? 'High'
                          : resource.player_demand_score <= 0.75 ? 'Low'
                          : 'Steady'
                        }
                      </div>
                    )}
                    {canTrade && (
                      <div className="resource-trade-hint">
                        Click to {tradeMode}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
              );
            })()}
          </div>
        )}

        {/* Trade Action Button - Opens Modal */}
        {selectedResource && marketInfo && (
          <div className="trade-action-bar">
            <div className="selected-resource-info">
              <span className="resource-icon-large">{resourceIcon(selectedResource)}</span>
              <div className="resource-details">
                <span className="resource-name-large">{formatName(selectedResource)}</span>
                <span className="resource-price">
                  {tradeMode === 'buy'
                    ? `Buy @ ${formatCredits(marketInfo.resources[selectedResource]?.sell_price || 0)}/unit`
                    : `Sell @ ${formatCredits(marketInfo.resources[selectedResource]?.buy_price || 0)}/unit`
                  }
                </span>
              </div>
            </div>
            <button
              className="open-trade-modal-button"
              onClick={() => setShowConfirmDialog(true)}
            >
              {tradeMode === 'buy' ? 'Buy' : 'Sell'} {formatName(selectedResource)}
            </button>
          </div>
        )}
      </div>

      {/* Trade Modal - Rendered via Portal to escape stacking context */}
      {showConfirmDialog && selectedResource && marketInfo && createPortal(
        <div className="modal-overlay" onClick={closeTradeModal}>
          <div className="trade-modal" onClick={(e) => e.stopPropagation()}>
            <div className="trade-modal-header">
              <div className="modal-resource-info">
                <span className="modal-resource-icon">{resourceIcon(selectedResource)}</span>
                <h3>
                  <span className={`modal-trade-mode ${tradeMode}`}>{tradeMode === 'buy' ? 'BUY' : 'SELL'}</span>
                  {formatName(selectedResource)}
                </h3>
              </div>
              <button className="modal-close" onClick={closeTradeModal}>×</button>
            </div>

            {haggleMode ? (
              <div className="trade-modal-body">
                <HaggleDesk
                  stationId={selectedPort}
                  commodity={selectedResource}
                  side={tradeMode}
                  quantity={haggleQuantity}
                  taxRate={marketInfo?.port.tax_rate ?? 0}
                  commodityLabel={formatName(selectedResource)}
                  personalityLabel={traderPersonality?.label ?? null}
                  onBack={() => setHaggleMode(false)}
                  onAccepted={() => {
                    // The agreed price is already stored server-side keyed by
                    // (station, commodity, side); firing the normal buy/sell at
                    // the haggled quantity consumes it transparently. Pass the
                    // frozen quantity explicitly so the trade matches the
                    // negotiated count regardless of state-flush timing.
                    executeTrade(haggleQuantity);
                  }}
                />
              </div>
            ) : (
            <div className="trade-modal-body">
              {/* Quantity Slider */}
              <div className="quantity-section">
                <label>Quantity</label>
                {/* mack optional LOW: quantity is locked during isExecuting
                    so a nudge mid-flight can't visually diverge from the
                    quantity actually being charged (traced harmless to the
                    charge itself — buyResource/sellResource capture qty in
                    a local before any await — but a moving number while a
                    trade is in flight is confusing regardless). */}
                <div className="quantity-slider-container">
                  <input
                    type="range"
                    min="1"
                    max={Math.max(1, getMaxQuantity())}
                    value={tradeQuantity}
                    onChange={(e) => setTradeQuantity(parseInt(e.target.value))}
                    className="quantity-slider"
                    disabled={isExecuting}
                  />
                  <div className="quantity-input-group">
                    <button
                      className="qty-btn"
                      onClick={() => setTradeQuantity(Math.max(1, tradeQuantity - 1))}
                      disabled={isExecuting || tradeQuantity <= 1}
                    >
                      −
                    </button>
                    <input
                      type="number"
                      min="1"
                      max={getMaxQuantity()}
                      value={tradeQuantity}
                      onChange={(e) => setTradeQuantity(Math.max(1, Math.min(getMaxQuantity(), parseInt(e.target.value) || 1)))}
                      className="quantity-input"
                      disabled={isExecuting}
                    />
                    <button
                      className="qty-btn"
                      onClick={() => setTradeQuantity(Math.min(getMaxQuantity(), tradeQuantity + 1))}
                      disabled={isExecuting || tradeQuantity >= getMaxQuantity()}
                    >
                      +
                    </button>
                  </div>
                </div>
                <div className="quantity-presets">
                  <button onClick={() => setTradeQuantity(1)} disabled={isExecuting}>1</button>
                  <button onClick={() => setTradeQuantity(Math.floor(getMaxQuantity() * 0.25) || 1)} disabled={isExecuting}>
                    25% ({Math.floor(getMaxQuantity() * 0.25) || 1})
                  </button>
                  <button onClick={() => setTradeQuantity(Math.floor(getMaxQuantity() * 0.5) || 1)} disabled={isExecuting}>
                    50% ({Math.floor(getMaxQuantity() * 0.5) || 1})
                  </button>
                  <button onClick={() => setTradeQuantity(Math.floor(getMaxQuantity() * 0.75) || 1)} disabled={isExecuting}>
                    75% ({Math.floor(getMaxQuantity() * 0.75) || 1})
                  </button>
                  <button onClick={() => setTradeQuantity(getMaxQuantity() || 1)} disabled={isExecuting}>
                    Max ({getMaxQuantity()})
                  </button>
                </div>
              </div>

              {/* mack LOW: a quote fetch failure is surfaced here rather than
                  silently leaving Confirm dead with no explanation — Retry
                  bumps quoteRetryNonce, forcing the fetch effect to re-run
                  with identical params. */}
              {quoteError && (
                <div className="quote-error-banner">
                  <span>Couldn't fetch the current price.</span>
                  <button
                    type="button"
                    className="quote-retry-btn"
                    onClick={() => setQuoteRetryNonce(n => n + 1)}
                  >
                    Retry
                  </button>
                </div>
              )}

              {/* Trade Summary — every number here comes from the server
                  quote (WO-API-B1); `quotePending` (dimmed via CSS) signals
                  a debounce/fetch in flight rather than blanking the card. */}
              <div className={`trade-summary-card${quotePending ? ' pending' : ''}`}>
                <div className="summary-row">
                  <span>Unit Price</span>
                  <span className="value">{formatCredits(quote?.unitPrice ?? 0)}</span>
                </div>
                <div className="summary-row">
                  <span>Quantity</span>
                  <span className="value">× {tradeQuantity}</span>
                </div>
                <div className="summary-row">
                  <span>{tradeMode === 'buy' ? 'Goods Cost' : 'Gross Earnings'}</span>
                  <span className="value">{formatCredits(quote?.subtotal ?? 0)}</span>
                </div>
                {/* Server-authoritative station tariff — rendered even at 0.0%
                    so the charged total always matches this preview
                    (routes/trading.py adds it on buys, withholds it on sells) */}
                <div className="summary-row tariff-row">
                  <span>Station tariff ({((quote?.taxRate ?? 0) * 100).toFixed(1)}%)</span>
                  <span className="value">
                    {tradeMode === 'buy' ? '+' : '−'}{formatCredits(quote?.tax ?? 0)}
                  </span>
                </div>
                <div className="summary-divider"></div>
                <div className="summary-row total">
                  <span>{tradeMode === 'buy' ? 'Total Cost' : 'Total Earnings'}</span>
                  <span className="value highlight">
                    {formatCredits(quote?.total ?? 0)}
                  </span>
                </div>

                {tradeMode === 'buy' && (
                  <>
                    <div className="summary-divider"></div>
                    <div className="summary-row">
                      <span>Your Credits</span>
                      <span className="value">{formatCredits(playerState?.credits || 0)}</span>
                    </div>
                    <div className="summary-row">
                      <span>After Purchase</span>
                      <span className={`value ${((playerState?.credits || 0) - (quote?.total ?? 0)) < 0 ? 'error' : 'success'}`}>
                        {formatCredits((playerState?.credits || 0) - (quote?.total ?? 0))}
                      </span>
                    </div>
                    <div className="summary-row">
                      <span>Cargo Space</span>
                      <span className={`value ${(getCargoUsed() + tradeQuantity) > getCargoCapacity() ? 'error' : ''}`}>
                        {getCargoUsed() + tradeQuantity} / {getCargoCapacity()}
                      </span>
                    </div>
                  </>
                )}

                {tradeMode === 'sell' && (
                  <>
                    <div className="summary-divider"></div>
                    <div className="summary-row">
                      <span>Your Credits</span>
                      <span className="value">{formatCredits(playerState?.credits || 0)}</span>
                    </div>
                    <div className="summary-row">
                      <span>After Sale</span>
                      <span className="value success">
                        {formatCredits((playerState?.credits || 0) + (quote?.total ?? 0))}
                      </span>
                    </div>
                  </>
                )}
              </div>
            </div>
            )}

            {/* The standard footer (Cancel / Haggle / Confirm) is hidden while
                the haggle desk is mounted — the desk carries its own actions. */}
            {!haggleMode && (
              <div className="trade-modal-footer">
                <button className="cancel-btn" onClick={closeTradeModal}>
                  Cancel
                </button>
                <button
                  className="haggle-launch-btn"
                  onClick={() => {
                    // Freeze the quantity the negotiation is opened against.
                    setHaggleQuantity(Math.max(1, tradeQuantity));
                    setHaggleMode(true);
                  }}
                  disabled={!canExecuteTrade() || isExecuting}
                  title="Negotiate a per-unit price over up to 4 rounds"
                >
                  Haggle
                </button>
                <button
                  className="confirm-trade-btn"
                  onClick={() => executeTrade()}
                  disabled={!canExecuteTrade() || isExecuting}
                >
                  {isExecuting ? 'Processing...' : `Confirm ${tradeMode === 'buy' ? 'Purchase' : 'Sale'}`}
                </button>
              </div>
            )}
          </div>
        </div>,
        document.body
      )}
    </div>
    </Wrapper>
  );
};

export default TradingInterface;