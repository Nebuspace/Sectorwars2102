import React, { useState, useCallback, useEffect, useMemo } from 'react';
import { useGame } from '../../contexts/GameContext';
import { formatCredits } from '../../utils/formatters';
import DeckPageTabs from '../cockpit/DeckPageTabs';
import StationSecurityMonitoringPane from '../station/StationSecurityMonitoringPane';
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
export function formatPortOfficeVenueError(error: unknown, fallback: string): string {
  // Network collapse (fetch TypeError) is not gameserver copy.
  if (error instanceof TypeError) return fallback;
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
}

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

// Ratified economic-defense magnitudes (port-ownership.md / DECISIONS).
const COUNTER_TRADE_MAX_ABSORB = 500_000;
const FRIENDLY_TRADE_MAX_VOLUME = 500_000;
const COUNTER_TRADE_CREDITS_PER_VOLUME = 1;

// Owner revenue levers — tip GS Field bounds (port_ownership.py request models).
const PRICE_LEVER_MIN = -0.1;
const PRICE_LEVER_MAX = 0.1;
const DOCKING_FEE_MIN = 50;
const DOCKING_FEE_MAX = 500;
const SERVICE_CHARGE_MIN = 0.8;
const SERVICE_CHARGE_MAX = 2.0;
const STORAGE_RENTAL_MIN = 1000;
const STORAGE_RENTAL_MAX = 10_000;

// Fee-distribution bounds (operating immutable 30%).
const FEE_DEFENSE_MIN = 0.3;
const FEE_DEFENSE_MAX = 0.6;
const FEE_OWNER_MIN = 0.1;
const FEE_OWNER_MAX = 0.5;
const FEE_OPERATING = 0.3;
const FEE_UNDERFUND_WARN = 0.35;

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
  /** LEG-370 / LEG-371 — tip my-stations price lever fields */
  priceAdjustmentLever: number | null;
  dockingFee: number | null;
  dockingFeeEnabled: boolean | null;
  serviceChargeMultiplier: number | null;
  storageRentalPerDay: number | null;
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
    monthly,
    priceAdjustmentLever: pickNumber(o.price_adjustment_lever),
    dockingFee: pickNumber(o.docking_fee),
    dockingFeeEnabled: pickBool(o.docking_fee_enabled),
    serviceChargeMultiplier: pickNumber(o.service_charge_multiplier),
    storageRentalPerDay: pickNumber(o.storage_rental_per_day),
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

type DockingAccess = 'open' | 'faction' | 'whitelist' | 'hostile_deny';

type DefensePolicyForm = {
  dockingAccess: DockingAccess;
  hostilityListText: string;
  punitiveFeeMult: number;
  defenderPosture: string;
  droneAllocationPct: number;
};

const DEFAULT_DEFENSE_FORM: DefensePolicyForm = {
  dockingAccess: 'open',
  hostilityListText: '',
  punitiveFeeMult: 1,
  defenderPosture: 'passive',
  droneAllocationPct: 100,
};

const DOCKING_ACCESS_OPTIONS: Array<{ value: DockingAccess; label: string }> = [
  { value: 'open', label: 'Open' },
  { value: 'faction', label: 'Faction reputation gate' },
  { value: 'whitelist', label: 'Whitelist (list = allow)' },
  { value: 'hostile_deny', label: 'Deny hostility list' },
];

const POSTURE_OPTIONS = ['passive', 'active', 'aggressive'] as const;

const normalizeDefensePolicy = (raw: unknown): DefensePolicyForm => {
  const root = asRecord(raw);
  const policy = asRecord(root?.defense_policy) ?? root;
  const accessRaw = pickString(policy?.docking_access) ?? 'open';
  const dockingAccess: DockingAccess =
    accessRaw === 'faction' || accessRaw === 'whitelist' || accessRaw === 'hostile_deny' || accessRaw === 'open'
      ? accessRaw
      : 'open';
  const listRaw = policy?.hostility_list;
  const hostilityListText = Array.isArray(listRaw)
    ? listRaw.map((id) => String(id)).filter(Boolean).join('\n')
    : '';
  const punitiveFeeMult = pickNumber(policy?.punitive_fee_mult) ?? 1;
  const defenderPosture = pickString(policy?.defender_posture) ?? 'passive';
  const droneAllocationPct = pickNumber(policy?.drone_allocation_pct) ?? 100;
  return {
    dockingAccess,
    hostilityListText,
    punitiveFeeMult: Math.max(1, Math.min(5, punitiveFeeMult)),
    defenderPosture,
    droneAllocationPct: Math.max(0, Math.min(100, Math.round(droneAllocationPct))),
  };
};

