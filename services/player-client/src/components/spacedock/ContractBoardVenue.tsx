import React, { useState, useCallback, useEffect, useMemo } from 'react';
import { contractsAPI, shipAPI, storageAPI } from '../../services/api';
import { useResourceCatalog } from '../../hooks/useResourceCatalog';
import { formatCredits } from '../../utils/formatters';
import type {
  ContractDTO,
  ContractInsuranceCoverageTier,
  ContractMineResponse,
  ContractType,
} from '../../types/contract';
import type { ClaimableLockerDTO } from '../../types/storage';
import DeckPageTabs, { type DeckPage } from '../cockpit/DeckPageTabs';
import './contract-board-venue.css';

// =====================================================================
// Contract Board — per-station trade contract board
// (SYSTEMS/contracts.md, /api/v1/contracts/*)
//
// Board / My Contracts / Post Contract tabs. All payloads are normalized
// defensively (same feature-detect posture as PortOfficeVenue /
// ConstructionVenue) — the venue renders ONLY what the API returns, with
// explicit loading / empty / error states. No mock data, ever.
//
// SCROLL LAW: the primary action per tab (Accept / Deposit / Full delivery /
// Abandon / Cancel / Post / Retrieve) must never require a page-level scroll
// at 1440x900. Only `.cb-list` (the board rows / my-contracts rows /
// claimable-locker rows) scrolls internally — `.venue-content-area` itself
// is pinned to `overflow: hidden` for this venue (contract-board-venue.css),
// so the tab bar and each row's own inline action button are always
// reachable without scrolling the outer content area. The Post tab's 6
// fields (WO-CONTRACT-5-CLIENT-SURFACE added Contract Type) are laid out as
// 3 `.cb-field-row` pairs rather than a single-column stack specifically to
// stay under the same vertical budget the old 5-field column fit inside —
// `.cb-post-form` carries a defensive `overflow-y: auto` fallback for a
// smaller viewport only.
// =====================================================================

const asRecord = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;

// api.ts's apiRequest() already normalizes FastAPI detail/message into
// Error.message (see api.ts's apiRequest docstring) — just surface it.
const errorMessage = (error: unknown, fallback: string): string => {
  if (error instanceof Error && error.message) return error.message;
  return fallback;
};

// Countdown formatting against a ticking clock (house pattern, mirrors
// PortOfficeVenue.fmtCountdown / ConstructionVenue — wall-clock ISO
// deadlines arrive pre-scaled).
const fmtCountdown = (
  iso: string | null,
  nowMs: number
): { text: string; expired: boolean; urgent: boolean } => {
  if (!iso) return { text: '—', expired: false, urgent: false };
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
  const text =
    days > 0
      ? `${days}d ${pad(hours)}h ${pad(minutes)}m ${pad(seconds)}s`
      : `${pad(hours)}h ${pad(minutes)}m ${pad(seconds)}s`;
  return { text, expired: false, urgent };
};

// Feature-detect the updated credit balance out of an action response —
// accept returns remaining_balance, complete/abandon/post/cancel return
// credits (contracts.py:266-274/:356-362/:413-418/:550-558/:617-622).
// Locker deposits that auto-complete nest the same shape under complete_result.
const creditsFromResponse = (result: unknown): number | null => {
  const body = asRecord(result);
  if (!body) return null;
  const nested = asRecord(body.complete_result);
  const candidates = [body.credits, body.remaining_balance, nested?.credits];
  for (const c of candidates) {
    if (typeof c === 'number' && Number.isFinite(c)) return c;
  }
  return null;
};

/** Units of `commodity` currently on the piloted ship (0 if unknown/empty). */
const heldCommodityOnShip = async (commodity: string): Promise<number> => {
  const ship = asRecord(await shipAPI.getCurrentShip());
  if (!ship) return 0;
  const cargo = asRecord(ship.cargo);
  const contents = asRecord(cargo?.contents) ?? {};
  const held = contents[commodity];
  return typeof held === 'number' && Number.isFinite(held) ? Math.max(0, Math.floor(held)) : 0;
};

const shortId = (id: string) => `#${id.slice(0, 8)}`;

// PLAYER_POST_MIN_DEADLINE_HOURS (contract_service.py:81) — canon floor,
// mirrored client-side so the form fails fast instead of round-tripping.
const MIN_DEADLINE_HOURS = 1;

// WO-CONTRACT-1-INSURANCE. INSURANCE_PREMIUM_PCT (contract_service.py) —
// mirrored client-side ONLY for the live premium preview before the user
// commits; the server is authoritative and recomputes this exact math at
// /insure time regardless of what the client shows.
const INSURANCE_PREMIUM_PCT: Record<ContractInsuranceCoverageTier, number> = {
  basic: 2,
  standard: 5,
  hazard: 10,
};

const INSURANCE_TIER_LABELS: Record<ContractInsuranceCoverageTier, string> = {
  basic: 'Basic (2%)',
  standard: 'Standard (5%)',
  hazard: 'Hazard (10%)',
};

// WO-CONTRACT-5-CLIENT-SURFACE P1. post_player_contract restricts a
// player posting to these two types (contracts.py's PostContractRequest
// validator) — the other 5 ContractType members carry NPC-generator-only
// pricing this route never computes.
type PostableContractType = Extract<ContractType, 'cargo_delivery' | 'bulk_procurement'>;

