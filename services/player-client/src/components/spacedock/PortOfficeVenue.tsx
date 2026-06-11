import React, { useState, useCallback, useEffect, useMemo } from 'react';
import { useGame } from '../../contexts/GameContext';
import './port-office-venue.css';

// =====================================================================
// Port Office — station ownership registry venue
// (FEATURES/economy/port-ownership)
//
// Backend contract: /api/v1/port-ownership/* via the GameContext helpers.
// All payloads are normalized defensively (same feature-detect posture as
// ConstructionVenue) — the venue renders ONLY what the API returns, with
// explicit loading / empty / error states. No mock data, ever.
// =====================================================================

// --- Payload normalization helpers ---

const asRecord = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;

const pickNumber = (...candidates: unknown[]): number | null => {
  for (const c of candidates) {
    if (typeof c === 'number' && Number.isFinite(c)) return c;
  }
  return null;
};

const pickString = (...candidates: unknown[]): string | null => {
  for (const c of candidates) {
    if (typeof c === 'string' && c) return c;
  }
  return null;
};

const pickBool = (...candidates: unknown[]): boolean | null => {
  for (const c of candidates) {
    if (typeof c === 'boolean') return c;
  }
  return null;
};

// Pull a readable message out of an axios error. FastAPI 422 validation
// errors arrive as detail: [{loc, msg, type}, ...] — flatten the msg fields.
const axiosErrorMessage = (error: unknown, fallback: string): string => {
  const e = asRecord(error);
  const response = asRecord(e?.response);
  const data = asRecord(response?.data);
  const raw = data?.message ?? data?.detail;
  if (typeof raw === 'string' && raw) return raw;
  if (Array.isArray(raw)) {
    const msgs = raw
      .map(item => {
        const rec = asRecord(item);
        return typeof rec?.msg === 'string' && rec.msg ? rec.msg : null;
      })
      .filter((m): m is string => m !== null);
    if (msgs.length > 0) return msgs.join('; ');
  }
  // Non-HTTP failures (e.g. 'Not authenticated' thrown by the context helpers)
  if (!response && typeof e?.message === 'string' && e.message) return e.message;
  return fallback;
};

// Countdown formatting against a ticking clock (house pattern from
// ConstructionVenue — wall-clock ISO deadlines arrive pre-scaled)
const fmtCountdown = (iso: string, nowMs: number): { text: string; expired: boolean; urgent: boolean } => {
  const target = Date.parse(iso);
  if (Number.isNaN(target)) return { text: '—', expired: false, urgent: false };
  let diff = Math.floor((target - nowMs) / 1000);
  if (diff <= 0) return { text: 'EXPIRED', expired: true, urgent: true };
  const urgent = diff < 3600;
  const days = Math.floor(diff / 86400);
  diff %= 86400;
  const hours = Math.floor(diff / 3600);
  diff %= 3600;
  const minutes = Math.floor(diff / 60);
  const seconds = diff % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  const text = days > 0
    ? `${days}d ${pad(hours)}h ${pad(minutes)}m ${pad(seconds)}s`
    : `${pad(hours)}h ${pad(minutes)}m ${pad(seconds)}s`;
  return { text, expired: false, urgent };
};

// Feature-detect new credit totals in action responses
const creditsFromResponse = (result: unknown): number | null => {
  const body = asRecord(result);
  if (!body) return null;
  return pickNumber(body.credits_remaining, body.new_credits, body.remaining_credits, body.credits);
};

// --- Normalized views ---

// Statutory station price clamp from the ownership spec — bids above this
// are rejected server-side, so reject them client-side too.
const BID_CEILING = 2_000_000;

interface OfferView {
  bidAmount: number | null;
  status: string | null;
}

interface ListingView {
  ownerId: string | null;
  ownerName: string | null;
  status: string | null;          // lowercase: unclaimed | owned | listed | ...
  isListed: boolean;
  listPrice: number | null;
  graceExpiresAt: string | null;
  offersCount: number | null;
  myOffer: OfferView | null;
  purchasable: boolean | null;
  blockedReason: string | null;
  taxRate: number | null;
  treasuryBalance: number | null;
}

interface MyStationView {
  taxRate: number | null;
  treasury: number | null;
  treasuryCapacity: number | null;
  acquisitionCost: number | null;
  revenue90d: number | null;
  revenue30d: number | null;
  monthly: Array<{ label: string; amount: number }>;
}

interface MonthView {
  label: string;
  sharePct: number | null;        // 0–100
  hostile: boolean | null;
  qualifies: boolean | null;
  challengerVolume: number | null;
  totalVolume: number | null;
}

interface TakeoverView {
  status: string | null;          // building | eligible | countered | disputed | failed | transferred
  challengerId: string | null;
  isOwner: boolean | null;
  isChallenger: boolean | null;
  months: MonthView[];
  monthsSatisfied: number | null;
  counterExpiresAt: string | null;
  forcedSalePrice: number | null;
}

const normalizeOffer = (raw: unknown): OfferView | null => {
  const o = asRecord(raw);
  if (!o) return null;
  return {
    bidAmount: pickNumber(o.bid),
    status: pickString(o.status)?.toLowerCase() ?? null
  };
};

