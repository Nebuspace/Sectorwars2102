import React, { useCallback, useEffect, useState } from 'react';
import { useGame } from '../../contexts/GameContext';
import { portOwnershipAPI } from '../../services/api';
import { formatCredits } from '../../utils/formatters';

type SyndicateShare = { player_id: string; pct: number };
type SyndicateInvite = {
  invite_id: string;
  invitee_player_id: string;
  pct: number;
  expires_at?: string;
};

type StakeTransferProposal = {
  proposal_id: string;
  from_player_id: string;
  to_player_id: string;
  pct: number;
  status: string;
  remaining_stake_pct?: number;
  approving_weight?: number;
  approvals?: Array<{ player_id: string; at?: string }>;
};

type SyndicateStatus = {
  station_id: string;
  owner_id: string | null;
  mode: string;
  shares: SyndicateShare[];
  pending_invites: SyndicateInvite[];
  is_primary: boolean;
};

function parseProposal(raw: unknown): StakeTransferProposal | null {
  const r = asRecord(raw);
  if (!r || typeof r.proposal_id !== 'string') return null;
  if (typeof r.from_player_id !== 'string' || typeof r.to_player_id !== 'string') return null;
  const pct = typeof r.pct === 'number' ? r.pct : Number(r.pct);
  if (!Number.isFinite(pct)) return null;
  const approvalsRaw = Array.isArray(r.approvals) ? r.approvals : [];
  const approvals = approvalsRaw
    .map((row): { player_id: string; at?: string } | null => {
      const a = asRecord(row);
      if (!a || typeof a.player_id !== 'string') return null;
      const entry: { player_id: string; at?: string } = { player_id: a.player_id };
      if (typeof a.at === 'string') entry.at = a.at;
      return entry;
    })
    .filter((a): a is { player_id: string; at?: string } => a !== null);
  return {
    proposal_id: r.proposal_id,
    from_player_id: r.from_player_id,
    to_player_id: r.to_player_id,
    pct,
    status: typeof r.status === 'string' ? r.status : 'pending',
    remaining_stake_pct:
      typeof r.remaining_stake_pct === 'number' ? r.remaining_stake_pct : undefined,
    approving_weight: typeof r.approving_weight === 'number' ? r.approving_weight : undefined,
    approvals,
  };
}

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

/** Same densify contract as formatPortOfficeVenueError (403/429/TypeError). */
export function formatSyndicateError(error: unknown, fallback: string): string {
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
    return 'You do not have permission to perform this syndicate action.';
  }
  if (status === 429) {
    return 'Syndicate action rate limit exceeded — wait a moment and try again.';
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
  const invitesRaw = Array.isArray(body.pending_invites) ? body.pending_invites : [];
  const shares: SyndicateShare[] = sharesRaw
    .map((row) => {
      const r = asRecord(row);
      if (!r || typeof r.player_id !== 'string') return null;
      const pct = typeof r.pct === 'number' ? r.pct : Number(r.pct);
      if (!Number.isFinite(pct)) return null;
      return { player_id: r.player_id, pct };
    })
    .filter((s): s is SyndicateShare => s !== null);
  const pending_invites: SyndicateInvite[] = invitesRaw
    .map((row): SyndicateInvite | null => {
      const r = asRecord(row);
      if (!r || typeof r.invite_id !== 'string' || typeof r.invitee_player_id !== 'string') {
        return null;
      }
      const pct = typeof r.pct === 'number' ? r.pct : Number(r.pct);
      if (!Number.isFinite(pct)) return null;
      const invite: SyndicateInvite = {
        invite_id: r.invite_id,
        invitee_player_id: r.invitee_player_id,
        pct,
      };
      if (typeof r.expires_at === 'string') {
        invite.expires_at = r.expires_at;
      }
      return invite;
    })
    .filter((i): i is SyndicateInvite => i !== null);
  return {
    station_id: typeof body.station_id === 'string' ? body.station_id : '',
    owner_id: typeof body.owner_id === 'string' ? body.owner_id : null,
    mode: typeof body.mode === 'string' ? body.mode : 'solo',
    shares,
    pending_invites,
    is_primary: body.is_primary === true,
  };
}

export interface PortOfficeSyndicatePanelProps {
  stationId: string;
  stationName: string;
}

