import React, { useState, useCallback, useEffect, useMemo } from 'react';
import { useGame } from '../../contexts/GameContext';
import { formatCredits } from '../../utils/formatters';
import { useResourceCatalog } from '../../hooks/useResourceCatalog';
import { resourceIcon } from '../../services/resourceCatalog';
import DeckPageTabs from '../cockpit/DeckPageTabs';
import './construction-venue.css';

// Use same API URL logic as GameContext for Codespaces compatibility
const getApiBaseUrl = () => {
  if (import.meta.env.VITE_API_URL) {
    return import.meta.env.VITE_API_URL;
  }
  // Use current origin to leverage Vite proxy (works in Codespaces)
  return window.location.origin;
};

// --- Contract types (GET /api/v1/construction/*) ---

interface ResourceBundle {
  ore?: number;
  equipment?: number;
  organics?: number;
  [key: string]: number | undefined;
}

interface ConstructionQuote {
  ship_type: string;
  total_cost: number;
  deposit: number;
  build_days: number;
  resources_required: ResourceBundle;
  requires_tier_a: boolean;
  uses_specialized_slip: boolean;
  // Server-computed availability (feature-detected; tier gating is the fallback)
  available?: boolean;
  unavailable_reason?: string | null;
  daily_rent?: number;
  milestones?: Record<string, number>;
}

interface SlipPool {
  capacity: number;
  in_use: number;
}

interface QuotesMeta {
  slips?: { standard?: SlipPool; specialized?: SlipPool };
  queue_length?: number;
}

interface ReservationRent {
  daily_rent?: number;
  paid_until?: string | null;
  overdue_canonical_days?: number;
  owed?: number;
  forfeit_after_days?: number;
}

interface NextCheckpoint {
  phase?: string;
  shortfall?: Record<string, number>;
}

interface ConstructionReservation {
  id: string;
  station_id?: string;
  state: string;
  ship_type: string;
  ship_name?: string | null;
  total_cost?: number;
  credits_paid?: number;
  // Server-computed advisory (mirrors cancel()'s authoritative cancel_refund() at commit time).
  estimated_refund?: number;
  queue_bonus_credit: number;
  paused?: boolean;
  needs?: string[];
  queue_position?: number | null;
  queue_length?: number;
  phase_progress_percent?: number;
  overall_progress_percent?: number;
  phase_deadline?: string | null;
  hold_expires_at?: string | null;
  claim_expires_at?: string | null;
  rent?: ReservationRent;
  milestones?: unknown;
  resources_required?: ResourceBundle;
  resources_delivered?: ResourceBundle;
  next_checkpoint?: NextCheckpoint;
  // Alternate field names from earlier payload revisions — feature-detected
  progress_pct?: number;
  phase?: string | null;
  rent_owed?: number;
  rent_per_day?: number;
  checkpoint_shortfall?: unknown;
}

interface MilestoneEntry {
  name: string;
  amount?: number;
  paid: boolean;
}

interface ShortfallEntry {
  resource: string;
  amount: number;
  label?: string;
}

interface ConstructionVenueProps {
  stationId: string;
  stationName: string;
  tier: 'A' | 'B';
  credits: number;
  onCreditsDelta: (delta: number) => void;
  onCreditsSet: (value: number) => void;
  onBack: () => void;
}

// --- Build phases (frame → systems → outfitting → final) ---

const PHASES = ['frame', 'systems', 'outfitting', 'final'] as const;
type BuildPhase = typeof PHASES[number];

const PHASE_LABELS: Record<BuildPhase, string> = {
  frame: 'Keel & Frame',
  systems: 'Systems Integration',
  outfitting: 'Outfitting',
  final: 'Final Assembly'
};

// Ship construction's resource_cost is a fixed 3-key contract (ResourceBundle),
// not an open catalog — so the SET stays a literal array. Icon/label for each
// key now come from the shared resource catalog (WO-ARCH-RES-3-FE-CATALOG,
// see resourceIcon() below + useResourceCatalog().getLabel in the component)
// instead of a locally-duplicated dict.
const RESOURCES = ['ore', 'equipment', 'organics'] as const;
type ConstructionResource = typeof RESOURCES[number];

// Construction/shipyard context uses a bolt glyph for equipment (vs. the gear
// glyph the catalog default gives planetary equipment production elsewhere)
// — preserved as a local override so this UI's look doesn't shift under the
// catalog swap; every other key defers to the shared default.
const iconFor = (resource: ConstructionResource): string =>
  resource === 'equipment' ? '🔩' : resourceIcon(resource);

// Reservation state buckets (server is the source of truth; we just classify).
// The build-phase states ARE the phases: frame_assembly → systems_integration
// → outfitting → final_assembly.
const READY_STATES = new Set(['ready', 'ready_to_claim', 'complete', 'completed']);
const TERMINAL_STATES = new Set(['claimed', 'delivered', 'cancelled', 'canceled', 'expired', 'forfeited', 'repossessed']);
// The four states that ARE build phases
const BUILD_PHASE_STATES = new Set(['frame_assembly', 'systems_integration', 'outfitting', 'final_assembly']);
// Deliveries open once the slip is secured and close when outfitting ends
const DELIVERY_STATES = new Set(['deposit_collected', 'frame_assembly', 'systems_integration', 'outfitting']);
// Rent accrues while the build occupies a slip
const RENT_STATES = new Set(['deposit_collected', 'frame_assembly', 'systems_integration', 'outfitting', 'final_assembly']);

const normalizeState = (state?: string | null): string =>
  (state || '').toLowerCase().replace(/[\s-]+/g, '_');

const STATE_DISPLAY: Record<string, { label: string; cls: string }> = {
  queued: { label: 'QUEUED', cls: 'hold' },
  hold_active: { label: 'SLIP ON HOLD', cls: 'hold' },
  deposit_collected: { label: 'AWAITING START', cls: 'hold' },
  reserved: { label: 'RESERVED', cls: 'hold' },
  hold: { label: 'ON HOLD', cls: 'hold' },
  pending: { label: 'PENDING', cls: 'hold' },
  frame_assembly: { label: 'BUILDING', cls: 'building' },
  systems_integration: { label: 'BUILDING', cls: 'building' },
  outfitting: { label: 'BUILDING', cls: 'building' },
  final_assembly: { label: 'BUILDING', cls: 'building' },
  building: { label: 'BUILDING', cls: 'building' },
  in_progress: { label: 'BUILDING', cls: 'building' },
  active: { label: 'BUILDING', cls: 'building' },
  paused: { label: 'PAUSED', cls: 'paused' },
  ready: { label: 'READY TO CLAIM', cls: 'ready' },
  ready_to_claim: { label: 'READY TO CLAIM', cls: 'ready' },
  complete: { label: 'READY TO CLAIM', cls: 'ready' },
  completed: { label: 'READY TO CLAIM', cls: 'ready' },
  claimed: { label: 'CLAIMED', cls: 'terminal' },
  delivered: { label: 'CLAIMED', cls: 'terminal' },
  cancelled: { label: 'CANCELLED', cls: 'terminal' },
  canceled: { label: 'CANCELLED', cls: 'terminal' },
  expired: { label: 'EXPIRED', cls: 'terminal' },
  forfeited: { label: 'FORFEITED', cls: 'terminal' },
  repossessed: { label: 'REPOSSESSED', cls: 'terminal' }
};

// "WARP_JUMPER" → "Warp Jumper"
const prettyShipType = (shipType?: string | null): string =>
  (shipType || 'Unknown Hull')
    .toLowerCase()
    .split(/[\s_-]+/)
    .filter(Boolean)
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');

