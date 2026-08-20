import React, { useCallback, useState } from 'react';
import { api } from '../../utils/auth';
import { useToast, useConfirm } from '../../contexts/ToastContext';
import './bounty-admin-panel.css';

/**
 * LEG-331 — admin bounty force-cancel / collapse / faction-bounty controls.
 * Canon: sw2102-docs/FEATURES/gameplay/bounties.md (shipped ECONOMY_INTERVENE routes).
 */

interface PlayerBountyEntry {
  id: string;
  placed_by?: string;
  placed_by_name?: string;
  amount?: number;
  type?: string;
  reason?: string;
  placed_at?: string;
}

interface SystemBountyEntry {
  amount?: number;
  reason?: string;
  type?: string;
}

interface BountyListResponse {
  success: boolean;
  target_id: string;
  target_name?: string;
  player_bounties: PlayerBountyEntry[];
  system_bounties: SystemBountyEntry[];
  total_value: number;
  message?: string;
}

const FACTION_TYPES = [
  'Federation',
  'Independents',
  'Pirates',
  'Merchants',
  'Explorers',
  'Military',
  'Mining',
  'Outlaws',
  'Syndicate',
  'Concord',
];

function detailFromErr(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
  return typeof detail === 'string' ? detail : fallback;
}

const BountyAdminPanel: React.FC = () => {
  const toast = useToast();
  const confirm = useConfirm();

  const [targetId, setTargetId] = useState('');
  const [list, setList] = useState<BountyListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [mutating, setMutating] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);

  const [npcId, setNpcId] = useState('');
  const [factionType, setFactionType] = useState(FACTION_TYPES[0]);
  const [factionAmount, setFactionAmount] = useState('1000');
  const [factionReason, setFactionReason] = useState('');

  const loadBounties = useCallback(async () => {
    const id = targetId.trim();
    if (!id) {
      toast.error('Enter a target player UUID');
      return;
    }
    setLoading(true);
    setListError(null);
    try {
      const { data } = await api.get<BountyListResponse>(
        `/api/v1/admin/players/${encodeURIComponent(id)}/bounties`
      );
      setList(data);
    } catch (err: unknown) {
      setList(null);
      setListError(detailFromErr(err, 'Failed to load bounties'));
    } finally {
      setLoading(false);
    }
  }, [targetId, toast]);

  const forceCancel = async (bountyId: string) => {
    const id = targetId.trim();
    if (!id) return;
    const ok = await confirm({
      title: 'Force-cancel bounty',
      message: `Refund placer principal (fee non-refundable) and remove bounty ${bountyId}?`,
      confirmLabel: 'Force-cancel',
      danger: true,
    });
    if (!ok) return;
    setMutating(bountyId);
    try {
      await api.post(
        `/api/v1/admin/players/${encodeURIComponent(id)}/bounties/${encodeURIComponent(bountyId)}/force-cancel`
      );
      toast.success('Bounty force-cancelled');
      await loadBounties();
    } catch (err: unknown) {
      toast.error(detailFromErr(err, 'Force-cancel failed'));
    } finally {
      setMutating(null);
    }
  };

  const collapse = async () => {
    const id = targetId.trim();
    if (!id) return;
    const ok = await confirm({
      title: 'Collapse excess bounties',
      message:
        'Merge older entries over the soft cap (50) per placer. No credits move. Continue?',
      confirmLabel: 'Collapse',
    });
    if (!ok) return;
    setMutating('collapse');
    try {
      const { data } = await api.post<{
        collapsed?: number;
        entry_count?: number;
        message?: string;
      }>(`/api/v1/admin/players/${encodeURIComponent(id)}/bounties/collapse`);
      toast.success(
        `Collapsed ${data?.collapsed ?? 0} · ${data?.entry_count ?? '—'} entries remain`
      );
      await loadBounties();
    } catch (err: unknown) {
      toast.error(detailFromErr(err, 'Collapse failed'));
    } finally {
      setMutating(null);
    }
  };

  const placeFactionBounty = async () => {
    const id = npcId.trim();
    const amount = parseInt(factionAmount, 10);
    const reason = factionReason.trim();
    if (!id) {
      toast.error('Enter an NPC UUID');
      return;
    }
    if (!Number.isFinite(amount) || amount < 1000) {
      toast.error('Amount must be an integer ≥ 1000');
      return;
    }
    if (!reason) {
      toast.error('Reason is required');
      return;
    }
    setMutating('faction');
    try {
      await api.post(`/api/v1/admin/npcs/${encodeURIComponent(id)}/faction-bounty`, {
        faction_type: factionType,
        amount,
        reason,
      });
      toast.success('Faction bounty placed');
      setFactionReason('');
    } catch (err: unknown) {
      toast.error(detailFromErr(err, 'Faction bounty failed'));
    } finally {
      setMutating(null);
    }
  };

  const playerBounties = list?.player_bounties ?? [];

  return (
    <section className="section bounty-admin-panel" data-testid="bounty-admin-panel">
      <div className="section-header">
        <div>
          <h3 className="section-title">Bounty admin tooling</h3>
          <p className="section-subtitle">
            Force-cancel / collapse player bounties · place NPC faction bounties (ECONOMY_INTERVENE)
          </p>
        </div>
      </div>

      <div className="bounty-admin-block">
        <h4>Player bounties</h4>
        <div className="bounty-admin-row">
          <label htmlFor="bounty-target-id">Target player UUID</label>
          <input
            id="bounty-target-id"
            type="text"
            value={targetId}
            onChange={(e) => setTargetId(e.target.value)}
            placeholder="player UUID"
            autoComplete="off"
          />
          <button
            type="button"
            className="btn btn-sm btn-primary"
            disabled={loading}
            onClick={() => void loadBounties()}
          >
            {loading ? 'Loading…' : 'Load'}
          </button>
          <button
            type="button"
            className="btn btn-sm"
            disabled={!targetId.trim() || mutating === 'collapse'}
            onClick={() => void collapse()}
          >
            Collapse excess
          </button>
        </div>

        {listError && (
          <div className="alert alert-error" role="alert">
            {listError}
          </div>
        )}

        {list && (
          <p className="bounty-admin-meta">
            {list.target_name || '—'} · total value {list.total_value?.toLocaleString?.() ?? list.total_value}{' '}
            · {playerBounties.length} player-placed · {list.system_bounties?.length ?? 0} system
          </p>
        )}

        {list && playerBounties.length === 0 && (
          <p className="bounty-admin-muted">No player-placed bounties on this target.</p>
        )}

        {playerBounties.length > 0 && (
          <div className="levers-table-wrap">
            <table className="levers-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Placer</th>
                  <th>Amount</th>
                  <th>Type</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {playerBounties.map((b) => (
                  <tr key={b.id}>
                    <td className="font-mono text-xs">{b.id}</td>
                    <td>
                      {b.placed_by_name || '—'}
                      <div className="font-mono text-xs bounty-admin-muted">{b.placed_by}</div>
                    </td>
                    <td>{b.amount?.toLocaleString?.() ?? b.amount ?? '—'}</td>
                    <td>{b.type || 'player'}</td>
                    <td>
                      <button
                        type="button"
                        className="btn btn-sm btn-danger"
                        disabled={mutating === b.id || b.type === 'system'}
                        onClick={() => void forceCancel(b.id)}
                      >
                        Force-cancel
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="bounty-admin-block">
        <h4>NPC faction bounty</h4>
        <div className="bounty-admin-grid">
          <label htmlFor="faction-npc-id">
            NPC UUID
            <input
              id="faction-npc-id"
              type="text"
              value={npcId}
              onChange={(e) => setNpcId(e.target.value)}
              placeholder="npc UUID"
            />
          </label>
          <label htmlFor="faction-type">
            Faction
            <select
              id="faction-type"
              value={factionType}
              onChange={(e) => setFactionType(e.target.value)}
            >
              {FACTION_TYPES.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </select>
          </label>
          <label htmlFor="faction-amount">
            Amount (≥ 1000)
            <input
              id="faction-amount"
              type="number"
              min={1000}
              step={1}
              value={factionAmount}
              onChange={(e) => setFactionAmount(e.target.value)}
            />
          </label>
          <label htmlFor="faction-reason" className="bounty-admin-span">
            Reason
            <input
              id="faction-reason"
              type="text"
              maxLength={200}
              value={factionReason}
              onChange={(e) => setFactionReason(e.target.value)}
            />
          </label>
        </div>
        <button
          type="button"
          className="btn btn-primary"
          disabled={mutating === 'faction'}
          onClick={() => void placeFactionBounty()}
        >
          Place faction bounty
        </button>
      </div>
    </section>
  );
};

export default BountyAdminPanel;