const POST_CONTRACT_TYPE_LABELS: Record<PostableContractType, string> = {
  cargo_delivery: 'Cargo Delivery — one shipment',
  bulk_procurement: 'Bulk Procurement — large order, multi-trip OK',
};

interface ContractBoardVenueProps {
  stationId: string;
  stationName: string;
  credits: number;
  onCreditsSet: (value: number) => void;
  onBack: () => void;
}

type ContractBoardTab = 'board' | 'mine' | 'post';
type MineSubTab = 'accepted' | 'posted' | 'claimable';

// Outer tablist pages — labels are static, unlike the nested My Contracts
// subtabs (which carry a live count badge), so this is a module constant
// rather than rebuilt every render.
const CONTRACT_BOARD_TABS: DeckPage[] = [
  { id: 'board', label: '📋 Board' },
  { id: 'mine', label: '📜 My Contracts' },
  { id: 'post', label: '✉️ Post Contract' },
];

const ContractBoardVenue: React.FC<ContractBoardVenueProps> = ({
  stationId,
  stationName,
  credits,
  onCreditsSet,
  onBack,
}) => {
  const { catalog, getLabel, getIcon } = useResourceCatalog();

  const [activeTab, setActiveTab] = useState<ContractBoardTab>('board');

  // Board state
  const [board, setBoard] = useState<ContractDTO[] | null>(null);
  const [boardLoading, setBoardLoading] = useState(false);
  const [boardError, setBoardError] = useState<string | null>(null);
  const [boardActionError, setBoardActionError] = useState<string | null>(null);
  const [boardActionSuccess, setBoardActionSuccess] = useState<string | null>(null);

  // My contracts state
  const [mine, setMine] = useState<ContractMineResponse | null>(null);
  const [mineLoading, setMineLoading] = useState(false);
  const [mineError, setMineError] = useState<string | null>(null);
  const [mineSubTab, setMineSubTab] = useState<MineSubTab>('accepted');
  const [mineActionError, setMineActionError] = useState<string | null>(null);
  const [mineActionSuccess, setMineActionSuccess] = useState<string | null>(null);
  // Optional deposit qty override per contract (empty → deposit all held of that commodity).
  const [depositQtyByContract, setDepositQtyByContract] = useState<Record<string, string>>({});
  // WO-CONTRACT-1-INSURANCE: selected tier per contract, defaults to 'basic'.
  const [insuranceTierByContract, setInsuranceTierByContract] = useState<
    Record<string, ContractInsuranceCoverageTier>
  >({});
  // Last known locker progress from a successful deposit (no GET progress endpoint yet).
  const [lockerProgress, setLockerProgress] = useState<
    Record<string, { accumulated: number; quantityRequired: number }>
  >({});

  // Claimable-cargo state (WO-CONTRACT-5-CLIENT-SURFACE P2) — every
  // CLAIMABLE locker the player owns (any station), surfaced as a 3rd
  // My Contracts subtab so cargo stranded by a missed multi-trip deadline
  // is reachable and retrievable, not a dead end.
  const [claimable, setClaimable] = useState<ClaimableLockerDTO[] | null>(null);
  const [claimableLoading, setClaimableLoading] = useState(false);
  const [claimableError, setClaimableError] = useState<string | null>(null);
  const [claimableActionError, setClaimableActionError] = useState<string | null>(null);
  const [claimableActionSuccess, setClaimableActionSuccess] = useState<string | null>(null);

  // Shared action-in-flight guard (one action at a time, mirrors PortOfficeVenue)
  const [busyAction, setBusyAction] = useState<string | null>(null);

  // Post form state
  const [postContractType, setPostContractType] = useState<PostableContractType>('cargo_delivery');
  const [postCommodity, setPostCommodity] = useState('');
  const [postQuantity, setPostQuantity] = useState('');
  const [postPayment, setPostPayment] = useState('');
  const [postDeadline, setPostDeadline] = useState('');
  const [postInsurance, setPostInsurance] = useState('');
  const [postError, setPostError] = useState<string | null>(null);
  const [postSuccess, setPostSuccess] = useState<string | null>(null);

  // 1s clock for countdowns
  const [nowMs, setNowMs] = useState(() => Date.now());

  // --- Fetching ---

  const fetchBoard = useCallback(async () => {
    setBoardLoading(true);
    try {
      const data = await contractsAPI.getBoard(stationId);
      setBoard(Array.isArray(data) ? (data as ContractDTO[]) : []);
      setBoardError(null);
    } catch (error) {
      setBoardError(errorMessage(error, 'The contract board terminal is not responding. Please try again.'));
    } finally {
      setBoardLoading(false);
    }
  }, [stationId]);

  const fetchMine = useCallback(async () => {
    setMineLoading(true);
    try {
      const data = await contractsAPI.getMine();
      const rec = asRecord(data);
      setMine({
        posted: Array.isArray(rec?.posted) ? (rec!.posted as ContractDTO[]) : [],
        accepted: Array.isArray(rec?.accepted) ? (rec!.accepted as ContractDTO[]) : [],
      });
      setMineError(null);
    } catch (error) {
      setMineError(errorMessage(error, 'Could not open your contract ledger. Please try again.'));
    } finally {
      setMineLoading(false);
    }
  }, []);

  const fetchClaimable = useCallback(async () => {
    setClaimableLoading(true);
    try {
      const data = await storageAPI.getClaimable();
      setClaimable(Array.isArray(data) ? (data as ClaimableLockerDTO[]) : []);
      setClaimableError(null);
    } catch (error) {
      setClaimableError(errorMessage(error, 'Could not reach the claimable-cargo ledger. Please try again.'));
    } finally {
      setClaimableLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchBoard();
    fetchMine();
    fetchClaimable();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stationId]);

  // Poll every 30s while the venue is open (mirrors PortOfficeVenue)
  useEffect(() => {
    const interval = setInterval(() => {
      fetchBoard();
      fetchMine();
      fetchClaimable();
    }, 30000);
    return () => clearInterval(interval);
  }, [fetchBoard, fetchMine, fetchClaimable]);

  // 1s tick only while something is counting down
  const hasCountdowns = useMemo(() => {
    const boardHas = (board || []).some((c) => c.deadline);
    const mineHas = mine ? [...mine.posted, ...mine.accepted].some((c) => c.deadline) : false;
    return boardHas || mineHas;
  }, [board, mine]);

  useEffect(() => {
    if (!hasCountdowns) return;
    const tick = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(tick);
  }, [hasCountdowns]);

  // --- Actions ---

  const runAction = useCallback(
    async <T,>(
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
        setError(errorMessage(error, fallback));
        return null;
      } finally {
        setBusyAction(null);
      }
    },
    [busyAction]
  );

  const handleAccept = useCallback(
    async (contract: ContractDTO) => {
      setBoardActionSuccess(null);
      const result = await runAction(
        `accept-${contract.id}`,
        () => contractsAPI.accept(contract.id),
        setBoardActionError,
        'The board rejected your acceptance.'
      );
      if (result !== null) {
        const newCredits = creditsFromResponse(result);
        if (newCredits !== null) onCreditsSet(newCredits);
        setBoardActionSuccess(`Contract accepted — ${getLabel(contract.commodity_type)} delivery underway.`);
        await Promise.allSettled([fetchBoard(), fetchMine()]);
        setMineSubTab('accepted');
      }
    },
    [runAction, onCreditsSet, getLabel, fetchBoard, fetchMine]
  );

  const handleComplete = useCallback(
    async (contract: ContractDTO) => {
      setMineActionSuccess(null);
      const result = await runAction(
        `complete-${contract.id}`,
        () => contractsAPI.complete(contract.id),
        setMineActionError,
        'Delivery could not be verified.'
      );
      if (result !== null) {
        const newCredits = creditsFromResponse(result);
        if (newCredits !== null) onCreditsSet(newCredits);
        setMineActionSuccess('Contract completed — payout received.');
        await fetchMine();
      }
    },
    [runAction, onCreditsSet, fetchMine]
  );

  // Multi-trip fulfillment via station locker (storage-lockers.md). Complete
  // requires the FULL quantity in one hold; Deposit rents/reuses the locker
  // and banks whatever is on the ship (or an explicit qty) toward the total.
  const handleDeposit = useCallback(
    async (contract: ContractDTO) => {
      setMineActionSuccess(null);
      if (contract.destination_station_id !== stationId) {
        setMineActionError(
          'Deposit at the contract destination station — lockers open only there.'
        );
        return;
      }

      const result = await runAction(
        `deposit-${contract.id}`,
        async () => {
          const locker = asRecord(await storageAPI.rentLocker(contract.id));
          const lockerId = typeof locker?.id === 'string' ? locker.id : null;
          if (!lockerId) throw new Error('Locker rent returned no id.');

          const typed = parseInt(depositQtyByContract[contract.id] ?? '', 10);
          let quantity =
            Number.isFinite(typed) && typed > 0 ? typed : await heldCommodityOnShip(contract.commodity_type);
          if (quantity <= 0) {
            throw new Error(
              `No ${getLabel(contract.commodity_type)} in your hold to deposit.`
            );
          }

          return storageAPI.deposit(lockerId, quantity);
        },
        setMineActionError,
        'Locker deposit failed.'
      );

      if (result !== null) {
        const body = asRecord(result);
        const deposited = typeof body?.deposited === 'number' ? body.deposited : null;
        const accumulated = typeof body?.accumulated === 'number' ? body.accumulated : null;
        const quantityRequired =
          typeof body?.quantity_required === 'number' ? body.quantity_required : contract.quantity;
        const completed = body?.completed === true;

        if (accumulated != null && quantityRequired != null) {
          setLockerProgress((prev) => ({
            ...prev,
            [contract.id]: { accumulated, quantityRequired },
          }));
        }

        const newCredits = creditsFromResponse(result);
        if (newCredits !== null) onCreditsSet(newCredits);

        if (completed) {
          setMineActionSuccess(
            `Locker full (${accumulated ?? '—'}/${quantityRequired}) — contract completed, payout received.`
          );
          setLockerProgress((prev) => {
            const next = { ...prev };
            delete next[contract.id];
            return next;
          });
          await fetchMine();
        } else {
          setMineActionSuccess(
            `Deposited ${deposited ?? '—'} → locker ${accumulated ?? '—'}/${quantityRequired}. Return with more to finish.`
          );
        }
      }
    },
    [
      stationId,
      runAction,
      depositQtyByContract,
      getLabel,
      onCreditsSet,
      fetchMine,
    ]
  );

  const handleAbandon = useCallback(
    async (contract: ContractDTO) => {
      setMineActionSuccess(null);
      const result = await runAction(
        `abandon-${contract.id}`,
        () => contractsAPI.abandon(contract.id),
        setMineActionError,
        'The board could not process your withdrawal.'
      );
      if (result !== null) {
        const newCredits = creditsFromResponse(result);
        if (newCredits !== null) onCreditsSet(newCredits);
        setMineActionSuccess('Contract abandoned — penalty charged.');
        await fetchMine();
      }
    },
    [runAction, onCreditsSet, fetchMine]
  );

  const handleInsure = useCallback(
    async (contract: ContractDTO) => {
      setMineActionSuccess(null);
      const tier = insuranceTierByContract[contract.id] ?? 'basic';
      const result = await runAction(
        `insure-${contract.id}`,
        () => contractsAPI.insure(contract.id, tier),
        setMineActionError,
        'The underwriter rejected your policy.'
      );
      if (result !== null) {
        const newCredits = creditsFromResponse(result);
        if (newCredits !== null) onCreditsSet(newCredits);
        setMineActionSuccess(`Insured at ${INSURANCE_TIER_LABELS[tier]} — premium debited.`);
        await fetchMine();
      }
    },
    [runAction, insuranceTierByContract, onCreditsSet, fetchMine]
  );

  const handleCancel = useCallback(
    async (contract: ContractDTO) => {
      setMineActionSuccess(null);
      const result = await runAction(
        `cancel-${contract.id}`,
        () => contractsAPI.cancel(contract.id),
        setMineActionError,
        'Cancellation was rejected.'
      );
      if (result !== null) {
        const newCredits = creditsFromResponse(result);
        if (newCredits !== null) onCreditsSet(newCredits);
        setMineActionSuccess('Contract cancelled — escrow refunded.');
        await fetchMine();
      }
    },
    [runAction, onCreditsSet, fetchMine]
  );

  // Retrieve cargo from a CLAIMABLE locker (WO-CONTRACT-5-CLIENT-SURFACE
  // P2) — mirrors handleDeposit's station-match early-reject (the server
  // enforces the same "must be docked at the locker's station" guard,
  // storage_service._load_and_lock_retrieve_targets); the Retrieve button
  // is also disabled client-side for an off-station locker so this
  // branch is a defensive backstop, not the primary UX signal.
  const handleRetrieve = useCallback(
    async (locker: ClaimableLockerDTO) => {
      setClaimableActionSuccess(null);
      if (locker.stationId !== stationId) {
        setClaimableActionError('Dock at this locker’s station to retrieve its cargo.');
        return;
      }
      const result = await runAction(
        `retrieve-${locker.id}`,
        () => storageAPI.retrieve(locker.id),
        setClaimableActionError,
        'The locker would not release your cargo.'
      );
      if (result !== null) {
        const body = asRecord(result);
        const retrieved = typeof body?.retrieved === 'number' ? body.retrieved : null;
        const remaining = typeof body?.remaining === 'number' ? body.remaining : null;
        setClaimableActionSuccess(
          remaining !== null && remaining > 0
            ? `Retrieved ${retrieved ?? '—'} — ${remaining} still stored (too much for one trip; come back for the rest).`
            : `Retrieved ${retrieved ?? '—'} — locker emptied.`
        );
        await fetchClaimable();
      }
    },
    [stationId, runAction, fetchClaimable]
  );

  // --- Post-contract form ---

  const quantityNum = parseInt(postQuantity, 10);
  const paymentNum = parseFloat(postPayment);
  const insuranceNum = postInsurance ? parseFloat(postInsurance) : 0;
  const escrowPreview =
    (Number.isFinite(paymentNum) ? paymentNum : 0) + (Number.isFinite(insuranceNum) ? insuranceNum : 0);

  const canSubmitPost = Boolean(
    postCommodity &&
      Number.isFinite(quantityNum) &&
      quantityNum > 0 &&
      Number.isFinite(paymentNum) &&
      paymentNum > 0 &&
      postDeadline &&
      escrowPreview <= credits
  );

  const submitPost = useCallback(async () => {
    setPostSuccess(null);
    setPostError(null);
    if (!postCommodity) {
      setPostError('Select a commodity to contract for.');
      return;
    }
    if (!Number.isFinite(quantityNum) || quantityNum <= 0) {
      setPostError('Enter a quantity greater than zero.');
      return;
    }
    if (!Number.isFinite(paymentNum) || paymentNum <= 0) {
      setPostError('Enter a payment amount greater than zero.');
      return;
    }
    if (!postDeadline) {
      setPostError('Set a delivery deadline.');
      return;
    }
    const deadlineDate = new Date(postDeadline);
    if (Number.isNaN(deadlineDate.getTime())) {
      setPostError('The deadline is not a valid date/time.');
      return;
    }
    const hoursOut = (deadlineDate.getTime() - Date.now()) / 3_600_000;
    if (hoursOut < MIN_DEADLINE_HOURS) {
      setPostError(`The deadline must be at least ${MIN_DEADLINE_HOURS} hour(s) out.`);
      return;
    }
    if (escrowPreview > credits) {
      setPostError(
        `Insufficient credits — posting requires ${formatCredits(escrowPreview)} held in escrow, you have ${formatCredits(credits)}.`
      );
      return;
    }

    const result = await runAction(
      'post',
      () =>
        contractsAPI.post({
          destination_station_id: stationId,
          commodity_type: postCommodity,
          quantity: quantityNum,
          payment: paymentNum,
          deadline: deadlineDate.toISOString(),
          insurance_pool_reserve: insuranceNum || undefined,
          contract_type: postContractType,
        }),
      setPostError,
      'The board rejected your posting.'
    );

    if (result !== null) {
      const newCredits = creditsFromResponse(result);
      if (newCredits !== null) onCreditsSet(newCredits);
      setPostSuccess(`Contract posted — ${formatCredits(escrowPreview)} placed in escrow.`);
      setPostContractType('cargo_delivery');
      setPostCommodity('');
      setPostQuantity('');
      setPostPayment('');
      setPostDeadline('');
      setPostInsurance('');
      await Promise.allSettled([fetchBoard(), fetchMine()]);
    }
  }, [
    postContractType,
    postCommodity,
    quantityNum,
    paymentNum,
    postDeadline,
    escrowPreview,
    credits,
    runAction,
    stationId,
    insuranceNum,
    onCreditsSet,
    fetchBoard,
    fetchMine,
  ]);

  // --- Render helpers ---

  const renderCountdown = (iso: string | null) => {
    const { text, expired, urgent } = fmtCountdown(iso, nowMs);
    return (
      <span className={`cb-countdown${expired ? ' expired' : urgent ? ' urgent' : ''}`}>
        ⏱ {text}
      </span>
    );
  };

  const renderRoute = (contract: ContractDTO) => {
    const dest =
      contract.destination_station_id === stationId ? stationName : shortId(contract.destination_station_id);
    const origin = contract.origin_station_id
      ? contract.origin_station_id === stationId
        ? stationName
        : shortId(contract.origin_station_id)
      : 'Any origin';
    return `${origin} → ${dest}`;
  };

  const renderBoardRow = (contract: ContractDTO) => (
    <div className="cb-row" key={contract.id}>
      <div className="cb-row-main">
        <span className="cb-commodity">
          <span aria-hidden="true">{getIcon(contract.commodity_type)}</span> {getLabel(contract.commodity_type)} ×{' '}
          {contract.quantity}
        </span>
        <span className="cb-route">{renderRoute(contract)}</span>
      </div>
      <div className="cb-row-terms">
        <span className="cb-payment">{formatCredits(contract.payment ?? 0)}</span>
        {contract.penalty !== null && <span className="cb-penalty">Penalty {formatCredits(contract.penalty)}</span>}
        {contract.acceptance_fee_pct !== null && (
          <span
            className="cb-fee"
            title="Debited immediately on Accept — non-refundable even if you later abandon."
            aria-label={`Accept fee ${contract.acceptance_fee_pct}% (${formatCredits(
              ((contract.payment ?? 0) * contract.acceptance_fee_pct) / 100
            )}) — charged immediately on accept, non-refundable`}
          >
            Accept fee {contract.acceptance_fee_pct}% (
            {formatCredits(((contract.payment ?? 0) * contract.acceptance_fee_pct) / 100)})
          </span>
        )}
        {renderCountdown(contract.deadline)}
      </div>
      <div className="cb-row-actions">
        <button className="action-button primary" onClick={() => handleAccept(contract)} disabled={Boolean(busyAction)}>
          {busyAction === `accept-${contract.id}` ? 'Accepting...' : '✅ Accept'}
        </button>
      </div>
    </div>
  );

  // Fallback for statuses that carry no acceptor/issuer action in this
  // build (P4, WO-CONTRACT-5-CLIENT-SURFACE) — in_progress/partial_
  // fulfilled/disputed are schema-only today (contract_service.py's own
  // module docstring: bulk-procurement's `deliver`/`walk_away_bulk_
  // procurement` exist as functions but no route mounts them yet, see
  // contracts.py's header comment), so no action button is honest here;
  // this is a read-only status note, not a dead end. `expired` differs by
  // `kind`: an ACCEPTED contract you were delivering against may have
  // stranded cargo in a locker (see Claimable Cargo); a POSTED contract
  // you issued has already had its escrow swept one way or the other.
  const renderStatusNote = (contract: ContractDTO, kind: MineSubTab): React.ReactNode => {
    switch (contract.status) {
      case 'in_progress':
        return <span className="cb-status-note">🔄 Delivery in progress.</span>;
      case 'partial_fulfilled':
        return <span className="cb-status-note">◐ Partially fulfilled.</span>;
      case 'disputed':
        return <span className="cb-status-note">⚖️ Disputed — pending resolution.</span>;
      case 'expired':
        return kind === 'accepted' ? (
          <span className="cb-status-note">
            ⏳ Expired — any cargo you deposited is under <strong>Claimable Cargo</strong>.
          </span>
        ) : (
          <span className="cb-status-note">⏳ Expired — escrow already settled.</span>
        );
      default:
        return null;
    }
  };

  const renderMineRow = (contract: ContractDTO, kind: MineSubTab) => (
    <div className="cb-row" key={contract.id}>
      <div className="cb-row-main">
        <span className="cb-commodity">
          <span aria-hidden="true">{getIcon(contract.commodity_type)}</span> {getLabel(contract.commodity_type)} ×{' '}
          {contract.quantity}
        </span>
        <span className="cb-route">{renderRoute(contract)}</span>
        <span className={`cb-status cb-status-${contract.status}`}>
          {contract.status.replace(/_/g, ' ').toUpperCase()}
        </span>
      </div>
      <div className="cb-row-terms">
        <span className="cb-payment">{formatCredits(contract.payment ?? 0)}</span>
        {renderCountdown(contract.deadline)}
      </div>
      <div className="cb-row-actions">
        {kind === 'accepted' && contract.status === 'accepted' && (
          <>
            {contract.destination_station_id === stationId && (
              <div className="cb-deposit-group">
                <input
                  className="cb-deposit-qty"
                  type="number"
                  min={1}
                  inputMode="numeric"
                  placeholder="qty (blank = all)"
                  aria-label={`Deposit quantity for ${getLabel(contract.commodity_type)} — leave blank to deposit all held cargo`}
                  value={depositQtyByContract[contract.id] ?? ''}
                  onChange={(e) =>
                    setDepositQtyByContract((prev) => ({ ...prev, [contract.id]: e.target.value }))
                  }
                  disabled={Boolean(busyAction)}
                />
                <button
                  className="action-button primary"
                  onClick={() => handleDeposit(contract)}
                  disabled={Boolean(busyAction)}
                  title="Bank cargo in the station locker toward this contract (partial trips OK). Leave qty blank to deposit everything in your hold."
                >
                  {busyAction === `deposit-${contract.id}` ? 'Depositing...' : '🗄️ Deposit'}
                </button>
              </div>
            )}
            {lockerProgress[contract.id] && (
              <span
                className="cb-locker-progress"
                title="Locker progress from your last deposit"
                aria-label={`Locker progress ${lockerProgress[contract.id].accumulated} of ${
                  lockerProgress[contract.id].quantityRequired
                } from your last deposit`}
              >
                Locker {lockerProgress[contract.id].accumulated}/
                {lockerProgress[contract.id].quantityRequired}
              </span>
            )}
            <button
              className="action-button"
              onClick={() => handleComplete(contract)}
              disabled={Boolean(busyAction)}
              title="One-shot: requires the FULL contract quantity in your hold. Use Deposit for multi-trip locker delivery."
            >
              {busyAction === `complete-${contract.id}` ? 'Delivering...' : '📦 Full delivery'}
            </button>
            {contract.insurance_coverage_tier ? (
              <span
                className="cb-insured-badge"
                title={`Premium paid: ${formatCredits(contract.insurance_premium_paid ?? 0)}`}
                aria-label={`Insured at ${INSURANCE_TIER_LABELS[contract.insurance_coverage_tier]} — premium paid: ${formatCredits(
                  contract.insurance_premium_paid ?? 0
                )}`}
              >
                🛡️ Insured: {INSURANCE_TIER_LABELS[contract.insurance_coverage_tier]}
              </span>
            ) : (
              <div className="cb-insure-group">
                <select
                  className="cb-insure-tier"
                  aria-label={`Insurance tier for ${getLabel(contract.commodity_type)} contract`}
                  value={insuranceTierByContract[contract.id] ?? 'basic'}
                  onChange={(e) =>
                    setInsuranceTierByContract((prev) => ({
                      ...prev,
                      [contract.id]: e.target.value as ContractInsuranceCoverageTier,
                    }))
                  }
                  disabled={Boolean(busyAction)}
                >
                  {(Object.keys(INSURANCE_TIER_LABELS) as ContractInsuranceCoverageTier[]).map((tier) => (
                    <option key={tier} value={tier}>
                      {INSURANCE_TIER_LABELS[tier]}
                    </option>
                  ))}
                </select>
                <span className="cb-insure-premium-preview">
                  {formatCredits(
                    ((contract.payment ?? 0) *
                      INSURANCE_PREMIUM_PCT[insuranceTierByContract[contract.id] ?? 'basic']) /
                      100
                  )}
                </span>
                <button
                  className="action-button"
                  onClick={() => handleInsure(contract)}
                  disabled={Boolean(busyAction)}
                  title="Buy coverage on this contract — ship loss in transit pays out the contract penalty, less a deductible."
                >
                  {busyAction === `insure-${contract.id}` ? 'Insuring...' : '🛡️ Insure'}
                </button>
              </div>
            )}
            <button
              className="action-button danger"
              onClick={() => handleAbandon(contract)}
              disabled={Boolean(busyAction)}
            >
              {busyAction === `abandon-${contract.id}` ? 'Abandoning...' : '🏳️ Abandon'}
            </button>
          </>
        )}
        {kind === 'posted' && (contract.status === 'posted' || contract.status === 'accepted') && (
          <button className="action-button danger" onClick={() => handleCancel(contract)} disabled={Boolean(busyAction)}>
            {busyAction === `cancel-${contract.id}` ? 'Cancelling...' : '✖ Cancel'}
          </button>
        )}
        {!(
          (kind === 'accepted' && contract.status === 'accepted') ||
          (kind === 'posted' && (contract.status === 'posted' || contract.status === 'accepted'))
        ) && renderStatusNote(contract, kind)}
      </div>
    </div>
  );

  const renderClaimableRow = (locker: ClaimableLockerDTO) => {
    const atLockerStation = locker.stationId === stationId;
    const station = atLockerStation ? stationName : shortId(locker.stationId);
    return (
      <div className="cb-row" key={locker.id}>
        <div className="cb-row-main">
          <span className="cb-commodity">
            <span aria-hidden="true">{getIcon(locker.commodity)}</span> {getLabel(locker.commodity)} ×{' '}
            {locker.storedUnits}
          </span>
          <span className="cb-route">{station}</span>
        </div>
        <div className="cb-row-terms">
          <span
            className="cb-fee"
            title="Settled as of the last rent tick — not the exact bill; rent keeps accruing."
            aria-label={`Accrued rent as of last settlement: ${formatCredits(
              locker.accruedFee
            )} — rent keeps accruing, this is not the final bill`}
          >
            Accrued rent (last settlement): {formatCredits(locker.accruedFee)}
          </span>
          <span className="cb-countdown">{formatCredits(locker.rentRate)}/unit/day</span>
        </div>
        <div className="cb-row-actions">
          <button
            className="action-button primary"
            onClick={() => handleRetrieve(locker)}
            disabled={Boolean(busyAction) || !atLockerStation}
            title={
              atLockerStation
                ? 'Retrieve as much as fits in your hold now — a locker larger than your hold stays claimable for a later trip.'
                : `Dock at ${station} to retrieve this cargo.`
            }
            aria-label={
              atLockerStation
                ? 'Retrieve as much as fits in your hold now'
                : `Dock at ${station} to retrieve this cargo`
            }
          >
            {busyAction === `retrieve-${locker.id}` ? 'Retrieving...' : '📤 Retrieve'}
          </button>
        </div>
      </div>
    );
  };

  const renderBoardTab = () => (
    <div className="cb-tab-content">
      <div className="cb-toolbar">
        <span className="cb-toolbar-count">
          {board ? `${board.length} contract${board.length === 1 ? '' : 's'} posted` : ''}
        </span>
        <button className="action-button" onClick={fetchBoard} disabled={boardLoading}>
          🔄 Refresh
        </button>
      </div>
      {boardActionError && (
        <div className="genesis-error-message" aria-live="polite" aria-atomic="true">
          <span className="error-icon">❌</span>
          {boardActionError}
        </div>
      )}
      {boardActionSuccess && (
        <div className="genesis-success-message" aria-live="polite" aria-atomic="true">
          <span className="success-icon">✅</span>
          {boardActionSuccess}
        </div>
      )}
      <div className="cb-list">
        {boardLoading && !board && <div className="catalog-loading">Pulling the contract board manifest...</div>}
        {boardError && !board && (
          <div className="genesis-error-message">
            <span className="error-icon">❌</span>
            {boardError}
            <button className="action-button" onClick={fetchBoard}>
              Retry
            </button>
          </div>
        )}
        {board && board.length === 0 && (
          <p className="section-description">No contracts posted at this station right now. Check back later.</p>
        )}
        {board && board.map(renderBoardRow)}
      </div>
    </div>
  );

  const renderMineTab = () => (
    <div className="cb-tab-content">
      <DeckPageTabs
        pages={[
          { id: 'accepted', label: `📦 Accepted${mine ? ` (${mine.accepted.length})` : ''}` },
          { id: 'posted', label: `📜 Posted${mine ? ` (${mine.posted.length})` : ''}` },
          { id: 'claimable', label: `🗄️ Claimable Cargo${claimable ? ` (${claimable.length})` : ''}` },
        ]}
        activeId={mineSubTab}
        onSelect={(id) => setMineSubTab(id as MineSubTab)}
        ariaLabel="My contracts filter"
        accent="#00d9ff"
        idBase="cb-mine"
        className="cb-mine-subtabs"
      />
      {mineSubTab !== 'claimable' && mineActionError && (
        <div className="genesis-error-message" aria-live="polite" aria-atomic="true">
          <span className="error-icon">❌</span>
          {mineActionError}
        </div>
      )}
      {mineSubTab !== 'claimable' && mineActionSuccess && (
        <div className="genesis-success-message" aria-live="polite" aria-atomic="true">
          <span className="success-icon">✅</span>
          {mineActionSuccess}
        </div>
      )}
      {mineSubTab === 'claimable' && claimableActionError && (
        <div className="genesis-error-message" aria-live="polite" aria-atomic="true">
          <span className="error-icon">❌</span>
          {claimableActionError}
        </div>
      )}
      {mineSubTab === 'claimable' && claimableActionSuccess && (
        <div className="genesis-success-message" aria-live="polite" aria-atomic="true">
          <span className="success-icon">✅</span>
          {claimableActionSuccess}
        </div>
      )}
      <div
        className="cb-list"
        role="tabpanel"
        id={`cb-mine-panel-${mineSubTab}`}
        aria-labelledby={`cb-mine-tab-${mineSubTab}`}
      >
        {mineSubTab !== 'claimable' && mineLoading && !mine && (
          <div className="catalog-loading">Opening your contract ledger...</div>
        )}
        {mineSubTab !== 'claimable' && mineError && !mine && (
          <div className="genesis-error-message">
            <span className="error-icon">❌</span>
            {mineError}
            <button className="action-button" onClick={fetchMine}>
              Retry
            </button>
          </div>
        )}
        {mine && mineSubTab === 'accepted' && mine.accepted.length === 0 && (
          <p className="section-description">You haven&apos;t accepted any contracts yet.</p>
        )}
        {mine && mineSubTab === 'accepted' && mine.accepted.map((c) => renderMineRow(c, 'accepted'))}
        {mine && mineSubTab === 'posted' && mine.posted.length === 0 && (
          <p className="section-description">You haven&apos;t posted any contracts yet.</p>
        )}
        {mine && mineSubTab === 'posted' && mine.posted.map((c) => renderMineRow(c, 'posted'))}
        {mineSubTab === 'claimable' && claimableLoading && !claimable && (
          <div className="catalog-loading">Opening the claimable-cargo ledger...</div>
        )}
        {mineSubTab === 'claimable' && claimableError && !claimable && (
          <div className="genesis-error-message">
            <span className="error-icon">❌</span>
            {claimableError}
            <button className="action-button" onClick={fetchClaimable}>
              Retry
            </button>
          </div>
        )}
        {mineSubTab === 'claimable' && claimable && claimable.length === 0 && (
          <p className="section-description">No cargo waiting to be claimed — nothing missed a delivery deadline.</p>
        )}
        {mineSubTab === 'claimable' && claimable && claimable.map(renderClaimableRow)}
      </div>
    </div>
  );

  const renderPostTab = () => (
    <div className="cb-tab-content cb-post-tab">
      <div className="cb-post-form">
        <p className="section-description">
          Post a delivery contract for {stationName}. Escrow (payment + insurance reserve) is debited immediately
          and held until the contract completes, is abandoned, or is cancelled.
        </p>

        <div className="cb-field-row">
          <label className="cb-field">
            <span>Contract Type</span>
            <select
              aria-label="Contract Type"
              value={postContractType}
              onChange={(e) => setPostContractType(e.target.value as PostableContractType)}
            >
              {(Object.keys(POST_CONTRACT_TYPE_LABELS) as PostableContractType[]).map(
                (type) => (
                  <option key={type} value={type}>
                    {POST_CONTRACT_TYPE_LABELS[type]}
                  </option>
                )
              )}
            </select>
          </label>

          <label className="cb-field">
            <span>Commodity</span>
            <select aria-label="Commodity" value={postCommodity} onChange={(e) => setPostCommodity(e.target.value)}>
              <option value="">Select a commodity...</option>
              {catalog.map((entry) => (
                <option key={entry.name} value={entry.name}>
                  {getLabel(entry.name)}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="cb-field-row">
          <label className="cb-field">
            <span>Quantity</span>
            <input
              type="number"
              aria-label="Quantity"
              min={1}
              value={postQuantity}
              onChange={(e) => setPostQuantity(e.target.value)}
            />
          </label>

          <label className="cb-field">
            <span>Payment (credits)</span>
            <input
              type="number"
              aria-label="Payment"
              min={1}
              value={postPayment}
              onChange={(e) => setPostPayment(e.target.value)}
            />
          </label>
        </div>

        <div className="cb-field-row">
          <label className="cb-field">
            <span>Deadline</span>
            <input
              type="datetime-local"
              aria-label="Deadline"
              value={postDeadline}
              onChange={(e) => setPostDeadline(e.target.value)}
            />
          </label>

          <label className="cb-field">
            <span>Insurance reserve (optional)</span>
            <input
              type="number"
              aria-label="Insurance reserve"
              min={0}
              value={postInsurance}
              onChange={(e) => setPostInsurance(e.target.value)}
            />
          </label>
        </div>

        <div className="cb-escrow-preview">
          <span>Escrow to be debited</span>
          <span className="cb-escrow-amount">{formatCredits(escrowPreview)}</span>
        </div>

        {postError && (
          <div className="genesis-error-message" aria-live="polite" aria-atomic="true">
            <span className="error-icon">❌</span>
            {postError}
          </div>
        )}
        {postSuccess && (
          <div className="genesis-success-message" aria-live="polite" aria-atomic="true">
            <span className="success-icon">✅</span>
            {postSuccess}
          </div>
        )}

        <button
          className="action-button primary cb-post-submit"
          onClick={submitPost}
          disabled={Boolean(busyAction) || !canSubmitPost}
        >
          {busyAction === 'post' ? 'Posting...' : '✉️ Post Contract'}
        </button>
      </div>
    </div>
  );

  return (
    <div className="venue-container contracts">
      <div className="venue-header">
        <button className="back-button" onClick={onBack}>
          ← Back to Hub
        </button>
        <h2>📋 Contract Board</h2>
      </div>

      <DeckPageTabs
        pages={CONTRACT_BOARD_TABS}
        activeId={activeTab}
        onSelect={(id) => setActiveTab(id as ContractBoardTab)}
        ariaLabel="Contract board section"
        accent="#00d9ff"
        idBase="cb"
        className="cb-tabs"
      />

      <div
        className="venue-content-area cb-content-area"
        role="tabpanel"
        id={`cb-panel-${activeTab}`}
        aria-labelledby={`cb-tab-${activeTab}`}
      >
        {activeTab === 'board' && renderBoardTab()}
        {activeTab === 'mine' && renderMineTab()}
        {activeTab === 'post' && renderPostTab()}
      </div>
    </div>
  );
};

export default ContractBoardVenue;