const normalizePhase = (phase?: string | null): BuildPhase => {
  const p = (phase || '').toLowerCase();
  // 'frame' first: 'frame_assembly' must not fall into the final branch
  if (p.includes('frame')) return 'frame';
  if (p.includes('system')) return 'systems';
  if (p.includes('outfit')) return 'outfitting';
  if (p.includes('final')) return 'final';
  return 'frame';
};

// Countdown formatting against a ticking clock
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

// Pull a readable error from a {message|detail} 400 body
const readError = async (response: Response, fallback: string): Promise<string> => {
  const data: unknown = await response.json().catch(() => null);
  if (data && typeof data === 'object') {
    const body = data as Record<string, unknown>;
    const raw = body.message ?? body.detail;
    if (typeof raw === 'string' && raw) return raw;
  }
  return fallback;
};

// Milestones arrive in an unspecified shape — normalize defensively
const normalizeMilestones = (raw: unknown): MilestoneEntry[] => {
  if (!raw) return [];

  const fromObject = (name: string, value: unknown): MilestoneEntry => {
    if (typeof value === 'number') return { name, amount: value, paid: false };
    if (typeof value === 'boolean') return { name, paid: value };
    if (value && typeof value === 'object') {
      const o = value as Record<string, unknown>;
      const amount = typeof o.amount === 'number' ? o.amount
        : typeof o.cost === 'number' ? o.cost
        : typeof o.credits === 'number' ? o.credits
        : undefined;
      const paid = o.paid === true || o.is_paid === true || o.state === 'paid' || o.status === 'paid';
      return { name, amount, paid };
    }
    return { name, paid: false };
  };

  if (Array.isArray(raw)) {
    return raw.map((entry): MilestoneEntry => {
      if (typeof entry === 'string') return { name: entry, paid: false };
      if (entry && typeof entry === 'object') {
        const o = entry as Record<string, unknown>;
        const name = String(o.milestone ?? o.name ?? o.id ?? o.phase ?? 'milestone');
        return fromObject(name, entry);
      }
      return { name: String(entry), paid: false };
    });
  }

  if (typeof raw === 'object') {
    return Object.entries(raw as Record<string, unknown>).map(([name, value]) => fromObject(name, value));
  }

  return [];
};

// checkpoint_shortfall — normalize to per-resource amounts
const normalizeShortfall = (raw: unknown): ShortfallEntry[] => {
  if (!raw) return [];

  if (Array.isArray(raw)) {
    return raw
      .map((entry): ShortfallEntry | null => {
        if (!entry || typeof entry !== 'object') return null;
        const o = entry as Record<string, unknown>;
        const resource = String(o.resource ?? o.commodity ?? o.name ?? '');
        const amount = typeof o.amount === 'number' ? o.amount
          : typeof o.needed === 'number' ? o.needed
          : typeof o.short === 'number' ? o.short
          : 0;
        const label = typeof o.milestone === 'string' ? o.milestone
          : typeof o.phase === 'string' ? o.phase
          : undefined;
        return resource && amount > 0 ? { resource, amount, label } : null;
      })
      .filter((e): e is ShortfallEntry => e !== null);
  }

  if (typeof raw === 'object') {
    return Object.entries(raw as Record<string, unknown>)
      .map(([resource, value]): ShortfallEntry | null => {
        if (typeof value === 'number' && value > 0) return { resource, amount: value };
        if (value && typeof value === 'object') {
          const o = value as Record<string, unknown>;
          const amount = typeof o.amount === 'number' ? o.amount : typeof o.needed === 'number' ? o.needed : 0;
          return amount > 0 ? { resource, amount } : null;
        }
        return null;
      })
      .filter((e): e is ShortfallEntry => e !== null);
  }

  return [];
};

// Feature-detect new credit totals in action responses
const creditsFromResponse = (result: unknown): number | null => {
  if (!result || typeof result !== 'object') return null;
  const body = result as Record<string, unknown>;
  const value = body.credits_remaining ?? body.new_credits ?? body.remaining_credits ?? body.credits;
  return typeof value === 'number' ? value : null;
};

// --- BuildLine: four-phase ship-under-construction visualization ---
// Pure-SVG sister of CitadelStructure: all four strata always render and
// CSS state classes decide how each appears (lit / current / paused / ghost).

type StratumState = 'lit' | 'current' | 'paused' | 'ghost';

const stratumState = (
  stratumIdx: number,
  phaseIdx: number,
  allLit: boolean,
  isPaused: boolean
): StratumState => {
  if (allLit) return 'lit';
  if (phaseIdx < 0) return 'ghost'; // queued / on hold — no keel laid yet
  if (stratumIdx < phaseIdx) return 'lit';
  if (stratumIdx === phaseIdx) return isPaused ? 'paused' : 'current';
  return 'ghost';
};

interface BuildLineProps {
  /** Index into PHASES; -1 when the build has not started (queued / hold). */
  phaseIdx: number;
  allLit: boolean;
  isPaused: boolean;
}

