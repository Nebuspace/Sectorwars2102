import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useGame } from '../../contexts/GameContext';
import { portOwnershipAPI } from '../../services/api';

type SyndicateShare = { player_id: string; pct: number };

type SyndicateStatus = {
  station_id: string;
  mode: string;
  shares: SyndicateShare[];
};

export type GovernanceVoteType =
  | 'tariff'
  | 'upgrade'
  | 'sale'
  | 'disbandment'
  | 'withdrawal';

export type GovernancePosition =
  | 'for'
  | 'against'
  | 'absent'
  | 'veto'
  | 'against_veto';

const VOTE_TYPES: GovernanceVoteType[] = [
  'tariff',
  'upgrade',
  'sale',
  'disbandment',
  'withdrawal',
];

const VETO_TYPES = new Set<GovernanceVoteType>(['upgrade', 'sale', 'disbandment']);

const POSITIONS_BASE: GovernancePosition[] = ['for', 'against', 'absent'];
const POSITIONS_WITH_VETO: GovernancePosition[] = [
  'for',
  'against',
  'absent',
  'veto',
  'against_veto',
];

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

const isNetworkCollapseMessage = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed)
  );
};

/** Same densify contract as formatSyndicateError (403/429/TypeError). */
export function formatGovernanceVoteError(error: unknown, fallback: string): string {
  if (error instanceof TypeError) return fallback;
  const e = asRecord(error);
  const response = asRecord(e?.response);
  const status =
    (typeof e?.status === 'number' ? e.status : undefined) ??
    (typeof response?.status === 'number' ? response.status : undefined);
  const data = asRecord(response?.data);
  const raw = data?.message ?? data?.detail;
  let detailCopy: string | undefined;
  if (typeof raw === 'string' && raw.trim()) detailCopy = raw.trim();
  const message = typeof e?.message === 'string' ? e.message : undefined;
  const messageDetail =
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim()) &&
    !isNetworkCollapseMessage(message)
      ? message.trim()
      : undefined;
  const serverCopy = detailCopy ?? messageDetail;

  if (status === 403) {
    if (serverCopy) return serverCopy;
    return 'You do not have permission to cast this governance vote.';
  }
  if (status === 429) {
    return 'Governance vote rate limit exceeded — wait a moment and try again.';
  }
  if (detailCopy) return detailCopy;
  if (!response && messageDetail) return messageDetail;
  if (message && isNetworkCollapseMessage(message)) return fallback;
  return fallback;
}

function parseStatus(raw: unknown): SyndicateStatus | null {
  const body = asRecord(raw);
  if (!body) return null;
  const sharesRaw = Array.isArray(body.shares) ? body.shares : [];
  const shares: SyndicateShare[] = sharesRaw
    .map((row) => {
      const r = asRecord(row);
      if (!r || typeof r.player_id !== 'string') return null;
      const pct = typeof r.pct === 'number' ? r.pct : Number(r.pct);
      if (!Number.isFinite(pct)) return null;
      return { player_id: r.player_id, pct: Math.round(pct) };
    })
    .filter((s): s is SyndicateShare => s !== null);
  return {
    station_id: typeof body.station_id === 'string' ? body.station_id : '',
    mode: typeof body.mode === 'string' ? body.mode : 'solo',
    shares,
  };
}

function summarizeVoteResult(raw: unknown): string {
  const body = asRecord(raw);
  if (!body) return 'Vote recorded.';
  const parts: string[] = [];
  if (typeof body.status === 'string') parts.push(`Status: ${body.status}`);
  if (typeof body.window_ends_at === 'string') parts.push(`Window ends ${body.window_ends_at}`);
  const ballots = Array.isArray(body.ballots) ? body.ballots : null;
  if (ballots) parts.push(`${ballots.length} ballot(s)`);
  const resolution = asRecord(body.resolution);
  if (resolution) {
    if (typeof resolution.yes_weight === 'number' && typeof resolution.threshold === 'number') {
      parts.push(`Yes ${resolution.yes_weight} / threshold ${resolution.threshold}`);
    }
    if (typeof resolution.status === 'string') parts.push(`Resolution: ${resolution.status}`);
    if (resolution.passed === true) parts.push('Passed');
    if (resolution.passed === false) parts.push('Not passed');
  }
  return parts.length > 0 ? parts.join(' · ') : 'Vote recorded.';
}