const parseHostilityList = (text: string): string[] =>
  text
    .split(/[\n,]+/)
    .map((s) => s.trim())
    .filter(Boolean);

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
    setPriceLever,
    setDockingFee,
    setServiceCharge,
    setStorageRental,
    withdrawTreasury,
    getDefensePolicy,
    setDefensePolicy,
    getTakeoverStatus,
    launchTakeover,
    counterTakeover,
    activateTariffCut,
    activateCounterTrade,
    activateFriendlyTrade,
    setFeeDistribution,
    militaryTakeover,
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

  // Defense policy (owner-only)
  const [defenseForm, setDefenseForm] = useState<DefensePolicyForm>(DEFAULT_DEFENSE_FORM);
  const [defenseLoading, setDefenseLoading] = useState(false);
  const [defenseError, setDefenseError] = useState<string | null>(null);

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

  // Revenue levers (LEG-366) — defaults match tip GS Field baselines when unset.
  const [priceLeverPct, setPriceLeverPct] = useState(0);
  const [dockingFeeAmount, setDockingFeeAmount] = useState(50);
  const [dockingFeeEnabled, setDockingFeeEnabled] = useState(true);
  const [serviceChargeMult, setServiceChargeMult] = useState(1.0);
  const [storageRentalPerDay, setStorageRentalPerDay] = useState(1000);

  // Economic takeover defense (LEG-INI-35)
  const [counterVolumeInput, setCounterVolumeInput] = useState('');
  const [friendlyVolumeInput, setFriendlyVolumeInput] = useState('');
  const [allyTeamIdInput, setAllyTeamIdInput] = useState('');
  const [allyFactionInput, setAllyFactionInput] = useState('');

  // Fee distribution rebalance (LEG-INI-36) — defense% slider; owner = 0.7 − defense
  const [defensePctInput, setDefensePctInput] = useState(0.4);

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
      setListingError(formatPortOfficeVenueError(error, 'The registry clerk is not answering. Please try again.'));
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
      setOwnerError(formatPortOfficeVenueError(error, 'Could not open your holdings ledger. Please try again.'));
    } finally {
      setOwnerLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stationId]);

  const fetchDefense = useCallback(async () => {
    setDefenseLoading(true);
    try {
      const data = await getDefensePolicy(stationId);
      setDefenseForm(normalizeDefensePolicy(data));
      setDefenseError(null);
    } catch (error) {
      setDefenseError(formatPortOfficeVenueError(error, 'Defense policy feed is down. Please try again.'));
    } finally {
      setDefenseLoading(false);
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
      setTakeoverError(formatPortOfficeVenueError(error, 'War-room intelligence feed is down. Please try again.'));
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

  // LEG-371 — hydrate revenue levers from tip my-stations (LEG-370 keys).
  useEffect(() => {
    if (!myStation) return;
    if (myStation.priceAdjustmentLever !== null) {
      setPriceLeverPct(myStation.priceAdjustmentLever);
    }
    if (myStation.dockingFee !== null) {
      setDockingFeeAmount(myStation.dockingFee);
    }
    if (myStation.dockingFeeEnabled !== null) {
      setDockingFeeEnabled(myStation.dockingFeeEnabled);
    }
    if (myStation.serviceChargeMultiplier !== null) {
      setServiceChargeMult(myStation.serviceChargeMultiplier);
    }
    if (myStation.storageRentalPerDay !== null) {
      setStorageRentalPerDay(myStation.storageRentalPerDay);
    }
  }, [myStation]);

  // Load owner-only defense policy once ownership is confirmed
  useEffect(() => {
    if (isMine) {
      void fetchDefense();
    } else {
      setDefenseForm(DEFAULT_DEFENSE_FORM);
      setDefenseError(null);
    }
  }, [isMine, fetchDefense]);

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
      setError(formatPortOfficeVenueError(error, fallback));
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
      setBuyError(`Bids below the list price are not accepted. The floor is ${formatCredits(floor)}.`);
      return;
    }
    if (bid > BID_CEILING) {
      setBuyError(`The Port Authority clamps station prices at ${formatCredits(BID_CEILING)} — bids above the statutory clamp are not accepted.`);
      return;
    }
    if (bid > credits) {
      setBuyError(`Insufficient credits to escrow this bid. Need ${formatCredits(bid)}, have ${formatCredits(credits)}.`);
      return;
    }
    setBuySuccess(null);
    const result = await runAction('offer', () => placeOffer(stationId, bid), setBuyError, 'The registry rejected your offer.');
    if (result !== null) {
      const newCredits = creditsFromResponse(result);
      if (newCredits !== null) onCreditsSet(newCredits);
      setBuySuccess(`Offer filed under seal — ${formatCredits(bid)} escrowed with the Port Authority.`);
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

  const submitPriceLever = useCallback(async () => {
    const pct = Math.min(PRICE_LEVER_MAX, Math.max(PRICE_LEVER_MIN, priceLeverPct));
    setConsoleSuccess(null);
    const result = await runAction(
      'price-lever',
      () => setPriceLever(stationId, pct),
      setConsoleError,
      'Price lever update failed.',
    );
    if (result !== null) {
      setPriceLeverPct(pct);
      setConsoleSuccess(`Price lever posted at ${(pct * 100).toFixed(0)}%.`);
      await fetchOwner();
    }
  }, [priceLeverPct, runAction, setPriceLever, stationId, fetchOwner]);

  const submitDockingFee = useCallback(async () => {
    const amount = Math.min(
      DOCKING_FEE_MAX,
      Math.max(DOCKING_FEE_MIN, Math.round(dockingFeeAmount)),
    );
    setConsoleSuccess(null);
    const result = await runAction(
      'docking-fee',
      () => setDockingFee(stationId, amount, dockingFeeEnabled),
      setConsoleError,
      'Docking fee update failed.',
    );
    if (result !== null) {
      setDockingFeeAmount(amount);
      setConsoleSuccess(
        `Docking fee posted: ${amount.toLocaleString()} cr (${dockingFeeEnabled ? 'on' : 'off'}).`,
      );
      await fetchOwner();
    }
  }, [dockingFeeAmount, dockingFeeEnabled, runAction, setDockingFee, stationId, fetchOwner]);

  const submitServiceCharge = useCallback(async () => {
    const multiplier = Math.min(
      SERVICE_CHARGE_MAX,
      Math.max(SERVICE_CHARGE_MIN, serviceChargeMult),
    );
    setConsoleSuccess(null);
    const result = await runAction(
      'service-charge',
      () => setServiceCharge(stationId, multiplier),
      setConsoleError,
      'Service charge update failed.',
    );
    if (result !== null) {
      setServiceChargeMult(multiplier);
      setConsoleSuccess(`Service charge posted at ${multiplier.toFixed(1)}×.`);
      await fetchOwner();
    }
  }, [serviceChargeMult, runAction, setServiceCharge, stationId, fetchOwner]);

  const submitStorageRental = useCallback(async () => {
    const perDay = Math.min(
      STORAGE_RENTAL_MAX,
      Math.max(STORAGE_RENTAL_MIN, Math.round(storageRentalPerDay)),
    );
    setConsoleSuccess(null);
    const result = await runAction(
      'storage-rental',
      () => setStorageRental(stationId, perDay),
      setConsoleError,
      'Storage rental update failed.',
    );
    if (result !== null) {
      setStorageRentalPerDay(perDay);
      setConsoleSuccess(`Storage rental posted at ${perDay.toLocaleString()} cr/day.`);
      await fetchOwner();
    }
  }, [storageRentalPerDay, runAction, setStorageRental, stationId, fetchOwner]);

  const submitWithdraw = useCallback(async () => {
    const amount = parseInt(withdrawInput, 10);
    const vault = myStation?.treasury ?? listing?.treasuryBalance ?? 0;
    if (!Number.isFinite(amount) || amount <= 0) {
      setConsoleError('Enter an amount to withdraw.');
      return;
    }
    if (amount > vault) {
      setConsoleError(`The vault holds ${formatCredits(vault)} — you cannot withdraw more than that.`);
      return;
    }
    setConsoleSuccess(null);
    const result = await runAction('withdraw', () => withdrawTreasury(stationId, amount), setConsoleError, 'Vault withdrawal failed.');
    if (result !== null) {
      const newCredits = creditsFromResponse(result);
      if (newCredits !== null) onCreditsSet(newCredits);
      setConsoleSuccess(`${formatCredits(amount)} transferred from the station vault to your account.`);
      setWithdrawInput('');
      await Promise.allSettled([fetchOwner(), fetchListing()]);
    }
  }, [withdrawInput, myStation?.treasury, listing?.treasuryBalance, runAction, withdrawTreasury, stationId, onCreditsSet, fetchOwner, fetchListing]);

  const submitDefensePolicy = useCallback(async () => {
    const mult = Math.max(1, Math.min(5, Number(defenseForm.punitiveFeeMult)));
    const dronePct = Math.max(0, Math.min(100, Math.round(Number(defenseForm.droneAllocationPct))));
    if (!Number.isFinite(mult) || !Number.isFinite(dronePct)) {
      setConsoleError('Punitive fee and drone allocation must be valid numbers.');
      return;
    }
    setConsoleSuccess(null);
    const result = await runAction(
      'defense',
      () =>
        setDefensePolicy(stationId, {
          docking_access: defenseForm.dockingAccess,
          hostility_list: parseHostilityList(defenseForm.hostilityListText),
          punitive_fee_mult: mult,
          defender_posture: defenseForm.defenderPosture.trim() || 'passive',
          drone_allocation_pct: dronePct,
        }),
      setConsoleError,
      'Defense policy update failed.',
    );
    if (result !== null) {
      setDefenseForm(normalizeDefensePolicy(result));
      setConsoleSuccess('Defense policy posted — docking and combat levers are live.');
      await fetchDefense();
    }
  }, [defenseForm, runAction, setDefensePolicy, stationId, fetchDefense]);

  const submitTariffCut = useCallback(async () => {
    setConsoleSuccess(null);
    const result = await runAction(
      'tariff-cut',
      () => activateTariffCut(stationId),
      setConsoleError,
      'Tariff cut failed — an active campaign window may be required.',
    );
    if (result !== null) {
      setConsoleSuccess('Tariff cut activated — rate halved for the counter window (floored at the statutory minimum).');
      await Promise.allSettled([fetchOwner(), fetchListing(), fetchTakeover()]);
    }
  }, [runAction, activateTariffCut, stationId, fetchOwner, fetchListing, fetchTakeover]);

  const submitCounterTrade = useCallback(async () => {
    const volume = parseInt(counterVolumeInput, 10);
    if (!Number.isFinite(volume) || volume < 1) {
      setConsoleError('Enter a counter-trade absorb volume of at least 1.');
      return;
    }
    if (volume > COUNTER_TRADE_MAX_ABSORB) {
      setConsoleError(
        `Counter-trade absorb is capped at ${COUNTER_TRADE_MAX_ABSORB.toLocaleString()} per activation.`,
      );
      return;
    }
    setConsoleSuccess(null);
    const result = await runAction(
      'counter-trade',
      () => activateCounterTrade(stationId, volume),
      setConsoleError,
      'Counter-trade absorb failed.',
    );
    if (result !== null) {
      const cost = volume * COUNTER_TRADE_CREDITS_PER_VOLUME;
      setConsoleSuccess(
        `Counter-trade absorb ${volume.toLocaleString()} posted (≈ ${formatCredits(cost)}).`,
      );
      setCounterVolumeInput('');
      const newCredits = creditsFromResponse(result);
      if (newCredits !== null) onCreditsSet(newCredits);
      await Promise.allSettled([fetchOwner(), fetchTakeover()]);
    }
  }, [
    counterVolumeInput,
    runAction,
    activateCounterTrade,
    stationId,
    onCreditsSet,
    fetchOwner,
    fetchTakeover,
  ]);

  const submitFriendlyTrade = useCallback(async () => {
    const volume = parseInt(friendlyVolumeInput, 10);
    if (!Number.isFinite(volume) || volume < 1) {
      setConsoleError('Enter a friendly-trade contracted volume of at least 1.');
      return;
    }
    if (volume > FRIENDLY_TRADE_MAX_VOLUME) {
      setConsoleError(
        `Friendly-trade volume is capped at ${FRIENDLY_TRADE_MAX_VOLUME.toLocaleString()}.`,
      );
      return;
    }
    const allyTeam = allyTeamIdInput.trim() || null;
    const allyFaction = allyFactionInput.trim() || null;
    if (!allyTeam && !allyFaction) {
      setConsoleError('Bind a friendly ally team id and/or faction before posting the contract.');
      return;
    }
    setConsoleSuccess(null);
    const result = await runAction(
      'friendly-trade',
      () =>
        activateFriendlyTrade(stationId, {
          contracted_volume: volume,
          ally_team_id: allyTeam,
          ally_faction: allyFaction,
        }),
      setConsoleError,
      'Friendly-trade contract failed.',
    );
    if (result !== null) {
      setConsoleSuccess(`Friendly-trade contract for ${volume.toLocaleString()} volume posted.`);
      setFriendlyVolumeInput('');
      await fetchTakeover();
    }
  }, [
    friendlyVolumeInput,
    allyTeamIdInput,
    allyFactionInput,
    runAction,
    activateFriendlyTrade,
    stationId,
    fetchTakeover,
  ]);

  const ownerPctFromDefense = useMemo(() => {
    const defense = Math.min(FEE_DEFENSE_MAX, Math.max(FEE_DEFENSE_MIN, defensePctInput));
    return Math.round((1 - FEE_OPERATING - defense) * 1000) / 1000;
  }, [defensePctInput]);

  const submitFeeDistribution = useCallback(async () => {
    const defense = Math.round(defensePctInput * 1000) / 1000;
    const owner = Math.round(ownerPctFromDefense * 1000) / 1000;
    if (defense < FEE_DEFENSE_MIN || defense > FEE_DEFENSE_MAX) {
      setConsoleError(`Defense share must be between ${FEE_DEFENSE_MIN * 100}% and ${FEE_DEFENSE_MAX * 100}%.`);
      return;
    }
    if (owner < FEE_OWNER_MIN || owner > FEE_OWNER_MAX) {
      setConsoleError(`Owner share must be between ${FEE_OWNER_MIN * 100}% and ${FEE_OWNER_MAX * 100}%.`);
      return;
    }
    const sum = Math.round((defense + owner + FEE_OPERATING) * 1000) / 1000;
    if (sum !== 1) {
      setConsoleError('Defense + owner + operating (30%) must sum to 100%.');
      return;
    }
    setConsoleSuccess(null);
    const result = await runAction(
      'fee-distribution',
      () => setFeeDistribution(stationId, defense, owner),
      setConsoleError,
      'Fee-distribution rebalance failed.',
    );
    if (result !== null) {
      setConsoleSuccess(
        `Fee split updated — defense ${(defense * 100).toFixed(0)}% / owner ${(owner * 100).toFixed(0)}% / operating 30%.`,
      );
      await fetchOwner();
    }
  }, [defensePctInput, ownerPctFromDefense, runAction, setFeeDistribution, stationId, fetchOwner]);

  const launchCampaign = useCallback(async () => {
    setWarSuccess(null);
    const result = await runAction('launch', () => launchTakeover(stationId), setWarError, 'The campaign filing was rejected.');
    if (result !== null) {
      setWarSuccess('Campaign filed. Outtrade the house — hold the majority of this station’s volume, month after month.');
      await fetchTakeover();
    }
  }, [runAction, launchTakeover, stationId, fetchTakeover]);

  const runMilitaryAction = useCallback(
    async (action: 'declare' | 'siege' | 'occupy') => {
      setWarSuccess(null);
      const successLabels: Record<typeof action, string> = {
        declare:
          'Declaration filed — 24-hour galaxy-wide notice before the siege may begin.',
        siege: 'Siege round resolved — check defenders remaining before occupying.',
        occupy:
          'Occupation complete — deed transferred; prior treasury forfeited as war-tax; severe reputation cost applies.',
      };
      const result = await runAction(
        `military-${action}`,
        () => militaryTakeover(stationId, action),
        setWarError,
        'Military takeover action rejected.',
      );
      if (result !== null) {
        setWarSuccess(successLabels[action]);
        if (action === 'occupy') {
          await fetchAll();
        } else {
          await fetchTakeover();
        }
      }
    },
    [runAction, militaryTakeover, stationId, fetchTakeover, fetchAll],
  );

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
                  <span>{formatCredits(listing.listPrice)}</span>
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
                  {myOffer.bidAmount !== null ? formatCredits(myOffer.bidAmount) : 'filed'}
                </span>
                <span className="po-my-offer-status">
                  {myOffer.status ? myOffer.status.replace(/_/g, ' ').toUpperCase() : 'IN ESCROW'}
                </span>
              </div>
            ) : (
              <div className="po-bid-row">
                <label htmlFor="po-bid-input">Sealed bid (₡)</label>
                <input
                  id="po-bid-input"
                  type="number"
                  min={listing.listPrice ?? 1}
                  max={BID_CEILING}
                  value={bidInput}
                  onChange={e => setBidInput(e.target.value)}
                  placeholder={listing.listPrice !== null ? `${formatCredits(listing.listPrice)} minimum` : 'Bid amount'}
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

        {/* Revenue levers — owner only (LEG-366); no information-sales */}
        <div className="po-section" data-testid="po-revenue-levers">
          <h3 className="po-section-title">💹 Revenue Levers</h3>
          <p className="section-description">
            Price adjustment (±10%), docking fee (50–500 cr, toggle), service charge (0.8×–2.0×),
            and storage rental (1,000–10,000 cr/day). Bounds are enforced by the Port Authority.
          </p>
          <div className="po-defense-grid">
            <label className="po-defense-field">
              <span>Price lever ({(priceLeverPct * 100).toFixed(0)}%)</span>
              <input
                type="range"
                min={PRICE_LEVER_MIN}
                max={PRICE_LEVER_MAX}
                step={0.01}
                value={priceLeverPct}
                disabled={Boolean(busyAction)}
                aria-label="Price adjustment lever"
                data-testid="po-price-lever-pct"
                onChange={(e) => setPriceLeverPct(parseFloat(e.target.value))}
              />
              <button
                className="action-button primary"
                type="button"
                data-testid="po-price-lever-submit"
                onClick={() => void submitPriceLever()}
                disabled={Boolean(busyAction)}
              >
                {busyAction === 'price-lever' ? 'Posting...' : 'Post Price Lever'}
              </button>
            </label>
            <label className="po-defense-field">
              <span>Docking fee (cr)</span>
              <input
                type="number"
                min={DOCKING_FEE_MIN}
                max={DOCKING_FEE_MAX}
                step={1}
                value={dockingFeeAmount}
                disabled={Boolean(busyAction)}
                aria-label="Docking fee amount"
                data-testid="po-docking-fee-amount"
                onChange={(e) => setDockingFeeAmount(parseInt(e.target.value, 10) || DOCKING_FEE_MIN)}
              />
              <label className="po-defense-field">
                <span>
                  <input
                    type="checkbox"
                    checked={dockingFeeEnabled}
                    disabled={Boolean(busyAction)}
                    aria-label="Docking fee enabled"
                    data-testid="po-docking-fee-enabled"
                    onChange={(e) => setDockingFeeEnabled(e.target.checked)}
                  />{' '}
                  Enabled
                </span>
              </label>
              <button
                className="action-button primary"
                type="button"
                data-testid="po-docking-fee-submit"
                onClick={() => void submitDockingFee()}
                disabled={Boolean(busyAction)}
              >
                {busyAction === 'docking-fee' ? 'Posting...' : 'Post Docking Fee'}
              </button>
            </label>
            <label className="po-defense-field">
              <span>Service charge multiplier</span>
              <input
                type="number"
                min={SERVICE_CHARGE_MIN}
                max={SERVICE_CHARGE_MAX}
                step={0.1}
                value={serviceChargeMult}
                disabled={Boolean(busyAction)}
                aria-label="Service charge multiplier"
                data-testid="po-service-charge-mult"
                onChange={(e) => setServiceChargeMult(parseFloat(e.target.value) || 1)}
              />
              <button
                className="action-button primary"
                type="button"
                data-testid="po-service-charge-submit"
                onClick={() => void submitServiceCharge()}
                disabled={Boolean(busyAction)}
              >
                {busyAction === 'service-charge' ? 'Posting...' : 'Post Service Charge'}
              </button>
            </label>
            <label className="po-defense-field">
              <span>Storage rental (cr/day)</span>
              <input
                type="number"
                min={STORAGE_RENTAL_MIN}
                max={STORAGE_RENTAL_MAX}
                step={100}
                value={storageRentalPerDay}
                disabled={Boolean(busyAction)}
                aria-label="Storage rental per day"
                data-testid="po-storage-rental-per-day"
                onChange={(e) =>
                  setStorageRentalPerDay(parseInt(e.target.value, 10) || STORAGE_RENTAL_MIN)
                }
              />
              <button
                className="action-button primary"
                type="button"
                data-testid="po-storage-rental-submit"
                onClick={() => void submitStorageRental()}
                disabled={Boolean(busyAction)}
              >
                {busyAction === 'storage-rental' ? 'Posting...' : 'Post Storage Rental'}
              </button>
            </label>
          </div>
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
                aria-label={`Station vault: ${formatCredits(vault)}`}
                title={`${gaugePct.toFixed(1)}% of the ${formatCredits(gaugeMax)} gauge scale`}
              >
                <div className="po-vault-fill" style={{ width: `${gaugePct}%` }} />
                <div className="po-vault-segments" aria-hidden="true" />
              </div>
              <div className="po-vault-readout">
                <span className="po-vault-amount">{formatCredits(vault)}</span>
                <span className="po-vault-capacity">in the vault</span>
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

        {/* Defense policy levers — owner only (renderOwnerConsole is already isMine-gated) */}
        <div className="po-section" data-testid="po-defense-policy">
          <h3 className="po-section-title">🛡️ Defense Policy</h3>
          <p className="section-description">
            Docking access, hostility list, punitive fees, defender posture, and drone allocation
            for {stationName}. Patrol radius remains deferred — not configurable here.
          </p>
          {defenseLoading && (
            <div className="catalog-loading">Loading defense policy...</div>
          )}
          {defenseError && (
            <div className="genesis-error-message">
              <span className="error-icon">❌</span>
              {defenseError}
              <button className="action-button" type="button" onClick={() => void fetchDefense()}>
                Retry
              </button>
            </div>
          )}
          <div className="po-defense-grid">
            <label className="po-defense-field">
              <span>Docking access</span>
              <select
                value={defenseForm.dockingAccess}
                disabled={Boolean(busyAction)}
                aria-label="Docking access mode"
                onChange={(e) =>
                  setDefenseForm((prev) => ({
                    ...prev,
                    dockingAccess: e.target.value as DockingAccess,
                  }))
                }
              >
                {DOCKING_ACCESS_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="po-defense-field">
              <span>Defender posture</span>
              <select
                value={
                  POSTURE_OPTIONS.includes(defenseForm.defenderPosture as (typeof POSTURE_OPTIONS)[number])
                    ? defenseForm.defenderPosture
                    : 'passive'
                }
                disabled={Boolean(busyAction)}
                aria-label="Defender posture"
                onChange={(e) =>
                  setDefenseForm((prev) => ({ ...prev, defenderPosture: e.target.value }))
                }
              >
                {POSTURE_OPTIONS.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </label>
            <label className="po-defense-field">
              <span>Punitive fee multiplier (1.0–5.0×)</span>
              <input
                type="number"
                min={1}
                max={5}
                step={0.1}
                value={defenseForm.punitiveFeeMult}
                disabled={Boolean(busyAction)}
                aria-label="Punitive fee multiplier"
                onChange={(e) =>
                  setDefenseForm((prev) => ({
                    ...prev,
                    punitiveFeeMult: parseFloat(e.target.value) || 1,
                  }))
                }
              />
            </label>
            <label className="po-defense-field">
              <span>Drone allocation %</span>
              <input
                type="number"
                min={0}
                max={100}
                step={1}
                value={defenseForm.droneAllocationPct}
                disabled={Boolean(busyAction)}
                aria-label="Drone allocation percentage"
                onChange={(e) =>
                  setDefenseForm((prev) => ({
                    ...prev,
                    droneAllocationPct: parseInt(e.target.value, 10) || 0,
                  }))
                }
              />
            </label>
            <label className="po-defense-field po-defense-field-wide">
              <span>
                Hostility list (player ids — allow-list under whitelist mode, deny-list under hostile_deny)
              </span>
              <textarea
                rows={3}
                value={defenseForm.hostilityListText}
                disabled={Boolean(busyAction)}
                aria-label="Hostility list player ids"
                placeholder="One player id per line"
                onChange={(e) =>
                  setDefenseForm((prev) => ({ ...prev, hostilityListText: e.target.value }))
                }
              />
            </label>
          </div>
          <button
            className="action-button primary"
            type="button"
            onClick={() => void submitDefensePolicy()}
            disabled={Boolean(busyAction)}
          >
            {busyAction === 'defense' ? 'Posting...' : 'Post Defense Policy'}
          </button>
        </div>

        <StationSecurityMonitoringPane stationId={stationId} isOwner={isMine} />

        {/* Economic takeover defense — owner only (LEG-INI-35) */}
        <div className="po-section" data-testid="po-econ-defense">
          <h3 className="po-section-title">📉 Economic Takeover Defense</h3>
          <p className="section-description">
            During an active counter window: temporarily halve the tariff, absorb volume with a
            credit-funded counter-trade (1 cr per unit, max {COUNTER_TRADE_MAX_ABSORB.toLocaleString()}),
            or bind a friendly-trade contract (max {FRIENDLY_TRADE_MAX_VOLUME.toLocaleString()}).
            Magnitudes are server-enforced — this console does not invent alternate ceilings.
          </p>
          <div className="po-defense-grid">
            <div className="po-defense-field po-defense-field-wide">
              <span>Tariff cut (halves current rate for the counter window)</span>
              <button
                className="action-button"
                type="button"
                data-testid="po-tariff-cut"
                onClick={() => void submitTariffCut()}
                disabled={Boolean(busyAction)}
              >
                {busyAction === 'tariff-cut' ? 'Cutting...' : 'Activate Tariff Cut'}
              </button>
            </div>
            <label className="po-defense-field">
              <span>Counter-trade absorb volume (1–{COUNTER_TRADE_MAX_ABSORB.toLocaleString()})</span>
              <input
                type="number"
                min={1}
                max={COUNTER_TRADE_MAX_ABSORB}
                value={counterVolumeInput}
                disabled={Boolean(busyAction)}
                aria-label="Counter-trade absorb volume"
                data-testid="po-counter-trade-volume"
                onChange={(e) => setCounterVolumeInput(e.target.value)}
              />
            </label>
            <div className="po-defense-field">
              <span>Cost ≈ volume × {COUNTER_TRADE_CREDITS_PER_VOLUME} cr</span>
              <button
                className="action-button primary"
                type="button"
                data-testid="po-counter-trade"
                onClick={() => void submitCounterTrade()}
                disabled={Boolean(busyAction) || !counterVolumeInput}
              >
                {busyAction === 'counter-trade' ? 'Absorbing...' : 'Post Counter-Trade'}
              </button>
            </div>
            <label className="po-defense-field">
              <span>Friendly-trade contracted volume</span>
              <input
                type="number"
                min={1}
                max={FRIENDLY_TRADE_MAX_VOLUME}
                value={friendlyVolumeInput}
                disabled={Boolean(busyAction)}
                aria-label="Friendly-trade contracted volume"
                data-testid="po-friendly-trade-volume"
                onChange={(e) => setFriendlyVolumeInput(e.target.value)}
              />
            </label>
            <label className="po-defense-field">
              <span>Ally team id (optional if faction set)</span>
              <input
                type="text"
                value={allyTeamIdInput}
                disabled={Boolean(busyAction)}
                aria-label="Ally team id"
                data-testid="po-friendly-ally-team"
                onChange={(e) => setAllyTeamIdInput(e.target.value)}
              />
            </label>
            <label className="po-defense-field">
              <span>Ally faction (optional if team set)</span>
              <input
                type="text"
                value={allyFactionInput}
                disabled={Boolean(busyAction)}
                aria-label="Ally faction"
                data-testid="po-friendly-ally-faction"
                onChange={(e) => setAllyFactionInput(e.target.value)}
              />
            </label>
            <div className="po-defense-field po-defense-field-wide">
              <button
                className="action-button primary"
                type="button"
                data-testid="po-friendly-trade"
                onClick={() => void submitFriendlyTrade()}
                disabled={Boolean(busyAction) || !friendlyVolumeInput}
              >
                {busyAction === 'friendly-trade' ? 'Binding...' : 'Post Friendly-Trade Contract'}
              </button>
            </div>
          </div>
        </div>

        {/* Fee distribution rebalance — owner only (LEG-INI-36) */}
        <div className="po-section" data-testid="po-fee-distribution">
          <h3 className="po-section-title">⚖️ Fee Distribution</h3>
          <p className="section-description">
            Rebalance defense vs owner buckets. Operating is fixed at 30%. Defense must stay
            between 30% and 60%; owner between 10% and 50%; the three shares sum to 100%.
          </p>
          <div className="po-defense-grid">
            <label className="po-defense-field po-defense-field-wide">
              <span>
                Defense share: {(defensePctInput * 100).toFixed(0)}% (owner{' '}
                {(ownerPctFromDefense * 100).toFixed(0)}% · operating 30%)
              </span>
              <input
                type="range"
                min={FEE_DEFENSE_MIN}
                max={FEE_DEFENSE_MAX}
                step={0.01}
                value={defensePctInput}
                disabled={Boolean(busyAction)}
                aria-label="Defense fee percentage"
                data-testid="po-fee-defense-pct"
                onChange={(e) => setDefensePctInput(parseFloat(e.target.value))}
              />
            </label>
          </div>
          {defensePctInput < FEE_UNDERFUND_WARN && (
            <div
              className="genesis-error-message"
              role="status"
              data-testid="po-fee-underfund-warn"
            >
              Defense underfunding: defense share is below 35%. Sustained deficits can auto-downgrade
              the station security tier (canon cascade).
            </div>
          )}
          <button
            className="action-button primary"
            type="button"
            data-testid="po-fee-submit"
            onClick={() => void submitFeeDistribution()}
            disabled={Boolean(busyAction)}
          >
            {busyAction === 'fee-distribution' ? 'Rebalancing...' : 'Post Fee Distribution'}
          </button>
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
                    <span>{formatCredits(myStation.revenue30d)}</span>
                  </div>
                )}
                {myStation.revenue90d !== null && (
                  <div className="po-term-row">
                    <span>Trailing 90 days</span>
                    <span>{formatCredits(myStation.revenue90d)}</span>
                  </div>
                )}
                {myStation.acquisitionCost !== null && (
                  <div className="po-term-row">
                    <span>Acquisition cost</span>
                    <span>{formatCredits(myStation.acquisitionCost)}</span>
                  </div>
                )}
              </div>
              {myStation.monthly.length > 0 && (
                <div className="po-ledger-months">
                  {myStation.monthly.map(m => (
                    <div key={m.label} className="po-term-row">
                      <span>{m.label}</span>
                      <span>{formatCredits(m.amount)}</span>
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
                  title={`${m.sharePct.toFixed(1)}% of station volume${m.totalVolume !== null ? ` (${formatCredits(m.totalVolume)} total)` : ''}${m.hostile === true ? ' — hostile pricing' : ''}${m.qualifies ? ' — qualifying month' : ''}`}
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
                  <span>{formatCredits(takeover.forcedSalePrice)}</span>
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

        {/* Military takeover — challenger only on owned foreign stations (LEG-368).
            GS enforces notice window, Military Contract immunity, drones, region
            rules; UI surfaces returned detail via warError. */}
        {!isMine && listing?.ownerId && (
          <div className="po-section" data-testid="po-military-takeover">
            <h3 className="po-section-title">🎖️ Military Takeover</h3>
            <p className="section-description">
              Hostile path: declare intent (24-hour galaxy-wide notice), siege defenders
              with attack drones, then occupy. Severe reputation cost; prior treasury is
              forfeited to the controlling faction as war-tax — not paid to you. Stations
              with a Military Contract are immune. Restricted regions reject at the server.
            </p>
            <div className="po-counter-actions">
              <button
                className="action-button primary"
                type="button"
                data-testid="po-military-declare"
                onClick={() => void runMilitaryAction('declare')}
                disabled={Boolean(busyAction)}
              >
                {busyAction === 'military-declare' ? 'Filing...' : '📜 Declare Intent'}
              </button>
              <button
                className="action-button"
                type="button"
                data-testid="po-military-siege"
                onClick={() => void runMilitaryAction('siege')}
                disabled={Boolean(busyAction)}
              >
                {busyAction === 'military-siege' ? 'Engaging...' : '⚔️ Siege Round'}
              </button>
              <button
                className="action-button"
                type="button"
                data-testid="po-military-occupy"
                onClick={() => void runMilitaryAction('occupy')}
                disabled={Boolean(busyAction)}
              >
                {busyAction === 'military-occupy' ? 'Occupying...' : '🏳️ Occupy'}
              </button>
            </div>
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

        <DeckPageTabs
          pages={[
            { id: 'registry', label: '📋 Registry' },
            { id: 'owner', label: '🏛️ Owner Console', available: isMine },
            { id: 'warroom', label: '⚔️ War Room' }
          ]}
          activeId={activeTab}
          onSelect={(id) => setActiveTab(id as PortOfficeTab)}
          ariaLabel="Port office view"
          accent="#00d9ff"
          idBase="po"
          className="po-tabs"
        />

        <div
          role="tabpanel"
          id={`po-panel-${activeTab}`}
          aria-labelledby={`po-tab-${activeTab}`}
        >
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
    </div>
  );
};

export default PortOfficeVenue;