const BuildLine: React.FC<BuildLineProps> = ({ phaseIdx, allLit, isPaused }) => {
  const cls = (idx: number): string => `bl-stratum state-${stratumState(idx, phaseIdx, allLit, isPaused)}`;

  return (
    <div className="build-line">
      <svg
        viewBox="0 0 360 130"
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label={
          allLit
            ? 'Build line: all four phases complete'
            : phaseIdx < 0
              ? 'Build line: construction not yet started'
              : `Build line: phase ${phaseIdx + 1} of 4 — ${PHASE_LABELS[PHASES[phaseIdx]]}${isPaused ? ' (paused)' : ''}`
        }
        className="build-line-svg"
      >
        <defs>
          <linearGradient id="bl-hull" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#4a9eff" stopOpacity="0.22" />
            <stop offset="100%" stopColor="#4a9eff" stopOpacity="0.04" />
          </linearGradient>
          <linearGradient id="bl-flame" x1="1" y1="0" x2="0" y2="0">
            <stop offset="0%" stopColor="#00ffcc" stopOpacity="0.55" />
            <stop offset="100%" stopColor="#00ffcc" stopOpacity="0" />
          </linearGradient>
        </defs>

        {/* Slip cradle — always visible */}
        <line className="bl-cradle" x1="20" y1="116" x2="340" y2="116" />
        <line className="bl-cradle" x1="110" y1="116" x2="110" y2="108" />
        <line className="bl-cradle" x1="250" y1="116" x2="250" y2="108" />

        {/* ===== Phase 1: Keel & Frame — keel, stern post, spine, ribs ===== */}
        <g className={`${cls(0)} stratum-frame`} data-phase="frame">
          <line className="bl-keel" x1="64" y1="104" x2="300" y2="104" />
          <line className="bl-spar" x1="64" y1="104" x2="72" y2="64" />
          <line className="bl-spar" x1="72" y1="64" x2="326" y2="72" />
          <path className="bl-spar" d="M 300 104 Q 322 92 326 72" fill="none" />
          <line className="bl-rib" x1="104" y1="104" x2="104" y2="65" />
          <line className="bl-rib" x1="144" y1="104" x2="144" y2="66" />
          <line className="bl-rib" x1="184" y1="104" x2="184" y2="67" />
          <line className="bl-rib" x1="224" y1="104" x2="224" y2="68" />
          <line className="bl-rib" x1="264" y1="104" x2="264" y2="69" />
        </g>

        {/* ===== Phase 2: Systems Integration — engine block, reactor, conduits ===== */}
        <g className={`${cls(1)} stratum-systems`} data-phase="systems">
          <rect className="bl-block" x="72" y="78" width="26" height="20" />
          <rect className="bl-block" x="56" y="80" width="12" height="7" />
          <rect className="bl-block" x="56" y="91" width="12" height="7" />
          <circle className="bl-block bl-reactor" cx="162" cy="86" r="9" />
          <circle className="bl-core" cx="162" cy="86" r="3.2" />
          <line className="bl-conduit" x1="98" y1="86" x2="153" y2="86" />
          <line className="bl-conduit" x1="171" y1="87" x2="296" y2="90" />
        </g>

        {/* ===== Phase 3: Outfitting — hull plating + viewport strip ===== */}
        <g className={`${cls(2)} stratum-outfitting`} data-phase="outfitting">
          <path
            className="bl-hull"
            d="M 60 104 L 70 62 L 270 58 Q 318 64 330 78 Q 326 96 306 104 Z"
            fill="url(#bl-hull)"
          />
          <line className="bl-seam" x1="118" y1="61" x2="114" y2="104" />
          <line className="bl-seam" x1="206" y1="59" x2="204" y2="104" />
          <line className="bl-window" x1="124" y1="76" x2="262" y2="74" />
        </g>

        {/* ===== Phase 4: Final Assembly — bridge, beacon, drive glow, running lights ===== */}
        <g className={`${cls(3)} stratum-final`} data-phase="final">
          <path className="bl-dome" d="M 150 60 Q 166 44 182 60 Z" fill="url(#bl-hull)" />
          <line className="bl-mast" x1="166" y1="48" x2="166" y2="34" />
          <circle className="bl-light bl-beacon" cx="166" cy="31" r="2.4" />
          <polygon className="bl-exhaust" points="56,80 56,98 34,89" fill="url(#bl-flame)" stroke="none" />
          <circle className="bl-light" cx="96" cy="100" r="2" />
          <circle className="bl-light" cx="182" cy="100" r="2" />
          <circle className="bl-light" cx="266" cy="100" r="2" />
        </g>
      </svg>

      <div className="bl-phase-track">
        {PHASES.map((p, idx) => (
          <div key={p} className={`bl-phase-node state-${stratumState(idx, phaseIdx, allLit, isPaused)}`}>
            <span className="bl-phase-dot" aria-hidden="true" />
            <span className="bl-phase-name">{PHASE_LABELS[p]}</span>
          </div>
        ))}
      </div>
    </div>
  );
};

// --- The venue itself ---