// GET /stations/{id}/listing — exact backend field names, no probing
const normalizeListing = (raw: unknown): ListingView | null => {
  const o = asRecord(raw);
  if (!o) return null;
  return {
    ownerId: pickString(o.owner_id),
    ownerName: pickString(o.owner_name),
    status: pickString(o.status)?.toLowerCase() ?? null,
    isListed: pickBool(o.is_listed) ?? false,
    listPrice: pickNumber(o.list_price),
    graceExpiresAt: pickString(o.grace_expires_at),
    offersCount: pickNumber(o.offers_count),
    myOffer: normalizeOffer(o.my_offer),
    purchasable: pickBool(o.purchasable),
    blockedReason: pickString(o.blocked_reason),
    taxRate: pickNumber(o.tax_rate),
    treasuryBalance: pickNumber(o.treasury_balance)
  };
};

const normalizeMyStation = (raw: unknown): MyStationView => {
  const o = asRecord(raw) ?? {};
  const revenue = asRecord(o.revenue) ?? {};
  const monthlyRaw = Array.isArray(revenue.monthly)
    ? revenue.monthly
    : Array.isArray(o.monthly_revenue) ? o.monthly_revenue : [];
  const monthly = monthlyRaw
    .map((entry, idx): { label: string; amount: number } | null => {
      if (typeof entry === 'number') return { label: `Month ${idx + 1}`, amount: entry };
      const e = asRecord(entry);
      if (!e) return null;
      const amount = pickNumber(e.amount, e.revenue, e.total);
      if (amount === null) return null;
      return { label: pickString(e.month, e.label) ?? `Month ${idx + 1}`, amount };
    })
    .filter((e): e is { label: string; amount: number } => e !== null);
  return {
    taxRate: pickNumber(o.tax_rate),
    treasury: pickNumber(o.treasury_balance, o.treasury),
    treasuryCapacity: pickNumber(o.treasury_capacity),
    acquisitionCost: pickNumber(o.acquisition_cost, o.purchase_price),
    revenue90d: pickNumber(revenue.last_90_days, o.revenue_90d),
    revenue30d: pickNumber(revenue.last_30_days, o.revenue_30d),
    monthly
  };
};

// Find this station inside the my-stations payload (bare array or {stations})
const findMyStation = (raw: unknown, stationId: string): MyStationView | null => {
  const list: unknown[] = Array.isArray(raw)
    ? raw
    : (() => {
        const o = asRecord(raw);
        return Array.isArray(o?.stations) ? o!.stations as unknown[] : [];
      })();
  for (const entry of list) {
    const e = asRecord(entry);
    if (!e) continue;
    const id = pickString(e.station_id, e.id);
    if (id === stationId) return normalizeMyStation(entry);
  }
  return null;
};

// Shares may arrive as a fraction (0–1) or a percentage (0–100); a value
// above 1 is unambiguously a percentage, at or below 1 a fraction.
const toSharePct = (value: number | null): number | null => {
  if (value === null) return null;
  const pct = value > 1 ? value : value * 100;
  return Math.max(0, Math.min(100, pct));
};

// GET /stations/{id}/takeover — exact backend field names, no probing
const normalizeTakeover = (raw: unknown): TakeoverView | null => {
  const o = asRecord(raw);
  if (!o) return null;
  const monthsRaw = Array.isArray(o.months) ? o.months : [];
  const months = monthsRaw
    .map((entry, idx): MonthView | null => {
      const e = asRecord(entry);
      if (!e) return null;
      return {
        label: pickString(e.month) ?? `M${idx + 1}`,
        sharePct: toSharePct(pickNumber(e.share)),
        hostile: pickBool(e.hostile),
        qualifies: pickBool(e.qualifies),
        challengerVolume: pickNumber(e.challenger_volume),
        totalVolume: pickNumber(e.total_volume)
      };
    })
    .filter((e): e is MonthView => e !== null);
  return {
    status: pickString(o.status)?.toLowerCase() ?? null,
    challengerId: pickString(o.challenger_id),
    isOwner: pickBool(o.is_owner),
    isChallenger: pickBool(o.is_challenger),
    months,
    monthsSatisfied: pickNumber(o.months_satisfied),
    counterExpiresAt: pickString(o.counter_expires_at),
    forcedSalePrice: pickNumber(o.forced_sale_price)
  };
};

// Campaign phases where there is a live challenge worth charting
// (vocabulary: building | eligible | countered | disputed | failed | transferred)
const CAMPAIGN_LIVE = new Set(['building', 'eligible', 'countered']);

// --- PortSeal: registry-seal SVG flourish (house SVG style) ---

type SealState = 'unclaimed' | 'owned' | 'mine' | 'forsale';

