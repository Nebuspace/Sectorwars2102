import React, { useState, useCallback, useEffect, useMemo } from 'react';
import { contractsAPI } from '../../services/api';
import { useResourceCatalog } from '../../hooks/useResourceCatalog';
import { formatCredits } from '../../utils/formatters';
import type { ContractDTO, ContractMineResponse } from '../../types/contract';
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
// SCROLL LAW: the primary action per tab (Accept / Complete / Abandon /
// Cancel / Post) must never require a page-level scroll at 1440x900. Only
// `.cb-list` (the board rows / my-contracts rows) scrolls internally —
// `.venue-content-area` itself is pinned to `overflow: hidden` for this
// venue (contract-board-venue.css), so the tab bar and each row's own
// inline action button are always reachable without scrolling the outer
// content area. The Post tab's compact 5-field form fits without scrolling
// at the reference resolution; `.cb-post-form` carries a defensive
// `overflow-y: auto` fallback for a smaller viewport only.
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
const creditsFromResponse = (result: unknown): number | null => {
  const body = asRecord(result);
  if (!body) return null;
  const candidates = [body.credits, body.remaining_balance];
  for (const c of candidates) {
    if (typeof c === 'number' && Number.isFinite(c)) return c;
  }
  return null;
};

const shortId = (id: string) => `#${id.slice(0, 8)}`;

// PLAYER_POST_MIN_DEADLINE_HOURS (contract_service.py:81) — canon floor,
// mirrored client-side so the form fails fast instead of round-tripping.
const MIN_DEADLINE_HOURS = 1;

interface ContractBoardVenueProps {
  stationId: string;
  stationName: string;
  credits: number;
  onCreditsSet: (value: number) => void;
  onBack: () => void;
}

type ContractBoardTab = 'board' | 'mine' | 'post';
type MineSubTab = 'accepted' | 'posted';

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

  // Shared action-in-flight guard (one action at a time, mirrors PortOfficeVenue)
  const [busyAction, setBusyAction] = useState<string | null>(null);

  // Post form state
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

  useEffect(() => {
    fetchBoard();
    fetchMine();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stationId]);

  // Poll every 30s while the venue is open (mirrors PortOfficeVenue)
  useEffect(() => {
    const interval = setInterval(() => {
      fetchBoard();
      fetchMine();
    }, 30000);
    return () => clearInterval(interval);
  }, [fetchBoard, fetchMine]);

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
        }),
      setPostError,
      'The board rejected your posting.'
    );

    if (result !== null) {
      const newCredits = creditsFromResponse(result);
      if (newCredits !== null) onCreditsSet(newCredits);
      setPostSuccess(`Contract posted — ${formatCredits(escrowPreview)} placed in escrow.`);
      setPostCommodity('');
      setPostQuantity('');
      setPostPayment('');
      setPostDeadline('');
      setPostInsurance('');
      await Promise.allSettled([fetchBoard(), fetchMine()]);
    }
  }, [
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
        {renderCountdown(contract.deadline)}
      </div>
      <div className="cb-row-actions">
        <button className="action-button primary" onClick={() => handleAccept(contract)} disabled={Boolean(busyAction)}>
          {busyAction === `accept-${contract.id}` ? 'Accepting...' : '✅ Accept'}
        </button>
      </div>
    </div>
  );

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
            <button
              className="action-button primary"
              onClick={() => handleComplete(contract)}
              disabled={Boolean(busyAction)}
            >
              {busyAction === `complete-${contract.id}` ? 'Delivering...' : '📦 Complete'}
            </button>
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
      </div>
    </div>
  );

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
        <div className="genesis-error-message">
          <span className="error-icon">❌</span>
          {boardActionError}
        </div>
      )}
      {boardActionSuccess && (
        <div className="genesis-success-message">
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
        ]}
        activeId={mineSubTab}
        onSelect={(id) => setMineSubTab(id as MineSubTab)}
        ariaLabel="My contracts filter"
        accent="#00d9ff"
        idBase="cb-mine"
        className="cb-mine-subtabs"
      />
      {mineActionError && (
        <div className="genesis-error-message">
          <span className="error-icon">❌</span>
          {mineActionError}
        </div>
      )}
      {mineActionSuccess && (
        <div className="genesis-success-message">
          <span className="success-icon">✅</span>
          {mineActionSuccess}
        </div>
      )}
      <div
        className="cb-list"
        role="tabpanel"
        id={`cb-mine-panel-${mineSubTab}`}
        aria-labelledby={`cb-mine-tab-${mineSubTab}`}
      >
        {mineLoading && !mine && <div className="catalog-loading">Opening your contract ledger...</div>}
        {mineError && !mine && (
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

        <div className="cb-escrow-preview">
          <span>Escrow to be debited</span>
          <span className="cb-escrow-amount">{formatCredits(escrowPreview)}</span>
        </div>

        {postError && (
          <div className="genesis-error-message">
            <span className="error-icon">❌</span>
            {postError}
          </div>
        )}
        {postSuccess && (
          <div className="genesis-success-message">
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