/**
 * Port Office co-ownership syndicate — tip GS /syndicate* (LEG-4117) + stake-transfer (LEG-4237).
 * invent=0: stake ledger, invite/accept/decline, buyout, propose/approve/reject stake transfer.
 * Pending stake-transfers tracked from propose/approve responses only (GET enrichment = LEG-4238).
 */
const PortOfficeSyndicatePanel: React.FC<PortOfficeSyndicatePanelProps> = ({
  stationId,
  stationName,
}) => {
  const { playerState, refreshPlayerState } = useGame();
  const playerId = playerState?.id ? String(playerState.id) : null;

  const [status, setStatus] = useState<SyndicateStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const [inviteeId, setInviteeId] = useState('');
  const [invitePct, setInvitePct] = useState(10);
  const [buyoutArmed, setBuyoutArmed] = useState(false);

  const [xferTargetId, setXferTargetId] = useState('');
  const [xferPct, setXferPct] = useState(10);
  const [localTransfers, setLocalTransfers] = useState<StakeTransferProposal[]>([]);

  const upsertLocalTransfer = useCallback((proposal: StakeTransferProposal) => {
    setLocalTransfers((prev) => {
      const rest = prev.filter((p) => p.proposal_id !== proposal.proposal_id);
      if (proposal.status !== 'pending') return rest;
      return [...rest, proposal];
    });
  }, []);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const raw = await portOwnershipAPI.getSyndicateStatus(stationId);
      setStatus(parseStatus(raw));
    } catch (e: unknown) {
      setStatus(null);
      setError(
        formatSyndicateError(e, 'Syndicate status feed is down. Please try again.'),
      );
    } finally {
      setLoading(false);
    }
  }, [stationId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    setLocalTransfers([]);
    setXferTargetId('');
    setXferPct(10);
  }, [stationId]);

  const isShareholder =
    !!playerId &&
    !!status &&
    status.shares.some((s) => String(s.player_id) === String(playerId));
  const myPending =
    status?.pending_invites.filter(
      (i) => playerId && String(i.invitee_player_id) === String(playerId),
    ) ?? [];
  const showPanel =
    !!status &&
    (status.is_primary || status.mode === 'syndicate' || status.pending_invites.length > 0);

  const run = async (key: string, action: () => Promise<unknown>, okText: string) => {
    if (busy) return;
    setBusy(key);
    setMsg(null);
    try {
      const result = await action();
      const body = asRecord(result);
      const proposal = parseProposal(body?.proposal);
      if (proposal) upsertLocalTransfer(proposal);
      const serverMsg = typeof body?.message === 'string' ? body.message : okText;
      let extra = '';
      if (typeof body?.fair_value === 'number' && typeof body?.total_payout === 'number') {
        extra = ` Fair value ${formatCredits(body.fair_value)}; total payout ${formatCredits(body.total_payout)}.`;
      }
      if (proposal?.status === 'applied') {
        extra = `${extra} Stake transfer applied.`.trimStart();
      }
      setMsg({ ok: true, text: `${serverMsg}${extra}` });
      if (refreshPlayerState) await refreshPlayerState();
      await reload();
      setBuyoutArmed(false);
    } catch (e: unknown) {
      setMsg({
        ok: false,
        text: formatSyndicateError(e, 'Syndicate action failed. Please try again.'),
      });
    } finally {
      setBusy(null);
    }
  };

  if (loading && !status) {
    return (
      <div className="po-section" data-testid="po-syndicate-panel">
        <h3 className="po-section-title">🤝 Co-Ownership Syndicate</h3>
        <div className="catalog-loading">Loading syndicate ledger...</div>
      </div>
    );
  }

  if (error && !status) {
    return (
      <div className="po-section" data-testid="po-syndicate-panel">
        <h3 className="po-section-title">🤝 Co-Ownership Syndicate</h3>
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

  if (!showPanel || !status) return null;

  return (
    <div className="po-section" data-testid="po-syndicate-panel">
      <h3 className="po-section-title">🤝 Co-Ownership Syndicate</h3>
      <p className="section-description">
        Mode: <strong>{status.mode}</strong> at {stationName}. Stake ledger, share invites, and
        stake-transfer proposals — no sale votes or upgrade catalog here.
      </p>

      <div className="po-defense-grid" data-testid="po-syndicate-shares">
        {status.shares.map((s) => (
          <div key={s.player_id} className="po-defense-field" data-testid={`po-syndicate-share-${s.player_id}`}>
            <span>
              Stake {s.pct}% — {String(s.player_id) === String(playerId) ? 'you' : s.player_id.slice(0, 8)}
            </span>
          </div>
        ))}
        {status.shares.length === 0 && (
          <div className="po-defense-field">No stakes on the ledger yet.</div>
        )}
      </div>

      {status.is_primary && (
        <div className="po-bid-row" data-testid="po-syndicate-invite-form">
          <label htmlFor="po-syndicate-invitee">Invitee player UUID</label>
          <input
            id="po-syndicate-invitee"
            data-testid="po-syndicate-invitee"
            type="text"
            value={inviteeId}
            onChange={(e) => setInviteeId(e.target.value.trim())}
            placeholder="player UUID"
            disabled={Boolean(busy)}
          />
          <label htmlFor="po-syndicate-pct">Share %</label>
          <input
            id="po-syndicate-pct"
            data-testid="po-syndicate-pct"
            type="number"
            min={1}
            max={99}
            value={invitePct}
            onChange={(e) => setInvitePct(Math.max(1, Math.min(99, Number(e.target.value) || 1)))}
            disabled={Boolean(busy)}
          />
          <button
            type="button"
            className="action-button primary"
            data-testid="po-syndicate-invite-submit"
            disabled={Boolean(busy) || !inviteeId}
            onClick={() =>
              void run(
                'invite',
                () => portOwnershipAPI.inviteShare(stationId, inviteeId, invitePct),
                `Share invite ${invitePct}% issued.`,
              )
            }
          >
            {busy === 'invite' ? 'Issuing...' : 'Issue Share Invite'}
          </button>
        </div>
      )}

      {status.pending_invites.length > 0 && (
        <div data-testid="po-syndicate-pending">
          <h4 className="po-section-title">Pending invites</h4>
          {status.pending_invites.map((inv) => {
            const mine = playerId && String(inv.invitee_player_id) === String(playerId);
            return (
              <div className="po-bid-row" key={inv.invite_id} data-testid={`po-syndicate-invite-${inv.invite_id}`}>
                <span>
                  {inv.pct}% → {inv.invitee_player_id.slice(0, 8)}
                  {inv.expires_at ? ` (expires ${inv.expires_at})` : ''}
                </span>
                {mine && (
                  <>
                    <button
                      type="button"
                      className="action-button primary"
                      data-testid={`po-syndicate-accept-${inv.invite_id}`}
                      disabled={Boolean(busy)}
                      onClick={() =>
                        void run(
                          `accept-${inv.invite_id}`,
                          () => portOwnershipAPI.acceptShareInvite(stationId, inv.invite_id),
                          'Share invite accepted.',
                        )
                      }
                    >
                      {busy === `accept-${inv.invite_id}` ? 'Accepting...' : 'Accept'}
                    </button>
                    <button
                      type="button"
                      className="action-button"
                      data-testid={`po-syndicate-decline-${inv.invite_id}`}
                      disabled={Boolean(busy)}
                      onClick={() =>
                        void run(
                          `decline-${inv.invite_id}`,
                          () => portOwnershipAPI.declineShareInvite(stationId, inv.invite_id),
                          'Share invite declined.',
                        )
                      }
                    >
                      {busy === `decline-${inv.invite_id}` ? 'Declining...' : 'Decline'}
                    </button>
                  </>
                )}
              </div>
            );
          })}
        </div>
      )}

      {status.mode === 'syndicate' && isShareholder && (
        <div className="po-bid-row" data-testid="po-syndicate-xfer-form">
          <label htmlFor="po-syndicate-xfer-target">Transfer stake to player UUID</label>
          <input
            id="po-syndicate-xfer-target"
            data-testid="po-syndicate-xfer-target"
            type="text"
            value={xferTargetId}
            onChange={(e) => setXferTargetId(e.target.value.trim())}
            placeholder="player UUID"
            disabled={Boolean(busy)}
          />
          <label htmlFor="po-syndicate-xfer-pct">Transfer %</label>
          <input
            id="po-syndicate-xfer-pct"
            data-testid="po-syndicate-xfer-pct"
            type="number"
            min={1}
            max={99}
            value={xferPct}
            onChange={(e) => setXferPct(Math.max(1, Math.min(99, Number(e.target.value) || 1)))}
            disabled={Boolean(busy)}
          />
          <button
            type="button"
            className="action-button primary"
            data-testid="po-syndicate-xfer-submit"
            disabled={Boolean(busy) || !xferTargetId}
            onClick={() =>
              void run(
                'xfer-propose',
                () => portOwnershipAPI.proposeStakeTransfer(stationId, xferTargetId, xferPct),
                `Stake transfer ${xferPct}% proposed.`,
              )
            }
          >
            {busy === 'xfer-propose' ? 'Proposing...' : 'Propose Stake Transfer'}
          </button>
        </div>
      )}

      {localTransfers.length > 0 && (
        <div data-testid="po-syndicate-xfer-pending">
          <h4 className="po-section-title">Pending stake transfers</h4>
          {localTransfers.map((xfer) => {
            const alreadyApproved =
              !!playerId &&
              (xfer.approvals ?? []).some((a) => String(a.player_id) === String(playerId));
            return (
              <div
                className="po-bid-row"
                key={xfer.proposal_id}
                data-testid={`po-syndicate-xfer-${xfer.proposal_id}`}
              >
                <span>
                  {xfer.pct}% {xfer.from_player_id.slice(0, 8)} → {xfer.to_player_id.slice(0, 8)}
                  {typeof xfer.approving_weight === 'number' &&
                  typeof xfer.remaining_stake_pct === 'number'
                    ? ` (weight ${xfer.approving_weight}/${xfer.remaining_stake_pct})`
                    : ''}
                </span>
                {isShareholder && !alreadyApproved && (
                  <button
                    type="button"
                    className="action-button primary"
                    data-testid={`po-syndicate-xfer-approve-${xfer.proposal_id}`}
                    disabled={Boolean(busy)}
                    onClick={() =>
                      void run(
                        `xfer-approve-${xfer.proposal_id}`,
                        () =>
                          portOwnershipAPI.approveStakeTransfer(stationId, xfer.proposal_id),
                        'Stake transfer approved.',
                      )
                    }
                  >
                    {busy === `xfer-approve-${xfer.proposal_id}` ? 'Approving...' : 'Approve'}
                  </button>
                )}
                {isShareholder && (
                  <button
                    type="button"
                    className="action-button"
                    data-testid={`po-syndicate-xfer-reject-${xfer.proposal_id}`}
                    disabled={Boolean(busy)}
                    onClick={() =>
                      void run(
                        `xfer-reject-${xfer.proposal_id}`,
                        () =>
                          portOwnershipAPI.rejectStakeTransfer(stationId, xfer.proposal_id),
                        'Stake transfer rejected.',
                      )
                    }
                  >
                    {busy === `xfer-reject-${xfer.proposal_id}` ? 'Rejecting...' : 'Reject'}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}

      {status.mode === 'syndicate' && isShareholder && (
        <div className="po-bid-row" data-testid="po-syndicate-buyout">
          {!buyoutArmed ? (
            <button
              type="button"
              className="action-button"
              data-testid="po-syndicate-buyout-arm"
              disabled={Boolean(busy)}
              onClick={() => setBuyoutArmed(true)}
            >
              Buy out co-owners…
            </button>
          ) : (
            <>
              <p className="section-description" data-testid="po-syndicate-buyout-confirm">
                Confirm buyout at fair value for all other shareholders. Credits leave your
                account immediately; mode reverts to solo. This cannot be undone from this desk.
              </p>
              <button
                type="button"
                className="action-button primary"
                data-testid="po-syndicate-buyout-confirm-btn"
                disabled={Boolean(busy)}
                onClick={() =>
                  void run(
                    'buyout',
                    () => portOwnershipAPI.syndicateBuyout(stationId),
                    'Syndicate buyout complete.',
                  )
                }
              >
                {busy === 'buyout' ? 'Buying out...' : 'Confirm Buyout'}
              </button>
              <button
                type="button"
                className="action-button"
                data-testid="po-syndicate-buyout-cancel"
                disabled={Boolean(busy)}
                onClick={() => setBuyoutArmed(false)}
              >
                Cancel
              </button>
            </>
          )}
        </div>
      )}

      {msg && (
        <div
          className={msg.ok ? 'genesis-success-message' : 'genesis-error-message'}
          role="status"
          data-testid="po-syndicate-msg"
        >
          <span className={msg.ok ? 'success-icon' : 'error-icon'}>{msg.ok ? '✅' : '❌'}</span>
          {msg.text}
        </div>
      )}
    </div>
  );
};

export default PortOfficeSyndicatePanel;