const PortSeal: React.FC<{ state: SealState }> = ({ state }) => (
  <div className={`port-seal state-${state}`}>
    <svg
      viewBox="0 0 120 120"
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label={
        state === 'mine' ? 'Registry seal: deed held by you'
        : state === 'owned' ? 'Registry seal: deed held'
        : state === 'forsale' ? 'Registry seal: station on the sale board'
        : 'Registry seal: deed unclaimed'
      }
      className="port-seal-svg"
    >
      {/* Outer registry ring with notches */}
      <circle className="ps-ring" cx="60" cy="60" r="52" fill="none" />
      <circle className="ps-ring-inner" cx="60" cy="60" r="44" fill="none" />
      {Array.from({ length: 12 }, (_, i) => {
        const a = (i * Math.PI) / 6;
        const x1 = 60 + Math.cos(a) * 48;
        const y1 = 60 + Math.sin(a) * 48;
        const x2 = 60 + Math.cos(a) * 52;
        const y2 = 60 + Math.sin(a) * 52;
        return <line key={i} className="ps-notch" x1={x1} y1={y1} x2={x2} y2={y2} />;
      })}
      {/* Station silhouette: docking ring + spine + habitat */}
      <ellipse className="ps-station-ring" cx="60" cy="62" rx="26" ry="9" fill="none" />
      <line className="ps-spine" x1="60" y1="34" x2="60" y2="78" />
      <rect className="ps-hab" x="52" y="44" width="16" height="12" rx="2" />
      <circle className="ps-core" cx="60" cy="50" r="2.4" />
      {/* Deed flag on the mast — lit only when a deed is held */}
      <g className="ps-flag-group">
        <line className="ps-mast" x1="60" y1="34" x2="60" y2="22" />
        <path className="ps-flag" d="M 60 22 L 76 26 L 60 30 Z" />
      </g>
      {/* Auction gavel arc — only on the sale board */}
      <g className="ps-sale-group">
        <path className="ps-sale-arc" d="M 24 92 Q 60 104 96 92" fill="none" />
        <circle className="ps-sale-dot" cx="24" cy="92" r="2" />
        <circle className="ps-sale-dot" cx="96" cy="92" r="2" />
      </g>
    </svg>
  </div>
);

// --- The venue itself ---

interface PortOfficeVenueProps {
  stationId: string;
  stationName: string;
  credits: number;
  onCreditsSet: (value: number) => void;
  onBack: () => void;
}

type PortOfficeTab = 'registry' | 'owner' | 'warroom';