const ConstructionVenue: React.FC<ConstructionVenueProps> = ({
  stationId,
  stationName,
  tier,
  credits,
  onCreditsDelta,
  onCreditsSet,
  onBack
}) => {
  const { currentShip, refreshPlayerState, loadShips } = useGame();
  const { getLabel } = useResourceCatalog();

  const getToken = () => localStorage.getItem('accessToken');

  const [activeTab, setActiveTab] = useState<'orders' | 'builds'>('orders');

  // Order book
  const [quotes, setQuotes] = useState<ConstructionQuote[] | null>(null);
  const [quotesMeta, setQuotesMeta] = useState<QuotesMeta | null>(null);
  const [quotesLoading, setQuotesLoading] = useState(false);
  const [quotesError, setQuotesError] = useState<string | null>(null);

  // Reserve flow
  const [confirmQuote, setConfirmQuote] = useState<ConstructionQuote | null>(null);
  const [reserveName, setReserveName] = useState('');
  const [reserving, setReserving] = useState(false);
  const [reserveError, setReserveError] = useState<string | null>(null);
  const [reserveSuccess, setReserveSuccess] = useState<string | null>(null);

  // My builds
  const [reservations, setReservations] = useState<ConstructionReservation[] | null>(null);
  const [reservationsLoading, setReservationsLoading] = useState(false);
  const [reservationsError, setReservationsError] = useState<string | null>(null);

  // Per-reservation action plumbing
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [cardErrors, setCardErrors] = useState<Record<string, string>>({});

  // Inline panels
  const [deliverFor, setDeliverFor] = useState<string | null>(null);
  const [deliverAmounts, setDeliverAmounts] = useState<Record<string, number>>({});
  const [rentFor, setRentFor] = useState<string | null>(null);
  const [rentDays, setRentDays] = useState(7);
  const [cancelFor, setCancelFor] = useState<ConstructionReservation | null>(null);

  // Claim ceremony
  const [ceremony, setCeremony] = useState<{ name: string; shipType: string } | null>(null);

  // QUEUE-UX-OVERLAY-CONSISTENCY REVISE: light focus-hygiene helper (NOT a
  // focus-trap system), same pattern as GameDashboard.tsx's dismissOverlay.
  // A keyboard user focused on the overlay's dismiss button loses focus to
  // nowhere the instant it unmounts -- restore it to document.body (the
  // floor; no stable landmark ref is guaranteed mounted here) right before
  // the dismissing setState, on every path.
  const dismissCeremony = () => {
    document.body.focus();
    setCeremony(null);
  };

  // QUEUE-UX-OVERLAY-CONSISTENCY: auto-dismiss, matching the exact idiom its
  // closest sibling (GameDashboard.tsx's claimCelebration -- "sister of
  // COLONY ESTABLISHED" per the JSX comment below) already uses. 10s to
  // match that sibling's duration exactly (both are full-screen "ceremony"
  // dialogs, not the smaller corner toasts, which run shorter at 6-7s).
  useEffect(() => {
    if (!ceremony) return;
    const timer = setTimeout(dismissCeremony, 10000);
    return () => clearTimeout(timer);
  }, [ceremony]);

  // 1s clock for countdowns
  const [nowMs, setNowMs] = useState(() => Date.now());

  const setCardError = useCallback((reservationId: string, message: string | null) => {
    setCardErrors(prev => {
      const next = { ...prev };
      if (message) {
        next[reservationId] = message;
      } else {
        delete next[reservationId];
      }
      return next;
    });
  }, []);

  // Cargo contents from the current ship — cargo JSONB is {used, capacity, contents:{...}}
  // but older payloads may put goods at the top level; feature-detect both.
  const cargoContents = useMemo((): Record<string, number> => {
    const cargo: unknown = currentShip?.cargo;
    if (!cargo || typeof cargo !== 'object') return {};
    const c = cargo as Record<string, unknown>;
    const source = (c.contents && typeof c.contents === 'object')
      ? c.contents as Record<string, unknown>
      : c;
    const out: Record<string, number> = {};
    for (const [key, value] of Object.entries(source)) {
      const k = key.toLowerCase();
      if (typeof value === 'number' && k !== 'capacity' && k !== 'used') {
        out[k] = value;
      }
    }
    return out;
  }, [currentShip]);

  const cargoAmount = useCallback(
    (resource: string): number => cargoContents[resource.toLowerCase()] ?? 0,
    [cargoContents]
  );

  // --- Fetching ---

  const fetchQuotes = useCallback(async () => {
    const token = getToken();
    if (!token) {
      setQuotesError('Not authenticated. Please log in again.');
      return;
    }
    setQuotesLoading(true);
    setQuotesError(null);
    try {
      const response = await fetch(
        `${getApiBaseUrl()}/api/v1/construction/quotes?station_id=${encodeURIComponent(stationId)}`,
        { headers: { 'Authorization': `Bearer ${token}` } }
      );
      if (!response.ok) {
        setQuotesError(await readError(response, 'Failed to load the ship order book'));
        return;
      }
      const data: unknown = await response.json();
      const list = Array.isArray(data)
        ? data
        : (data && typeof data === 'object' && Array.isArray((data as Record<string, unknown>).quotes))
          ? (data as { quotes: ConstructionQuote[] }).quotes
          : [];
      setQuotes(list as ConstructionQuote[]);
      if (data && typeof data === 'object' && !Array.isArray(data)) {
        const meta = data as Record<string, unknown>;
        setQuotesMeta({
          slips: (meta.slips && typeof meta.slips === 'object')
            ? meta.slips as QuotesMeta['slips']
            : undefined,
          queue_length: typeof meta.queue_length === 'number' ? meta.queue_length : undefined
        });
      }
    } catch (error) {
      console.error('Construction quotes error:', error);
      setQuotesError('Connection error. Please try again.');
    } finally {
      setQuotesLoading(false);
    }
  }, [stationId]);

  const fetchReservations = useCallback(async () => {
    const token = getToken();
    if (!token) {
      setReservationsError('Not authenticated. Please log in again.');
      return;
    }
    setReservationsLoading(true);
    try {
      const response = await fetch(`${getApiBaseUrl()}/api/v1/construction/reservations/mine`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (!response.ok) {
        setReservationsError(await readError(response, 'Failed to load your builds'));
        return;
      }
      const data: unknown = await response.json();
      const list = Array.isArray(data)
        ? data
        : (data && typeof data === 'object' && Array.isArray((data as Record<string, unknown>).reservations))
          ? (data as { reservations: ConstructionReservation[] }).reservations
          : [];
      setReservations(list as ConstructionReservation[]);
      setReservationsError(null);
    } catch (error) {
      console.error('Construction reservations error:', error);
      setReservationsError('Connection error. Please try again.');
    } finally {
      setReservationsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchQuotes();
    fetchReservations();
  }, [fetchQuotes, fetchReservations]);

  // Poll my builds every 30s while the venue is open
  useEffect(() => {
    const interval = setInterval(() => {
      fetchReservations();
    }, 30000);
    return () => clearInterval(interval);
  }, [fetchReservations]);

  // 1s tick for the countdown displays (only while there is something to count)
  const hasCountdowns = useMemo(
    () => (reservations ?? []).some(r =>
      !TERMINAL_STATES.has(normalizeState(r.state)) &&
      Boolean(r.phase_deadline || r.hold_expires_at || r.claim_expires_at)
    ),
    [reservations]
  );

  useEffect(() => {
    if (!hasCountdowns) return;
    const tick = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(tick);
  }, [hasCountdowns]);

  // --- Actions ---

  const reserveShip = useCallback(async () => {
    if (!confirmQuote || reserving) return;
    const token = getToken();
    if (!token) {
      setReserveError('Not authenticated. Please log in again.');
      return;
    }
    if (credits < confirmQuote.deposit) {
      setReserveError(`Insufficient credits for the deposit. Need ${formatCredits(confirmQuote.deposit)}, have ${formatCredits(credits)}`);
      return;
    }

    setReserving(true);
    setReserveError(null);
    setReserveSuccess(null);

    // Immediately deduct the deposit for instant feedback
    onCreditsDelta(-confirmQuote.deposit);

    try {
      const body: { station_id: string; ship_type: string; ship_name?: string } = {
        station_id: stationId,
        ship_type: confirmQuote.ship_type
      };
      const trimmedName = reserveName.trim();
      if (trimmedName) {
        body.ship_name = trimmedName;
      }

      const response = await fetch(`${getApiBaseUrl()}/api/v1/construction/reservations`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify(body)
      });

      if (!response.ok) {
        setReserveError(await readError(response, 'Reservation failed'));
        onCreditsDelta(confirmQuote.deposit);
        return;
      }

      const result: unknown = await response.json().catch(() => ({}));
      const newCredits = creditsFromResponse(result);
      if (newCredits !== null) {
        onCreditsSet(newCredits);
      }

      const displayName = trimmedName || prettyShipType(confirmQuote.ship_type);
      setReserveSuccess(`Slip reserved — the keel for ${displayName} will be laid shortly.`);
      setConfirmQuote(null);
      setReserveName('');
      setActiveTab('builds');
      await fetchReservations();
      refreshPlayerState();
    } catch (error) {
      console.error('Construction reserve error:', error);
      setReserveError('Connection error. Please try again.');
      onCreditsDelta(confirmQuote.deposit);
    } finally {
      setReserving(false);
    }
  }, [confirmQuote, reserving, credits, reserveName, stationId, onCreditsDelta, onCreditsSet, fetchReservations, refreshPlayerState]);

  const reservationAction = useCallback(async (
    reservationId: string,
    action: 'deliver' | 'pay-milestone' | 'pay-rent' | 'claim' | 'cancel',
    body: Record<string, unknown> | null,
    failureMessage: string
  ): Promise<Record<string, unknown> | null> => {
    const token = getToken();
    if (!token) {
      setCardError(reservationId, 'Not authenticated. Please log in again.');
      return null;
    }

    setBusyAction(`${reservationId}:${action}`);
    setCardError(reservationId, null);

    try {
      const response = await fetch(
        `${getApiBaseUrl()}/api/v1/construction/reservations/${encodeURIComponent(reservationId)}/${action}`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
          },
          body: JSON.stringify(body ?? {})
        }
      );

      if (!response.ok) {
        setCardError(reservationId, await readError(response, failureMessage));
        return null;
      }

      const result: unknown = await response.json().catch(() => ({}));
      const newCredits = creditsFromResponse(result);
      if (newCredits !== null) {
        onCreditsSet(newCredits);
      }
      await fetchReservations();
      refreshPlayerState();
      return (result && typeof result === 'object') ? result as Record<string, unknown> : {};
    } catch (error) {
      console.error(`Construction ${action} error:`, error);
      setCardError(reservationId, 'Connection error. Please try again.');
      return null;
    } finally {
      setBusyAction(null);
    }
  }, [setCardError, onCreditsSet, fetchReservations, refreshPlayerState]);

  const submitDelivery = useCallback(async (reservation: ConstructionReservation) => {
    const body: Record<string, number> = {};
    RESOURCES.forEach(resource => {
      const amount = deliverAmounts[resource] ?? 0;
      if (amount > 0) {
        body[resource] = amount;
      }
    });
    if (Object.keys(body).length === 0) return;

    const result = await reservationAction(reservation.id, 'deliver', body, 'Delivery failed');
    if (result) {
      setDeliverFor(null);
      setDeliverAmounts({});
      // Delivery consumes ship cargo — resync the fleet
      await loadShips();
    }
  }, [deliverAmounts, reservationAction, loadShips]);

  const payMilestone = useCallback(async (reservation: ConstructionReservation, milestone: string) => {
    await reservationAction(reservation.id, 'pay-milestone', { milestone }, 'Milestone payment failed');
  }, [reservationAction]);

  const payRent = useCallback(async (reservation: ConstructionReservation) => {
    const days = Math.max(1, Math.min(30, rentDays));
    const result = await reservationAction(reservation.id, 'pay-rent', { days }, 'Rent payment failed');
    if (result) {
      setRentFor(null);
    }
  }, [rentDays, reservationAction]);

  const claimShip = useCallback(async (reservation: ConstructionReservation) => {
    const result = await reservationAction(reservation.id, 'claim', null, 'Claim failed');
    if (result) {
      setCeremony({
        name: reservation.ship_name || prettyShipType(reservation.ship_type),
        shipType: prettyShipType(reservation.ship_type)
      });
      // The new hull joins the fleet
      await loadShips();
    }
  }, [reservationAction, loadShips]);

  const cancelReservation = useCallback(async (reservation: ConstructionReservation) => {
    const result = await reservationAction(reservation.id, 'cancel', null, 'Cancellation failed');
    if (result) {
      setCancelFor(null);
    }
  }, [reservationAction]);

  // --- Render helpers ---

  const renderResourceBundle = (bundle?: ResourceBundle) => (
    <div className="cq-resources">
      {RESOURCES.map(resource => {
        const amount = Number(bundle?.[resource] ?? 0);
        if (amount <= 0) return null;
        return (
          <span key={resource} className="cq-resource-chip" title={getLabel(resource)}>
            {iconFor(resource)} {amount.toLocaleString()}
          </span>
        );
      })}
    </div>
  );

  const renderQuoteCard = (quote: ConstructionQuote) => {
    // Server-computed availability when present; tier gating as the fallback
    const tierGated = quote.available === false || (quote.requires_tier_a && tier !== 'A');
    const gatedReason = quote.unavailable_reason
      || `Requires a Tier-A TradeDock — this is a Tier-${tier} facility.`;
    const canAffordDeposit = credits >= quote.deposit;

    return (
      <div key={quote.ship_type} className={`cq-card${tierGated ? ' gated' : ''}`}>
        <div className="cq-card-header">
          <span className="cq-ship-name">{prettyShipType(quote.ship_type)}</span>
          <div className="cq-badges">
            {quote.requires_tier_a && (
              <span className={`cq-badge tier-a${tierGated ? ' gated' : ''}`} title="Only Tier-A TradeDocks can lay this keel">
                TIER A ONLY
              </span>
            )}
            {quote.uses_specialized_slip && (
              <span className="cq-badge specialized" title="Built in a specialized construction slip">
                SPECIALIZED SLIP
              </span>
            )}
          </div>
        </div>

        <div className="cq-rows">
          <div className="cq-row">
            <span>Total cost</span>
            <span>{formatCredits(quote.total_cost)}</span>
          </div>
          <div className="cq-row">
            <span>Deposit</span>
            <span>{formatCredits(quote.deposit)}</span>
          </div>
          <div className="cq-row">
            <span>Build time</span>
            <span>{quote.build_days} day{quote.build_days === 1 ? '' : 's'}</span>
          </div>
          {typeof quote.daily_rent === 'number' && (
            <div className="cq-row">
              <span>Slip rent / day</span>
              <span>{formatCredits(quote.daily_rent)}</span>
            </div>
          )}
        </div>

        {renderResourceBundle(quote.resources_required)}

        {tierGated ? (
          <div className="cq-gated-reason">{gatedReason}</div>
        ) : (
          <button
            className="action-button primary cq-reserve-btn"
            onClick={() => {
              setConfirmQuote(quote);
              setReserveName('');
              setReserveError(null);
              setReserveSuccess(null);
            }}
            disabled={!canAffordDeposit}
            title={!canAffordDeposit ? 'Insufficient credits for the deposit' : undefined}
          >
            Reserve Slip
          </button>
        )}
      </div>
    );
  };

  const renderCountdownRow = (icon: string, label: string, iso: string) => {
    const { text, expired, urgent } = fmtCountdown(iso, nowMs);
    return (
      <div key={label} className={`cr-countdown${expired ? ' expired' : urgent ? ' urgent' : ''}`}>
        <span className="cr-countdown-icon" aria-hidden="true">{icon}</span>
        <span className="cr-countdown-label">{label}</span>
        <span className="cr-countdown-value">{text}</span>
      </div>
    );
  };

  const renderDeliverPanel = (reservation: ConstructionReservation, remaining: Record<ConstructionResource, number>) => {
    const totalQueued = RESOURCES.reduce((sum, r) => sum + (deliverAmounts[r] ?? 0), 0);

    return (
      <div className="cr-panel deliver-panel">
        <h5>📦 Deliver Materials</h5>
        <p className="cr-panel-warning">
          ⚠️ Deliveries are irreversible — materials are welded into the hull and cannot be
          recovered, even if the build is cancelled (ADR-0039).
        </p>
        {RESOURCES.map(resource => {
          const need = remaining[resource];
          const aboard = cargoAmount(resource);
          const max = Math.min(aboard, need);
          const value = deliverAmounts[resource] ?? 0;
          return (
            <div key={resource} className="cr-deliver-row">
              <span className="cr-deliver-resource">
                {iconFor(resource)} {getLabel(resource)}
              </span>
              <span className="cr-deliver-meta">
                need {need.toLocaleString()} · aboard {aboard.toLocaleString()}
              </span>
              <input
                type="number"
                min={0}
                max={max}
                value={value}
                onChange={e => {
                  const next = Math.max(0, Math.min(max, parseInt(e.target.value, 10) || 0));
                  setDeliverAmounts(prev => ({ ...prev, [resource]: next }));
                }}
                disabled={max === 0 || Boolean(busyAction)}
                aria-label={`${getLabel(resource)} to deliver`}
              />
              <button
                className="cr-max-btn"
                onClick={() => setDeliverAmounts(prev => ({ ...prev, [resource]: max }))}
                disabled={max === 0 || Boolean(busyAction)}
              >
                Max
              </button>
            </div>
          );
        })}
        <div className="cr-panel-actions">
          <button
            className="action-button"
            onClick={() => {
              setDeliverFor(null);
              setDeliverAmounts({});
            }}
            disabled={Boolean(busyAction)}
          >
            Close
          </button>
          <button
            className="action-button primary"
            onClick={() => submitDelivery(reservation)}
            disabled={totalQueued === 0 || Boolean(busyAction)}
          >
            {busyAction === `${reservation.id}:deliver` ? 'Delivering...' : `Deliver ${totalQueued.toLocaleString()} units`}
          </button>
        </div>
      </div>
    );
  };

  const renderRentPanel = (reservation: ConstructionReservation) => {
    const rentOwed = reservation.rent?.owed ?? reservation.rent_owed ?? 0;
    const dailyRate = typeof reservation.rent?.daily_rent === 'number'
      ? reservation.rent.daily_rent
      : typeof reservation.rent_per_day === 'number'
        ? reservation.rent_per_day
        : null;
    const overdueDays = reservation.rent?.overdue_canonical_days ?? 0;
    const forfeitDays = reservation.rent?.forfeit_after_days ?? null;
    const days = Math.max(1, Math.min(30, rentDays));

    return (
      <div className="cr-panel rent-panel">
        <h5>🏠 Pay Slip Rent</h5>
        {rentOwed > 0 && (
          <div className="cr-rent-owed">
            Currently owed: {formatCredits(rentOwed)}
            {forfeitDays !== null && overdueDays > 0 && (
              <> — {overdueDays.toFixed(1)} day{overdueDays === 1 ? '' : 's'} overdue (build forfeits at {forfeitDays})</>
            )}
          </div>
        )}
        <div className="cr-rent-days">
          <label htmlFor={`rent-days-${reservation.id}`}>Days (max 30)</label>
          <input
            id={`rent-days-${reservation.id}`}
            type="number"
            min={1}
            max={30}
            value={days}
            onChange={e => setRentDays(Math.max(1, Math.min(30, parseInt(e.target.value, 10) || 1)))}
            disabled={Boolean(busyAction)}
          />
          <div className="cr-rent-quick">
            {[1, 7, 14, 30].map(d => (
              <button
                key={d}
                className={`cr-quick-btn${days === d ? ' selected' : ''}`}
                onClick={() => setRentDays(d)}
                disabled={Boolean(busyAction)}
              >
                {d}d
              </button>
            ))}
          </div>
        </div>
        {dailyRate !== null ? (
          <div className="cr-rent-preview">
            {days} day{days === 1 ? '' : 's'} × {formatCredits(dailyRate)} = <strong>{formatCredits(days * dailyRate)}</strong>
          </div>
        ) : (
          <div className="cr-rent-preview">
            The dockmaster will bill the exact amount for {days} day{days === 1 ? '' : 's'} when you confirm.
          </div>
        )}
        <div className="cr-panel-actions">
          <button className="action-button" onClick={() => setRentFor(null)} disabled={Boolean(busyAction)}>
            Close
          </button>
          <button
            className="action-button primary"
            onClick={() => payRent(reservation)}
            disabled={Boolean(busyAction)}
          >
            {busyAction === `${reservation.id}:pay-rent` ? 'Paying...' : 'Pay Rent'}
          </button>
        </div>
      </div>
    );
  };

  const renderReservationCard = (reservation: ConstructionReservation) => {
    const state = normalizeState(reservation.state);
    const display = STATE_DISPLAY[state] ?? { label: (reservation.state || 'UNKNOWN').toUpperCase(), cls: 'unknown' };
    const isTerminal = TERMINAL_STATES.has(state);
    const isReady = READY_STATES.has(state);
    // PAUSED is a flag on a build state, not a state of its own
    const isPaused = reservation.paused === true || state === 'paused';
    const isActive = !isTerminal;
    // Deliveries and claims happen in person; the rest works remotely
    const isHere = !reservation.station_id || reservation.station_id === stationId;

    // The build-phase states are the phases themselves; legacy payloads may
    // carry a separate `phase` field instead
    const phaseSource = reservation.phase ?? (BUILD_PHASE_STATES.has(state) ? state : null);
    const phaseIdx = phaseSource
      ? PHASES.indexOf(normalizePhase(phaseSource))
      : state === 'deposit_collected'
        ? 0 // slip secured, frame not yet started
        : isReady
          ? PHASES.length - 1
          : -1; // queued / hold — no keel laid yet

    const milestones = normalizeMilestones(reservation.milestones);
    const firstUnpaid = milestones.find(m => !m.paid) ?? null;
    const shortfall = normalizeShortfall(reservation.next_checkpoint?.shortfall ?? reservation.checkpoint_shortfall);
    const rentInfo = reservation.rent;
    const rentOwed = rentInfo?.owed ?? reservation.rent_owed ?? 0;

    // The checkpoint a shortfall is blocking — the server names the gated
    // phase in next_checkpoint; fall back to the phase after the current one
    const gateLabel = reservation.next_checkpoint?.phase
      ? PHASE_LABELS[normalizePhase(reservation.next_checkpoint.phase)]
      : PHASE_LABELS[PHASES[Math.min(Math.max(phaseIdx, 0) + 1, PHASES.length - 1)]];

    const required = reservation.resources_required ?? {};
    const delivered = reservation.resources_delivered ?? {};
    const remaining = {} as Record<ConstructionResource, number>;
    RESOURCES.forEach(resource => {
      remaining[resource] = Math.max(0, Number(required[resource] ?? 0) - Number(delivered[resource] ?? 0));
    });
    const anyRemaining = RESOURCES.some(resource => remaining[resource] > 0);
    const anyRequired = RESOURCES.some(resource => Number(required[resource] ?? 0) > 0);

    // The server names its blockers in `needs`; derive reasons as a fallback
    const blockers: string[] = Array.isArray(reservation.needs)
      ? reservation.needs.filter((n): n is string => typeof n === 'string')
      : [];
    const pausedReasons: string[] = blockers.length > 0 ? blockers : [];
    if (isPaused && pausedReasons.length === 0) {
      if (shortfall.length > 0) pausedReasons.push('Checkpoint resources missing');
      if (firstUnpaid) pausedReasons.push('Milestone payment due');
      if (rentOwed > 0) pausedReasons.push('Slip rent unpaid');
      if (pausedReasons.length === 0) pausedReasons.push('Build halted — resolve outstanding obligations');
    }

    const canDeliver = isHere && anyRemaining && DELIVERY_STATES.has(state);
    const canPayRent = isActive && (RENT_STATES.has(state) || rentOwed > 0);
    const canClaim = isHere && isReady;
    // Backend rejects cancel on 'complete' (claim it or let the window lapse)
    const canCancel = isActive && !READY_STATES.has(state);

    const displayName = reservation.ship_name || prettyShipType(reservation.ship_type);
    const progressPct = Math.max(0, Math.min(100, Math.round(
      reservation.overall_progress_percent ?? reservation.progress_pct ?? 0
    )));

    return (
      <div key={reservation.id} className={`cr-card state-${display.cls}`}>
        <div className="cr-header">
          <div className="cr-identity">
            <span className="cr-ship-name">{displayName}</span>
            <span className="cr-ship-type">{prettyShipType(reservation.ship_type)}</span>
          </div>
          <div className="cr-header-right">
            {isActive && <span className="cr-progress">{progressPct}%</span>}
            <span className={`cr-state-badge ${display.cls}`}>{display.label}</span>
          </div>
        </div>

        {!isTerminal && (
          <BuildLine phaseIdx={phaseIdx} allLit={isReady} isPaused={isPaused} />
        )}

        {isActive && !isReady && phaseIdx >= 0 && (
          <div className="cr-phase-line">
            Current phase: <strong>{PHASE_LABELS[PHASES[phaseIdx]]}</strong>
            {typeof reservation.phase_progress_percent === 'number' && !isPaused && (
              <span className="cr-phase-pct"> ({Math.round(reservation.phase_progress_percent)}%)</span>
            )}
          </div>
        )}

        {state === 'queued' && typeof reservation.queue_position === 'number' && (
          <div className="cr-phase-line">
            ⏳ Queue position <strong>{reservation.queue_position}</strong>
            {typeof reservation.queue_length === 'number' ? ` of ${reservation.queue_length}` : ''}
            {' '}— the hold clock starts when a slip frees up
          </div>
        )}

        {!isHere && isActive && (
          <div className="cr-away-note">
            🛰️ Built at another station — dock there to deliver materials or claim the ship.
            Payments and cancellation work remotely.
          </div>
        )}

        <div className="cr-countdowns">
          {isActive && !isReady && reservation.phase_deadline &&
            renderCountdownRow('⏱️', 'Phase checkpoint', reservation.phase_deadline)}
          {isActive && reservation.hold_expires_at &&
            renderCountdownRow('🔒', 'Hold expires', reservation.hold_expires_at)}
          {isActive && reservation.claim_expires_at &&
            renderCountdownRow('📦', 'Claim window', reservation.claim_expires_at)}
        </div>

        {canPayRent && rentOwed > 0 && (
          <div className="cr-rent-warning">
            🏠 Rent owed: <strong>{formatCredits(rentOwed)}</strong>
          </div>
        )}

        {(isPaused || pausedReasons.length > 0) && isActive && (
          <div className="cr-paused-panel">
            <span className="cr-paused-title">{isPaused ? '⏸️ BUILD PAUSED' : '⚠️ ACTION NEEDED'}</span>
            <ul>
              {pausedReasons.map(reason => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          </div>
        )}

        {shortfall.length > 0 && (
          <ul className="cr-shortfall-list">
            {shortfall.map(entry => (
              <li key={entry.resource}>
                Need {entry.amount.toLocaleString()} more {entry.resource.toLowerCase()} for {entry.label || gateLabel}
              </li>
            ))}
          </ul>
        )}

        {isActive && anyRequired && (
          <div className="cr-resource-progress">
            {RESOURCES.map(resource => {
              const need = Number(required[resource] ?? 0);
              if (need <= 0) return null;
              const got = Number(delivered[resource] ?? 0);
              const pct = Math.max(0, Math.min(100, (got / need) * 100));
              return (
                <div key={resource} className="cr-resource-bar">
                  <span className="cr-resource-bar-label">
                    {iconFor(resource)} {getLabel(resource)}
                  </span>
                  <div className="cr-resource-bar-track">
                    <div className="cr-resource-bar-fill" style={{ width: `${pct}%` }} />
                  </div>
                  <span className="cr-resource-bar-value">
                    {got.toLocaleString()} / {need.toLocaleString()}
                  </span>
                </div>
              );
            })}
          </div>
        )}

        {isActive && milestones.length > 0 && (
          <div className="cr-milestones">
            {milestones.map(milestone => (
              <div key={milestone.name} className={`cr-milestone${milestone.paid ? ' paid' : ''}`}>
                <span className="cr-milestone-name">
                  {milestone.paid ? '✓' : '•'} {prettyShipType(milestone.name)}
                </span>
                {typeof milestone.amount === 'number' && (
                  <span className="cr-milestone-amount">{formatCredits(milestone.amount)}</span>
                )}
                {!milestone.paid && firstUnpaid && milestone.name === firstUnpaid.name && (
                  <button
                    className="cr-milestone-pay-btn"
                    onClick={() => payMilestone(reservation, milestone.name)}
                    disabled={Boolean(busyAction) || (typeof milestone.amount === 'number' && credits + (reservation.queue_bonus_credit || 0) < milestone.amount)}
                    title={typeof milestone.amount === 'number' && credits + (reservation.queue_bonus_credit || 0) < milestone.amount ? 'Insufficient credits' : undefined}
                  >
                    {busyAction === `${reservation.id}:pay-milestone` ? '...' : 'Pay'}
                  </button>
                )}
              </div>
            ))}
          </div>
        )}

        {cardErrors[reservation.id] && (
          <div className="genesis-error-message">
            <span className="error-icon">❌</span>
            {cardErrors[reservation.id]}
          </div>
        )}

        {isActive && (
          <div className="cr-actions">
            {canDeliver && (
              <button
                className="action-button"
                onClick={() => {
                  setDeliverFor(deliverFor === reservation.id ? null : reservation.id);
                  setDeliverAmounts({});
                  setRentFor(null);
                }}
                disabled={Boolean(busyAction)}
              >
                📦 Deliver
              </button>
            )}
            {canPayRent && (
              <button
                className="action-button"
                onClick={() => {
                  setRentFor(rentFor === reservation.id ? null : reservation.id);
                  setRentDays(7);
                  setDeliverFor(null);
                }}
                disabled={Boolean(busyAction)}
              >
                🏠 Pay Rent
              </button>
            )}
            {canClaim && (
              <button
                className="action-button primary cr-claim-btn"
                onClick={() => claimShip(reservation)}
                disabled={Boolean(busyAction)}
              >
                {busyAction === `${reservation.id}:claim` ? 'Claiming...' : '🚀 Claim Ship'}
              </button>
            )}
            {canCancel && (
              <button
                className="action-button cr-cancel-btn"
                onClick={() => setCancelFor(reservation)}
                disabled={Boolean(busyAction)}
              >
                Cancel
              </button>
            )}
          </div>
        )}

        {deliverFor === reservation.id && canDeliver && renderDeliverPanel(reservation, remaining)}
        {rentFor === reservation.id && canPayRent && renderRentPanel(reservation)}
      </div>
    );
  };

  // Active builds first, terminal history afterwards
  const sortedReservations = useMemo(() => {
    const list = reservations ?? [];
    return [...list].sort((a, b) => {
      const aTerminal = TERMINAL_STATES.has(normalizeState(a.state)) ? 1 : 0;
      const bTerminal = TERMINAL_STATES.has(normalizeState(b.state)) ? 1 : 0;
      return aTerminal - bTerminal;
    });
  }, [reservations]);

  const activeBuildCount = useMemo(
    () => (reservations ?? []).filter(r => !TERMINAL_STATES.has(normalizeState(r.state))).length,
    [reservations]
  );

  return (
    <div className="venue-container construction">
      <div className="venue-header">
        <button className="back-button" onClick={onBack}>
          ← Back to Hub
        </button>
        <h2>🏗️ Construction</h2>
        <span className={`construction-tier-badge tier-${tier.toLowerCase()}`}>
          TIER {tier} TRADEDOCK
        </span>
      </div>

      <div className="venue-content-area">
        <div className="construction-intro">
          <p>
            {stationName} runs {tier === 'A' ? 'full heavy-construction slips — every hull in the book can be laid here' : 'standard construction slips — heavy hulls require a Tier-A TradeDock'}.
            Place an order, deliver materials, keep the milestones paid, and claim your ship when the keel reaches the stars.
          </p>
        </div>

        <DeckPageTabs
          pages={[
            { id: 'orders', label: '📜 Ship Order Book' },
            {
              id: 'builds',
              label: `🛠️ My Builds${activeBuildCount > 0 ? ` (${activeBuildCount})` : ''}`
            }
          ]}
          activeId={activeTab}
          onSelect={(id) => setActiveTab(id as 'orders' | 'builds')}
          ariaLabel="Construction view"
          accent="#00d9ff"
          idBase="construction"
          className="construction-tabs"
        />

        {reserveSuccess && (
          <div className="genesis-success-message">
            <span className="success-icon">✅</span>
            {reserveSuccess}
          </div>
        )}

        <div
          role="tabpanel"
          id={`construction-panel-${activeTab}`}
          aria-labelledby={`construction-tab-${activeTab}`}
        >
          {activeTab === 'orders' && (
            <div className="construction-orders">
              {quotesMeta && (quotesMeta.slips || typeof quotesMeta.queue_length === 'number') && (
                <div className="construction-slip-status">
                  {quotesMeta.slips?.standard && (
                    <span className="cq-slip-stat">
                      🛠️ Standard slips: {quotesMeta.slips.standard.in_use}/{quotesMeta.slips.standard.capacity} in use
                    </span>
                  )}
                  {quotesMeta.slips?.specialized && quotesMeta.slips.specialized.capacity > 0 && (
                    <span className="cq-slip-stat">
                      🛸 Specialized slips: {quotesMeta.slips.specialized.in_use}/{quotesMeta.slips.specialized.capacity} in use
                    </span>
                  )}
                  {typeof quotesMeta.queue_length === 'number' && quotesMeta.queue_length > 0 && (
                    <span className="cq-slip-stat">⏳ Queue: {quotesMeta.queue_length}</span>
                  )}
                </div>
              )}
              {quotesLoading && !quotes && (
                <div className="catalog-loading">Pulling slips schedule from the dockmaster...</div>
              )}
              {quotesError && !quotesLoading && (
                <div className="genesis-error-message">
                  <span className="error-icon">❌</span>
                  {quotesError}
                  <button className="action-button" onClick={fetchQuotes}>Retry</button>
                </div>
              )}
              {!quotesError && quotes && (
                <div className="cq-grid">
                  {quotes.map(renderQuoteCard)}
                  {quotes.length === 0 && (
                    <p className="section-description">No hulls are quoted at this facility right now.</p>
                  )}
                </div>
              )}
            </div>
          )}

          {activeTab === 'builds' && (
            <div className="construction-builds">
              {reservationsLoading && !reservations && (
                <div className="catalog-loading">Checking the slips for your keels...</div>
              )}
              {reservationsError && (
                <div className="genesis-error-message">
                  <span className="error-icon">❌</span>
                  {reservationsError}
                  <button className="action-button" onClick={fetchReservations}>Retry</button>
                </div>
              )}
              {!reservationsError && reservations && (
                <>
                  {sortedReservations.map(renderReservationCard)}
                  {reservations.length === 0 && (
                    <p className="section-description">
                      No builds on the slips. Reserve a hull from the Ship Order Book to lay your first keel.
                    </p>
                  )}
                </>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Reserve confirmation — balance preview like the shipyard purchase panel */}
      {confirmQuote && (
        <div
          className="ship-confirm-overlay"
          onClick={() => !reserving && setConfirmQuote(null)}
        >
          <div className="ship-confirm-panel" onClick={e => e.stopPropagation()}>
            <h3>Reserve Slip — {prettyShipType(confirmQuote.ship_type)}</h3>
            <p className="section-description">
              The deposit places your order in the build queue. When a slip frees up you get a
              24-hour hold — pay the Keel Laid milestone
              {typeof confirmQuote.milestones?.keel_laid === 'number'
                ? ` (${formatCredits(confirmQuote.milestones.keel_laid)})`
                : ''} to confirm it and start construction. The balance is settled through
              milestones while you deliver materials from your cargo hold.
            </p>
            <label className="ship-name-label">
              Ship name (optional)
              <input
                type="text"
                value={reserveName}
                onChange={e => setReserveName(e.target.value)}
                placeholder={prettyShipType(confirmQuote.ship_type)}
                maxLength={50}
                disabled={reserving}
              />
            </label>
            <div className="confirm-cost-rows">
              <div className="confirm-cost-row">
                <span>Deposit due now</span>
                <span>{formatCredits(confirmQuote.deposit)}</span>
              </div>
              <div className="confirm-cost-row">
                <span>Total build cost</span>
                <span>{formatCredits(confirmQuote.total_cost)}</span>
              </div>
              <div className="confirm-cost-row">
                <span>Build time</span>
                <span>{confirmQuote.build_days} day{confirmQuote.build_days === 1 ? '' : 's'}</span>
              </div>
              <div className="confirm-cost-row">
                <span>Your credits</span>
                <span>{formatCredits(credits)}</span>
              </div>
              <div className={`confirm-cost-row balance${credits - confirmQuote.deposit < 0 ? ' negative' : ''}`}>
                <span>After deposit</span>
                <span>{formatCredits(credits - confirmQuote.deposit)}</span>
              </div>
            </div>
            {renderResourceBundle(confirmQuote.resources_required)}
            {reserveError && (
              <div className="genesis-error-message">
                <span className="error-icon">❌</span>
                {reserveError}
              </div>
            )}
            <div className="confirm-actions">
              <button
                className="action-button"
                onClick={() => setConfirmQuote(null)}
                disabled={reserving}
              >
                Cancel
              </button>
              <button
                className="action-button primary"
                onClick={reserveShip}
                disabled={reserving || credits < confirmQuote.deposit}
              >
                {reserving ? 'Reserving...' : 'Confirm Deposit'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Cancel confirmation with the refund-rule explanation */}
      {cancelFor && (() => {
        const cancelMilestones = normalizeMilestones(cancelFor.milestones);
        const hullPaid = cancelMilestones.some(m => m.name === 'hull_complete' && m.paid);
        const creditsPaid = cancelFor.credits_paid ?? null;
        // Server-computed (status_payload's estimated_refund) — advisory only, the
        // authoritative refund is recomputed server-side at cancel-commit time.
        const estRefund = cancelFor.estimated_refund ?? null;
        return (
        <div
          className="ship-confirm-overlay"
          onClick={() => !busyAction && setCancelFor(null)}
        >
          <div className="ship-confirm-panel" onClick={e => e.stopPropagation()}>
            <h3>Cancel Build — {cancelFor.ship_name || prettyShipType(cancelFor.ship_type)}</h3>
            <ul className="cr-refund-rules">
              <li>
                Cancelling refunds <strong>50%</strong> of the credits paid so far —
                <strong> 70%</strong> sell-back once the Hull Complete milestone is paid.
              </li>
              <li>
                Delivered materials are already welded into the hull and are never refunded
                (ADR-0039).
              </li>
              <li>Slip rent already paid is not returned.</li>
              <li>The slip is released back to the dock.</li>
            </ul>
            {creditsPaid !== null && (
              <div className="confirm-cost-rows">
                <div className="confirm-cost-row">
                  <span>Credits paid so far</span>
                  <span>{formatCredits(creditsPaid)}</span>
                </div>
                <div className="confirm-cost-row balance">
                  <span>Estimated refund ({hullPaid ? '70%' : '50%'})</span>
                  <span>{estRefund !== null ? formatCredits(estRefund) : '—'}</span>
                </div>
              </div>
            )}
            {cardErrors[cancelFor.id] && (
              <div className="genesis-error-message">
                <span className="error-icon">❌</span>
                {cardErrors[cancelFor.id]}
              </div>
            )}
            <div className="confirm-actions">
              <button
                className="action-button"
                onClick={() => setCancelFor(null)}
                disabled={Boolean(busyAction)}
              >
                Keep Building
              </button>
              <button
                className="action-button cr-cancel-btn"
                onClick={() => cancelReservation(cancelFor)}
                disabled={Boolean(busyAction)}
              >
                {busyAction === `${cancelFor.id}:cancel` ? 'Cancelling...' : 'Cancel Build'}
              </button>
            </div>
          </div>
        </div>
        );
      })()}

      {/* Claim ceremony — sister of COLONY ESTABLISHED */}
      {ceremony && (
        <div
          className="keel-ceremony-overlay"
          /* role="status" (not "dialog"/"alert"): this overlay auto-dismisses
             on a timer AND dismisses on click-anywhere -- both prove it's a
             non-modal transient announcement, not a blocking dialog. No
             aria-modal (the background isn't inert -- the game keeps
             running) and no focus trap (trapping a user inside something
             that vanishes on its own in 10s would be hostile). Not "alert"
             either -- a ship-delivered celebration is positive, not an
             assertive interruption. Matches its sister claimCelebration and
             the 3 cockpit-alert toasts' own role="status"
             (QUEUE-UX-OVERLAY-CONSISTENCY REVISE). */
          role="status"
          aria-label="Ship delivered"
          onClick={dismissCeremony}
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              dismissCeremony();
            }
          }}
        >
          <div className="keel-ceremony-card">
            <button
              className="keel-ceremony-dismiss"
              onClick={(e) => { e.stopPropagation(); dismissCeremony(); }}
              aria-label="Dismiss ship delivered notice"
            >
              ×
            </button>
            <div className="keel-scanline" aria-hidden="true"></div>
            <div className="keel-banner">⭐ KEEL TO STARS</div>
            <div className="keel-ship-icon">🚀</div>
            <div className="keel-ship-name">{ceremony.name} delivered</div>
            <div className="keel-detail">{ceremony.shipType} — ready in the hangar</div>
            <div className="keel-dismiss-hint">Click anywhere to continue</div>
          </div>
        </div>
      )}
    </div>
  );
};

export default ConstructionVenue;