export interface PortOfficeGovernancePanelProps {
  stationId: string;
  stationName: string;
}

/**
 * Port Office syndicate co-owner policy votes — tip GS /governance/vote (LEG-4121).
 * invent=0: tariff/upgrade(capex)/sale/disbandment/withdrawal cast only —
 * no stake-transfer, faction arbitration, upgrade catalog, or forfeiture.
 */
const PortOfficeGovernancePanel: React.FC<PortOfficeGovernancePanelProps> = ({
  stationId,
  stationName,
}) => {
  const { playerState } = useGame();
  const playerId = playerState?.id ? String(playerState.id) : null;

  const [status, setStatus] = useState<SyndicateStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const [voteType, setVoteType] = useState<GovernanceVoteType>('tariff');
  const [position, setPosition] = useState<GovernancePosition>('for');
  const [tariffPct, setTariffPct] = useState(10);
  const [withdrawalSchedule, setWithdrawalSchedule] = useState<'daily' | 'weekly' | 'monthly'>(
    'weekly',
  );
  const [upgradeCapex, setUpgradeCapex] = useState(500001);
  const [confirmArmed, setConfirmArmed] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const raw = await portOwnershipAPI.getSyndicateStatus(stationId);
      setStatus(parseStatus(raw));
    } catch (e: unknown) {
      setStatus(null);
      setError(
        formatGovernanceVoteError(e, 'Governance vote feed is down. Please try again.'),
      );
    } finally {
      setLoading(false);
    }
  }, [stationId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    setConfirmArmed(false);
    setPosition((prev) => {
      if (!VETO_TYPES.has(voteType) && (prev === 'veto' || prev === 'against_veto')) {
        return 'for';
      }
      return prev;
    });
  }, [voteType]);

  const myShare = useMemo(() => {
    if (!playerId || !status) return null;
    return status.shares.find((s) => String(s.player_id) === String(playerId)) ?? null;
  }, [playerId, status]);

  const showPanel =
    !!status && status.mode === 'syndicate' && !!myShare && myShare.pct >= 1;

  const positions = VETO_TYPES.has(voteType) ? POSITIONS_WITH_VETO : POSITIONS_BASE;

  const buildProposedValue = (): unknown => {
    if (voteType === 'tariff') {
      const fraction = Math.min(0.25, Math.max(0.05, tariffPct / 100));
      return { tax_rate: fraction };
    }
    if (voteType === 'withdrawal') return { schedule: withdrawalSchedule };
    if (voteType === 'upgrade') return { capex: Math.max(0, Math.round(upgradeCapex)) };
    return null;
  };

  const needsConfirm = voteType === 'sale' || voteType === 'disbandment';

  const cast = async () => {
    if (busy || !myShare) return;
    if (needsConfirm && !confirmArmed) return;
    setBusy(true);
    setMsg(null);
    try {
      const result = await portOwnershipAPI.castGovernanceVote(stationId, {
        vote_type: voteType,
        proposed_value: buildProposedValue(),
        voter_stake_pct: myShare.pct,
        position,
      });
      setMsg({ ok: true, text: summarizeVoteResult(result) });
      setConfirmArmed(false);
    } catch (e: unknown) {
      setMsg({
        ok: false,
        text: formatGovernanceVoteError(e, 'Governance vote failed. Please try again.'),
      });
    } finally {
      setBusy(false);
    }
  };

  if (loading && !status) {
    return (
      <div className="po-section" data-testid="po-governance-panel">
        <h3 className="po-section-title">🗳️ Syndicate Policy Votes</h3>
        <div className="catalog-loading">Loading governance desk...</div>
      </div>
    );
  }

  if (error && !status) {
    return (
      <div className="po-section" data-testid="po-governance-panel">
        <h3 className="po-section-title">🗳️ Syndicate Policy Votes</h3>
        <div className="genesis-error-message" role="alert">
          <span className="error-icon">❌</span>
          {error}
          <button type="button" className="action-button" onClick={() => void reload()}>
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!showPanel || !status || !myShare) return null;

  return (
    <div className="po-section" data-testid="po-governance-panel">
      <h3 className="po-section-title">🗳️ Syndicate Policy Votes</h3>
      <p className="section-description">
        Co-owner policy motions at {stationName}. Your locked stake for this cast:{' '}
        <strong>{myShare.pct}%</strong>. Opening a motion and casting a ballot use the same tip
        endpoint — no upgrade catalog, stake-transfer, or forfeiture here.
      </p>

      <div className="po-bid-row" data-testid="po-governance-form">
        <label htmlFor="po-governance-vote-type">Vote type</label>
        <select
          id="po-governance-vote-type"
          data-testid="po-governance-vote-type"
          value={voteType}
          disabled={busy}
          onChange={(e) => setVoteType(e.target.value as GovernanceVoteType)}
        >
          {VOTE_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>

        <label htmlFor="po-governance-position">Position</label>
        <select
          id="po-governance-position"
          data-testid="po-governance-position"
          value={position}
          disabled={busy}
          onChange={(e) => setPosition(e.target.value as GovernancePosition)}
        >
          {positions.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>

        {voteType === 'tariff' && (
          <>
            <label htmlFor="po-governance-tariff">Proposed tariff %</label>
            <input
              id="po-governance-tariff"
              data-testid="po-governance-tariff"
              type="number"
              min={5}
              max={25}
              step={1}
              value={tariffPct}
              disabled={busy}
              onChange={(e) => setTariffPct(Number(e.target.value))}
            />
          </>
        )}

        {voteType === 'withdrawal' && (
          <>
            <label htmlFor="po-governance-withdrawal">Withdrawal schedule</label>
            <select
              id="po-governance-withdrawal"
              data-testid="po-governance-withdrawal"
              value={withdrawalSchedule}
              disabled={busy}
              onChange={(e) =>
                setWithdrawalSchedule(e.target.value as 'daily' | 'weekly' | 'monthly')
              }
            >
              <option value="daily">daily</option>
              <option value="weekly">weekly</option>
              <option value="monthly">monthly</option>
            </select>
          </>
        )}

        {voteType === 'upgrade' && (
          <>
            <label htmlFor="po-governance-capex">Upgrade capex (credits)</label>
            <input
              id="po-governance-capex"
              data-testid="po-governance-capex"
              type="number"
              min={500001}
              step={1}
              value={upgradeCapex}
              disabled={busy}
              onChange={(e) => setUpgradeCapex(Number(e.target.value))}
            />
          </>
        )}

        {needsConfirm && !confirmArmed && (
          <button
            type="button"
            className="action-button"
            data-testid="po-governance-arm"
            disabled={busy}
            onClick={() => setConfirmArmed(true)}
          >
            Arm {voteType} vote
          </button>
        )}

        {needsConfirm && confirmArmed && (
          <button
            type="button"
            className="action-button"
            data-testid="po-governance-cancel-arm"
            disabled={busy}
            onClick={() => setConfirmArmed(false)}
          >
            Cancel arm
          </button>
        )}

        <button
          type="button"
          className="action-button primary"
          data-testid="po-governance-cast"
          disabled={busy || (needsConfirm && !confirmArmed)}
          onClick={() => void cast()}
        >
          {busy ? 'Casting…' : 'Cast vote'}
        </button>
      </div>

      {msg && (
        <div
          className={msg.ok ? 'genesis-success-message' : 'genesis-error-message'}
          role={msg.ok ? 'status' : 'alert'}
          data-testid="po-governance-msg"
        >
          {msg.ok ? '✅' : '❌'} {msg.text}
        </div>
      )}
    </div>
  );
};

export default PortOfficeGovernancePanel;