const PortOfficeVenue: React.FC<PortOfficeVenueProps> = ({
  stationId,
  stationName,
  credits,
  onCreditsSet,
  onBack
}) => {
  const {
    playerState,
    getListing,
    listStation,
    placeOffer,
    getMyStations,
    setStationTax,
    withdrawTreasury,
    getTakeoverStatus,
    launchTakeover,
    counterTakeover
  } = useGame();

  const [activeTab, setActiveTab] = useState<PortOfficeTab>('registry');

  // Registry / listing state
  const [listing, setListing] = useState<ListingView | null>(null);
  const [listingLoading, setListingLoading] = useState(false);
  const [listingError, setListingError] = useState<string | null>(null);

  // Owner console state
  const [myStation, setMyStation] = useState<MyStationView | null>(null);
  const [ownerLoading, setOwnerLoading] = useState(false);
  const [ownerError, setOwnerError] = useState<string | null>(null);

  // Takeover state
  const [takeover, setTakeover] = useState<TakeoverView | null>(null);
  const [takeoverLoading, setTakeoverLoading] = useState(false);
  const [takeoverError, setTakeoverError] = useState<string | null>(null);

  // Action plumbing
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [buyError, setBuyError] = useState<string | null>(null);
  const [buySuccess, setBuySuccess] = useState<string | null>(null);
  const [consoleError, setConsoleError] = useState<string | null>(null);
  const [consoleSuccess, setConsoleSuccess] = useState<string | null>(null);
  const [warError, setWarError] = useState<string | null>(null);
  const [warSuccess, setWarSuccess] = useState<string | null>(null);

  // Buy flow inputs
  const [bidInput, setBidInput] = useState('');

  // Owner console inputs
  const [taxPctInput, setTaxPctInput] = useState<number | null>(null);
  const [withdrawInput, setWithdrawInput] = useState('');

  // 1s clock for countdowns
  const [nowMs, setNowMs] = useState(() => Date.now());

  const isMine = Boolean(
    listing?.ownerId && playerState?.id && listing.ownerId === playerState.id
  );
  const iAmChallenger = Boolean(
    takeover?.isChallenger ??
    (takeover?.challengerId && playerState?.id && takeover.challengerId === playerState.id)
  );

  // --- Fetching ---

  const fetchListing = useCallback(async () => {
    setListingLoading(true);
    try {
      const data = await getListing(stationId);
      setListing(normalizeListing(data));
      setListingError(null);
    } catch (error) {
      setListingError(axiosErrorMessage(error, 'The registry clerk is not answering. Please try again.'));
    } finally {
      setListingLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stationId]);

  const fetchOwner = useCallback(async () => {
    setOwnerLoading(true);
    try {
      const data = await getMyStations();
      setMyStation(findMyStation(data, stationId));
      setOwnerError(null);
    } catch (error) {
      setOwnerError(axiosErrorMessage(error, 'Could not open your holdings ledger. Please try again.'));
    } finally {
      setOwnerLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stationId]);

  const fetchTakeover = useCallback(async () => {
    setTakeoverLoading(true);
    try {
      const data = await getTakeoverStatus(stationId);
      setTakeover(normalizeTakeover(data));
      setTakeoverError(null);
    } catch (error) {
      setTakeoverError(axiosErrorMessage(error, 'War-room intelligence feed is down. Please try again.'));
    } finally {
      setTakeoverLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stationId]);

  const fetchAll = useCallback(async () => {
    await Promise.allSettled([fetchListing(), fetchOwner(), fetchTakeover()]);
  }, [fetchListing, fetchOwner, fetchTakeover]);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  // Poll every 30s while the venue is open — grace windows and counter
  // windows resolve lazily on read, so polling IS the resolution trigger
  useEffect(() => {
    const interval = setInterval(() => {
      fetchAll();
    }, 30000);
    return () => clearInterval(interval);
  }, [fetchAll]);

  // 1s tick only while something is counting down
  const hasCountdowns = Boolean(listing?.graceExpiresAt || takeover?.counterExpiresAt);
  useEffect(() => {
    if (!hasCountdowns) return;
    const tick = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(tick);
  }, [hasCountdowns]);

  // Seed the tariff slider from server truth once it arrives
  const serverTaxPct = useMemo(() => {
    const rate = myStation?.taxRate ?? listing?.taxRate;
    return rate !== null && rate !== undefined ? Math.round(rate * 1000) / 10 : null;
  }, [myStation?.taxRate, listing?.taxRate]);

  useEffect(() => {
    if (taxPctInput === null && serverTaxPct !== null) {
      setTaxPctInput(serverTaxPct);
    }
  }, [serverTaxPct, taxPctInput]);

  // If I lose the owner tab (sale completed, takeover accepted), fall back
  useEffect(() => {
    if (activeTab === 'owner' && !isMine && listing) {
      setActiveTab('registry');
    }
  }, [activeTab, isMine, listing]);

  // --- Actions ---

  const runAction = useCallback(async <T,>(
    key: string,
    fn: () => Promise<T>,
    setError: (message: string | null) => void,
    fallback: string
  ): Promise<T | null> => {
    if (busyAction) return null;
    setBusyAction(key);
    setError(null);
    try {
      return await fn();
    } catch (error) {
      setError(axiosErrorMessage(error, fallback));
      return null;
    } finally {
      setBusyAction(null);
    }
  }, [busyAction]);

  const submitOffer = useCallback(async () => {
    const bid = parseInt(bidInput, 10);
    const floor = listing?.listPrice ?? 0;
    if (!Number.isFinite(bid) || bid <= 0) {
      setBuyError('Enter a bid amount in credits.');
      return;
    }
    if (floor > 0 && bid < floor) {
      setBuyError(`Bids below the list price are not accepted. The floor is ${floor.toLocaleString()} cr.`);
      return;
    }
    if (bid > BID_CEILING) {
      setBuyError(`The Port Authority clamps station prices at ${BID_CEILING.toLocaleString()} cr — bids above the statutory clamp are not accepted.`);
      return;
    }
    if (bid > credits) {
      setBuyError(`Insufficient credits to escrow this bid. Need ${bid.toLocaleString()}, have ${credits.toLocaleString()}.`);
      return;
    }
    setBuySuccess(null);
    const result = await runAction('offer', () => placeOffer(stationId, bid), setBuyError, 'The registry rejected your offer.');
    if (result !== null) {
      const newCredits = creditsFromResponse(result);
      if (newCredits !== null) onCreditsSet(newCredits);
      setBuySuccess(`Offer filed under seal — ${bid.toLocaleString()} cr escrowed with the Port Authority.`);
      setBidInput('');
      await fetchListing();
    }
  }, [bidInput, listing?.listPrice, credits, runAction, placeOffer, stationId, onCreditsSet, fetchListing]);

  const petitionListing = useCallback(async () => {
    setBuySuccess(null);
    const result = await runAction('list', () => listStation(stationId), setBuyError, 'The registry declined to open a sale on this station.');
    if (result !== null) {
      setBuySuccess('Sale opened — the deed is on the board and the grace window is running.');
      await fetchListing();
    }
  }, [runAction, listStation, stationId, fetchListing]);

  const confirmTax = useCallback(async () => {
    if (taxPctInput === null) return;
    const pct = Math.max(0, Math.min(25, taxPctInput));
    setConsoleSuccess(null);
    const result = await runAction('tax', () => setStationTax(stationId, pct / 100), setConsoleError, 'Tariff filing failed.');
    if (result !== null) {
      setConsoleSuccess(`Tariff posted at ${pct}% — effective on the next transaction.`);
      await Promise.allSettled([fetchOwner(), fetchListing()]);
    }
  }, [taxPctInput, runAction, setStationTax, stationId, fetchOwner, fetchListing]);

  const submitWithdraw = useCallback(async () => {
    const amount = parseInt(withdrawInput, 10);
    const vault = myStation?.treasury ?? listing?.treasuryBalance ?? 0;
    if (!Number.isFinite(amount) || amount <= 0) {
      setConsoleError('Enter an amount to withdraw.');
      return;
    }
    if (amount > vault) {
      setConsoleError(`The vault holds ${vault.toLocaleString()} cr — you cannot withdraw more than that.`);
      return;
    }
    setConsoleSuccess(null);
    const result = await runAction('withdraw', () => withdrawTreasury(stationId, amount), setConsoleError, 'Vault withdrawal failed.');
    if (result !== null) {
      const newCredits = creditsFromResponse(result);
      if (newCredits !== null) onCreditsSet(newCredits);
      setConsoleSuccess(`${amount.toLocaleString()} cr transferred from the station vault to your account.`);
      setWithdrawInput('');
      await Promise.allSettled([fetchOwner(), fetchListing()]);
    }
  }, [withdrawInput, myStation?.treasury, listing?.treasuryBalance, runAction, withdrawTreasury, stationId, onCreditsSet, fetchOwner, fetchListing]);

  const launchCampaign = useCallback(async () => {
    setWarSuccess(null);
    const result = await runAction('launch', () => launchTakeover(stationId), setWarError, 'The campaign filing was rejected.');
    if (result !== null) {
      setWarSuccess('Campaign filed. Outtrade the house — hold the majority of this station’s volume, month after month.');
      await fetchTakeover();
    }
  }, [runAction, launchTakeover, stationId, fetchTakeover]);

  const counter = useCallback(async (action: 'accept' | 'match' | 'dispute') => {
    setWarSuccess(null);
    const labels: Record<typeof action, string> = {
      accept: 'Forced sale executed — the deed and treasury have changed hands.',
      match: 'Match filed — if your volume held the month, the challenger’s clock resets.',
      dispute: 'Dispute filed with the Port Authority arbiter.'
    };
    const result = await runAction(`counter-${action}`, () => counterTakeover(stationId, action), setWarError, 'Counter filing failed.');
    if (result !== null) {
      const newCredits = creditsFromResponse(result);
      if (newCredits !== null) onCreditsSet(newCredits);
      setWarSuccess(labels[action]);
      await fetchAll();
    }
  }, [runAction, counterTakeover, stationId, onCreditsSet, fetchAll]);

  // --- Render helpers ---

  const sealState: SealState = isMine
    ? 'mine'
    : listing?.isListed
      ? 'forsale'
      : listing?.ownerId
        ? 'owned'
        : 'unclaimed';

  const renderCountdownRow = (icon: string, label: string, iso: string) => {
    const { text, expired, urgent } = fmtCountdown(iso, nowMs);
    return (
      <div className={`po-countdown${expired ? ' expired' : urgent ? ' urgent' : ''}`}>
        <span className="po-countdown-icon" aria-hidden="true">{icon}</span>
        <span className="po-countdown-label">{label}</span>
        <span className="po-countdown-value">{text}</span>
      </div>
    );
  };

  const renderStatusPanel = () => {
    if (listingLoading && !listing) {
      return <div className="catalog-loading">Pulling the deed file from the registry archive...</div>;
    }
    if (listingError && !listing) {
      return (
        <div className="genesis-error-message">
          <span className="error-icon">❌</span>
          {listingError}
          <button className="action-button" onClick={fetchListing}>Retry</button>
        </div>
      );
    }
    if (!listing) {
      return (
        <p className="section-description">
          The registry has no deed file for this station yet.
        </p>
      );
    }

    return (
      <div className="po-status-panel">
        <PortSeal state={sealState} />
        <div className="po-status-detail">
          <div className="po-badges">
            {isMine ? (
              <span className="po-badge mine">DEED HELD — YOURS</span>
            ) : listing.ownerId ? (
              <span className="po-badge owned">OWNED{listing.ownerName ? ` BY ${listing.ownerName.toUpperCase()}` : ''}</span>
            ) : (
              <span className="po-badge unclaimed">UNCLAIMED</span>
            )}
            {listing.isListed && <span className="po-badge forsale">FOR SALE</span>}
          </div>

          {listing.isListed && (
            <div className="po-sale-terms">
              {listing.listPrice !== null && (
                <div className="po-term-row">
                  <span>List price</span>
                  <span>{listing.listPrice.toLocaleString()} cr</span>
                </div>
              )}
              {listing.offersCount !== null && (
                <div className="po-term-row">
                  <span>Sealed offers filed</span>
                  <span>{listing.offersCount}</span>
                </div>
              )}
              {listing.graceExpiresAt && renderCountdownRow('⏱️', 'Grace window closes', listing.graceExpiresAt)}
            </div>
          )}

          {!listing.isListed && !listing.ownerId && (
            <p className="po-flavor">
              No deed has ever been cut for {stationName}. The Port Authority will open a sale
              on petition — if the station qualifies for private ownership.
            </p>
          )}
          {!listing.isListed && listing.ownerId && !isMine && (
            <p className="po-flavor">
              The deed is privately held and not on the sale board. If you want this station,
              the War Room is the long way around.
            </p>
          )}
          {isMine && (
            <p className="po-flavor">
              Your name is on the deed. Tariffs, the vault, and the ledger are in the Owner Console.
            </p>
          )}
        </div>
      </div>
    );
  };

  const renderBuySection = () => {
    if (!listing || isMine) return null;

    const purchaseBlocked = listing.purchasable === false;
    const myOffer = listing.myOffer;

    return (
      <div className="po-section">
        <h3 className="po-section-title">📨 Acquisition Desk</h3>

        {purchaseBlocked && (
          <div className="po-blocked-note">
            🚫 {listing.blockedReason || 'This station does not qualify for private ownership.'}
          </div>
        )}

        {!purchaseBlocked && !listing.isListed && (
          <>
            <p className="section-description">
              The deed is not on the sale board. Petition the registry to open a sale —
              the Port Authority sets the price from class, region, and the station&apos;s books.
            </p>
            <button
              className="action-button primary"
              onClick={petitionListing}
              disabled={Boolean(busyAction)}
            >
              {busyAction === 'list' ? 'Filing...' : 'Petition to Open Sale'}
            </button>
          </>
        )}

        {!purchaseBlocked && listing.isListed && (
          <>
            <p className="section-description">
              Offers are <strong>sealed bids</strong>: your credits are escrowed the moment you file.
              If yours is the only offer when the grace window closes, the deed is yours at list price.
              If others file too, highest sealed bid takes it — losing bids are refunded in full.
            </p>

            {myOffer ? (
              <div className="po-my-offer">
                <span className="po-my-offer-label">📜 Your sealed offer</span>
                <span className="po-my-offer-amount">
                  {myOffer.bidAmount !== null ? `${myOffer.bidAmount.toLocaleString()} cr` : 'filed'}
                </span>
                <span className="po-my-offer-status">
                  {myOffer.status ? myOffer.status.replace(/_/g, ' ').toUpperCase() : 'IN ESCROW'}
                </span>
              </div>
            ) : (
              <div className="po-bid-row">
                <label htmlFor="po-bid-input">Sealed bid (cr)</label>
                <input
                  id="po-bid-input"
                  type="number"
                  min={listing.listPrice ?? 1}
                  max={BID_CEILING}
                  value={bidInput}
                  onChange={e => setBidInput(e.target.value)}
                  placeholder={listing.listPrice !== null ? `${listing.listPrice.toLocaleString()} minimum` : 'Bid amount'}
                  disabled={Boolean(busyAction)}
                />
                <button
                  className="action-button primary"
                  onClick={submitOffer}
                  disabled={Boolean(busyAction) || !bidInput}
                >
                  {busyAction === 'offer' ? 'Filing...' : 'File Sealed Offer'}
                </button>
              </div>
            )}
            <p className="po-escrow-note">
              ⚖️ Escrow notice: the full bid leaves your account at filing and is returned only if outbid.
            </p>
          </>
        )}

        {buyError && (
          <div className="genesis-error-message">
            <span className="error-icon">❌</span>
            {buyError}
          </div>
        )}
        {buySuccess && (
          <div className="genesis-success-message">
            <span className="success-icon">✅</span>
            {buySuccess}
          </div>
        )}
      </div>
    );
  };

  const renderOwnerConsole = () => {
    if (!isMine) return null;

    const vault = myStation?.treasury ?? listing?.treasuryBalance ?? null;
    // The vault gauge needs a scale. Use the server-declared capacity when
    // present; otherwise scale against the statutory 2,000,000 cr price
    // clamp ceiling from the ownership spec — a meaningful in-fiction yardstick.
    const gaugeMax = myStation?.treasuryCapacity ?? 2_000_000;
    const gaugePct = vault !== null && gaugeMax > 0 ? Math.min(100, (vault / gaugeMax) * 100) : 0;
    const taxPct = taxPctInput ?? serverTaxPct ?? 10;

    return (
      <div className="po-owner-console">
        {ownerLoading && !myStation && (
          <div className="catalog-loading">Opening the station books...</div>
        )}
        {ownerError && (
          <div className="genesis-error-message">
            <span className="error-icon">❌</span>
            {ownerError}
            <button className="action-button" onClick={fetchOwner}>Retry</button>
          </div>
        )}

        {/* Tariff lever */}
        <div className="po-section">
          <h3 className="po-section-title">🧾 Trade Tariff</h3>
          <p className="section-description">
            Every credit traded at {stationName} pays your tariff into the station vault.
            Squeeze too hard and the freighters route around you. Bounds: 0–25%.
          </p>
          <div className="po-tariff-row">
            <input
              type="range"
              min={0}
              max={25}
              step={0.5}
              value={taxPct}
              onChange={e => setTaxPctInput(parseFloat(e.target.value))}
              disabled={Boolean(busyAction)}
              aria-label="Trade tariff percentage"
            />
            <span className="po-tariff-value">{taxPct}%</span>
            <button
              className="action-button primary"
              onClick={confirmTax}
              disabled={Boolean(busyAction) || taxPctInput === null || taxPctInput === serverTaxPct}
            >
              {busyAction === 'tax' ? 'Posting...' : 'Post Tariff'}
            </button>
          </div>
          {serverTaxPct !== null && (
            <div className="po-tariff-current">Currently posted: {serverTaxPct}%</div>
          )}
        </div>

        {/* Treasury vault — citadel vault gauge visual language */}
        <div className="po-section">
          <h3 className="po-section-title">🔐 Station Vault</h3>
          {vault === null ? (
            <p className="section-description">The vault ledger has not arrived from the registry yet.</p>
          ) : (
            <>
              <div
                className="po-vault-gauge"
                role="meter"
                aria-valuemin={0}
                aria-valuemax={gaugeMax}
                aria-valuenow={vault}
                aria-label={`Station vault: ${vault.toLocaleString()} credits`}
                title={`${gaugePct.toFixed(1)}% of the ${gaugeMax.toLocaleString()} cr gauge scale`}
              >
                <div className="po-vault-fill" style={{ width: `${gaugePct}%` }} />
                <div className="po-vault-segments" aria-hidden="true" />
              </div>
              <div className="po-vault-readout">
                <span className="po-vault-amount">{vault.toLocaleString()}</span>
                <span className="po-vault-capacity">cr in the vault</span>
              </div>
              <div className="po-withdraw-row">
                <input
                  type="number"
                  min={1}
                  max={vault}
                  value={withdrawInput}
                  onChange={e => setWithdrawInput(e.target.value)}
                  placeholder="Amount"
                  disabled={Boolean(busyAction) || vault <= 0}
                  aria-label="Credits to withdraw from the station vault"
                />
                <button
                  className="po-max-btn"
                  onClick={() => setWithdrawInput(String(vault))}
                  disabled={Boolean(busyAction) || vault <= 0}
                >
                  Max
                </button>
                <button
                  className="action-button primary"
                  onClick={submitWithdraw}
                  disabled={Boolean(busyAction) || vault <= 0 || !withdrawInput}
                >
                  {busyAction === 'withdraw' ? 'Transferring...' : 'Withdraw'}
                </button>
              </div>
            </>
          )}
        </div>

        {/* Revenue ledger */}
        <div className="po-section">
          <h3 className="po-section-title">📊 Revenue Ledger</h3>
          {!myStation ? (
            !ownerLoading && (
              <p className="section-description">
                No ledger entries returned for this station yet — revenue appears here
                as trade clears the books.
              </p>
            )
          ) : (
            <>
              <div className="po-ledger-rows">
                {myStation.revenue30d !== null && (
                  <div className="po-term-row">
                    <span>Trailing 30 days</span>
                    <span>{myStation.revenue30d.toLocaleString()} cr</span>
                  </div>
                )}
                {myStation.revenue90d !== null && (
                  <div className="po-term-row">
                    <span>Trailing 90 days</span>
                    <span>{myStation.revenue90d.toLocaleString()} cr</span>
                  </div>
                )}
                {myStation.acquisitionCost !== null && (
                  <div className="po-term-row">
                    <span>Acquisition cost</span>
                    <span>{myStation.acquisitionCost.toLocaleString()} cr</span>
                  </div>
                )}
              </div>
              {myStation.monthly.length > 0 && (
                <div className="po-ledger-months">
                  {myStation.monthly.map(m => (
                    <div key={m.label} className="po-term-row">
                      <span>{m.label}</span>
                      <span>{m.amount.toLocaleString()} cr</span>
                    </div>
                  ))}
                </div>
              )}
              {myStation.revenue30d === null && myStation.revenue90d === null && myStation.monthly.length === 0 && (
                <p className="section-description">
                  The ledger is open but empty — no taxable trade has cleared yet.
                </p>
              )}
            </>
          )}
        </div>

        {consoleError && (
          <div className="genesis-error-message">
            <span className="error-icon">❌</span>
            {consoleError}
          </div>
        )}
        {consoleSuccess && (
          <div className="genesis-success-message">
            <span className="success-icon">✅</span>
            {consoleSuccess}
          </div>
        )}
      </div>
    );
  };

  const renderShareChart = (months: MonthView[]) => (
    <div className="po-share-chart" role="img" aria-label="Monthly share of station trade volume">
      <div className="po-share-legend" aria-hidden="true">
        <span className="po-threshold-swatch" /> 50% takeover threshold
      </div>
      <div className="po-share-bars">
        {months.map((m, idx) => (
          <div key={`${m.label}-${idx}`} className="po-share-col">
            <div className="po-share-track">
              {m.sharePct !== null && (
                <div
                  className={`po-share-fill${m.sharePct > 50 ? ' over' : ''}${m.qualifies ? ' qualifies' : ''}`}
                  style={{ height: `${m.sharePct}%` }}
                  title={`${m.sharePct.toFixed(1)}% of station volume${m.totalVolume !== null ? ` (${m.totalVolume.toLocaleString()} cr total)` : ''}${m.hostile === true ? ' — hostile pricing' : ''}${m.qualifies ? ' — qualifying month' : ''}`}
                />
              )}
              <div className="po-share-threshold" aria-hidden="true" />
            </div>
            <span className="po-share-pct">
              {m.sharePct !== null ? `${Math.round(m.sharePct)}%` : '—'}
            </span>
            <span className="po-share-month">{m.label}</span>
            <span
              className={`po-share-hostile${m.hostile === true ? '' : ' hidden'}`}
              title={m.hostile === true ? 'Hostile pricing this month' : undefined}
            >
              ⚔️
            </span>
          </div>
        ))}
      </div>
    </div>
  );

  const renderWarRoom = () => {
    if (takeoverLoading && !takeover) {
      return <div className="catalog-loading">Decrypting the volume intercepts...</div>;
    }
    if (takeoverError && !takeover) {
      return (
        <div className="genesis-error-message">
          <span className="error-icon">❌</span>
          {takeoverError}
          <button className="action-button" onClick={fetchTakeover}>Retry</button>
        </div>
      );
    }

    const status = takeover?.status ?? 'none';
    // 'disputed' renders as live with a dispute-pending note
    const live = CAMPAIGN_LIVE.has(status) || status === 'disputed';
    const required = 3;
    const counterOpen = status === 'eligible' && Boolean(takeover?.counterExpiresAt);

    return (
      <div className="po-war-room">
        <p className="section-description">
          Ownership can be taken without a single shot: hold the majority of a station&apos;s
          trade volume with hostile pricing for {required} consecutive months and the Port
          Authority will force the deed onto the table.
        </p>

        {/* Campaign status */}
        <div className="po-section">
          <h3 className="po-section-title">⚔️ Campaign Status</h3>
          {!takeover || !live ? (
            <>
              <p className="section-description">
                {status === 'failed'
                  ? 'The last campaign against this station collapsed. The board is clear.'
                  : status === 'transferred'
                    ? 'The deed has already been transferred — a takeover here ran to completion. The board is clear.'
                    : 'No economic campaign is currently underway against this station.'}
              </p>
              {!isMine && (
                <button
                  className="action-button primary"
                  onClick={launchCampaign}
                  disabled={Boolean(busyAction)}
                >
                  {busyAction === 'launch' ? 'Filing...' : '🚩 Launch Takeover Campaign'}
                </button>
              )}
              {isMine && (
                <p className="po-flavor">
                  Quiet on all channels. Keep your prices honest and your volume up, and it stays that way.
                </p>
              )}
            </>
          ) : (
            <>
              <div className="po-campaign-head">
                <span className={`po-badge campaign-${status}`}>
                  {status.replace(/_/g, ' ').toUpperCase()}
                </span>
                {(iAmChallenger || takeover.challengerId) && (
                  <span className="po-campaign-challenger">
                    Challenger: <strong>{iAmChallenger ? 'you' : 'a rival trader'}</strong>
                  </span>
                )}
                {takeover.monthsSatisfied !== null && (
                  <span className="po-campaign-progress">
                    {takeover.monthsSatisfied} / {required} qualifying months
                  </span>
                )}
              </div>
              {status === 'disputed' && (
                <p className="po-flavor">
                  📜 A dispute is pending before the Port Authority arbiter — the campaign
                  holds while the challenger&apos;s books are audited for wash trades.
                </p>
              )}
              {takeover.forcedSalePrice !== null && (
                <div className="po-term-row">
                  <span>Forced-sale price on the table</span>
                  <span>{takeover.forcedSalePrice.toLocaleString()} cr</span>
                </div>
              )}
              {counterOpen && takeover.counterExpiresAt &&
                renderCountdownRow('🛡️', 'Owner counter window', takeover.counterExpiresAt)}
            </>
          )}
        </div>

        {/* Monthly share-of-volume bars */}
        {takeover && takeover.months.length > 0 && (
          <div className="po-section">
            <h3 className="po-section-title">📈 Share of Station Volume</h3>
            {renderShareChart(takeover.months)}
            <p className="po-flavor">
              Bars above the 50% line are months the challenger out-traded the house.
              ⚔️ marks months priced to undercut the station midpoint.
            </p>
          </div>
        )}

        {/* Owner counter desk */}
        {isMine && counterOpen && (
          <div className="po-section po-counter-desk">
            <h3 className="po-section-title">🛡️ Owner Counter Desk</h3>
            <p className="section-description">
              The challenger has met the takeover threshold. You have until the counter window
              closes to answer — silence is treated as acceptance.
            </p>
            <div className="po-counter-actions">
              <button
                className="action-button po-counter-accept"
                onClick={() => counter('accept')}
                disabled={Boolean(busyAction)}
              >
                {busyAction === 'counter-accept' ? 'Signing...' : '✍️ Accept Forced Sale'}
              </button>
              <button
                className="action-button po-counter-match"
                onClick={() => counter('match')}
                disabled={Boolean(busyAction)}
              >
                {busyAction === 'counter-match' ? 'Filing...' : '⚖️ Match Their Volume'}
              </button>
              <button
                className="action-button po-counter-dispute"
                onClick={() => counter('dispute')}
                disabled={Boolean(busyAction)}
              >
                {busyAction === 'counter-dispute' ? 'Filing...' : '📜 Dispute (Arbitration)'}
              </button>
            </div>
            <ul className="po-counter-rules">
              <li><strong>Accept</strong> — sell at the forced-sale price; deed and vault transfer together.</li>
              <li><strong>Match</strong> — if your own volume this month meets the challenger&apos;s, their clock resets to zero.</li>
              <li><strong>Dispute</strong> — the arbiter audits the challenger&apos;s books for self-dealing wash trades.</li>
            </ul>
          </div>
        )}

        {warError && (
          <div className="genesis-error-message">
            <span className="error-icon">❌</span>
            {warError}
          </div>
        )}
        {warSuccess && (
          <div className="genesis-success-message">
            <span className="success-icon">✅</span>
            {warSuccess}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="venue-container port-office">
      <div className="venue-header">
        <button className="back-button" onClick={onBack}>
          ← Back to Hub
        </button>
        <h2>🏛️ Port Office</h2>
        {isMine && <span className="po-deed-badge">DEED HOLDER</span>}
      </div>

      <div className="venue-content-area">
        <div className="po-intro">
          <p>
            The Port Authority registry for {stationName}: deeds, tariffs, sealed-bid sales,
            and the slow knife of economic takeover. Everything here is a matter of public record —
            except the bids.
          </p>
        </div>

        <div className="po-tabs" role="tablist">
          <button
            role="tab"
            aria-selected={activeTab === 'registry'}
            className={`po-tab${activeTab === 'registry' ? ' active' : ''}`}
            onClick={() => setActiveTab('registry')}
          >
            📋 Registry
          </button>
          {isMine && (
            <button
              role="tab"
              aria-selected={activeTab === 'owner'}
              className={`po-tab${activeTab === 'owner' ? ' active' : ''}`}
              onClick={() => setActiveTab('owner')}
            >
              🏛️ Owner Console
            </button>
          )}
          <button
            role="tab"
            aria-selected={activeTab === 'warroom'}
            className={`po-tab${activeTab === 'warroom' ? ' active' : ''}`}
            onClick={() => setActiveTab('warroom')}
          >
            ⚔️ War Room
          </button>
        </div>

        {activeTab === 'registry' && (
          <div className="po-registry">
            {renderStatusPanel()}
            {listingError && listing && (
              <div className="genesis-error-message">
                <span className="error-icon">❌</span>
                {listingError}
                <button className="action-button" onClick={fetchListing}>Retry</button>
              </div>
            )}
            {renderBuySection()}
          </div>
        )}

        {activeTab === 'owner' && renderOwnerConsole()}

        {activeTab === 'warroom' && renderWarRoom()}
      </div>
    </div>
  );
};

export default PortOfficeVenue;
