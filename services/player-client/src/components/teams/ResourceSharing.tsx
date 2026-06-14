import React, { useState, useEffect, useCallback } from 'react';
import { teamAPI } from '../../services/api';
import type { TeamMember, TreasuryBalanceApiResponse } from '../../types/team';
import './resource-sharing.css';

interface ResourceSharingProps {
  teamId: string;
  playerId: string;
  /** Already-mapped roster from TeamManager (camelCase) */
  members: TeamMember[];
  /** Player's spendable credits (from GameContext) — the only personal balance the API exposes */
  playerCredits: number;
  /** Backend gates withdraw and transfer on can_manage_treasury */
  canManageTreasury: boolean;
  /** Called after a successful op so the parent can refresh team + player state */
  onChanged?: () => void;
}

// Backend whitelist: PLAYER_TRANSFERABLE_RESOURCES = {credits, quantum_crystals}.
// Other treasury columns exist but are server-fed; players can't move them.
const TRANSFERABLE: Array<{ key: 'credits' | 'quantum_crystals'; label: string }> = [
  { key: 'credits', label: 'Credits' },
  { key: 'quantum_crystals', label: 'Quantum Crystals' }
];

// Read-only resources surfaced when the treasury holds them (loot, etc.).
const READ_ONLY_KEYS: Array<keyof TreasuryBalanceApiResponse> = [
  'fuel', 'organics', 'equipment', 'technology', 'luxury_items',
  'precious_metals', 'raw_materials', 'plasma', 'bio_samples', 'dark_matter'
];

const LABELS: Record<string, string> = {
  fuel: 'Fuel', organics: 'Organics', equipment: 'Equipment', technology: 'Technology',
  luxury_items: 'Luxury Items', precious_metals: 'Precious Metals', raw_materials: 'Raw Materials',
  plasma: 'Plasma', bio_samples: 'Bio Samples', dark_matter: 'Dark Matter'
};

type Operation = 'deposit' | 'withdraw' | 'transfer';

export const ResourceSharing: React.FC<ResourceSharingProps> = ({
  teamId,
  playerId,
  members,
  playerCredits,
  canManageTreasury,
  onChanged
}) => {
  const [balance, setBalance] = useState<TreasuryBalanceApiResponse | null>(null);
  const [operation, setOperation] = useState<Operation>('deposit');
  const [resource, setResource] = useState<'credits' | 'quantum_crystals'>('credits');
  const [amount, setAmount] = useState('');
  const [recipient, setRecipient] = useState('');
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null);

  const recipients = members.filter(m => m.playerId !== playerId);

  const loadBalance = useCallback(async () => {
    try {
      const data = await teamAPI.getTreasuryBalance(teamId) as TreasuryBalanceApiResponse;
      setBalance(data);
    } catch (error) {
      console.error('Failed to load treasury balance:', error);
    }
  }, [teamId]);

  useEffect(() => {
    void loadBalance();
  }, [loadBalance]);

  // Members can deposit; only treasury managers can withdraw or transfer.
  const allowedOps: Operation[] = canManageTreasury ? ['deposit', 'withdraw', 'transfer'] : ['deposit'];

  // "Available" depends on the direction of the flow.
  const available = (): number | null => {
    if (operation === 'deposit') {
      return resource === 'credits' ? playerCredits : null; // personal QC balance not exposed by API
    }
    // withdraw / transfer pull from the treasury
    return balance ? balance[resource] : null;
  };

  const handleSubmit = async () => {
    setStatus(null);
    const amt = parseInt(amount, 10);
    if (!Number.isFinite(amt) || amt <= 0) {
      setStatus({ kind: 'err', text: 'Enter an amount greater than zero.' });
      return;
    }
    if (operation === 'transfer' && !recipient) {
      setStatus({ kind: 'err', text: 'Select a member to transfer to.' });
      return;
    }
    const avail = available();
    if (avail !== null && amt > avail) {
      setStatus({ kind: 'err', text: `Only ${avail.toLocaleString()} available.` });
      return;
    }

    setLoading(true);
    try {
      const label = TRANSFERABLE.find(t => t.key === resource)!.label;
      if (operation === 'deposit') {
        await teamAPI.depositToTreasury(teamId, resource, amt);
        setStatus({ kind: 'ok', text: `Deposited ${amt.toLocaleString()} ${label} to the treasury.` });
      } else if (operation === 'withdraw') {
        await teamAPI.withdrawFromTreasury(teamId, resource, amt);
        setStatus({ kind: 'ok', text: `Withdrew ${amt.toLocaleString()} ${label} from the treasury.` });
      } else {
        await teamAPI.transferTreasury(teamId, recipient, resource, amt);
        setStatus({ kind: 'ok', text: `Transferred ${amt.toLocaleString()} ${label} to ${recipient}.` });
      }
      setAmount('');
      await loadBalance();
      onChanged?.();
    } catch (error) {
      setStatus({ kind: 'err', text: error instanceof Error ? error.message : 'Operation failed.' });
    } finally {
      setLoading(false);
    }
  };

  const avail = available();

  return (
    <div className="resource-sharing">
      <div className="sharing-header">
        <h3>Team Treasury</h3>
        <p>Pool resources for the team. Deposits are open to all members; withdrawals and transfers require treasury permission.</p>
      </div>

      <div className="treasury-balance">
        {TRANSFERABLE.map(t => (
          <div key={t.key} className="resource-item">
            <label>{t.label}</label>
            <span className="amount">{balance ? balance[t.key].toLocaleString() : '—'}</span>
          </div>
        ))}
        {balance && READ_ONLY_KEYS.filter(k => typeof balance[k] === 'number' && (balance[k] as number) > 0).map(k => (
          <div key={k} className="resource-item readonly">
            <label>{LABELS[k as string] ?? String(k)}</label>
            <span className="amount">{(balance[k] as number).toLocaleString()}</span>
          </div>
        ))}
      </div>

      <div className="transfer-type-selector">
        {allowedOps.map(op => (
          <button
            key={op}
            className={operation === op ? 'active' : ''}
            onClick={() => { setOperation(op); setStatus(null); }}
          >
            {op === 'deposit' ? 'Deposit' : op === 'withdraw' ? 'Withdraw' : 'Transfer to Member'}
          </button>
        ))}
      </div>

      {operation === 'transfer' && (
        <div className="member-selector">
          <h4>Recipient</h4>
          {recipients.length === 0 ? (
            <p className="field-note">No other members to transfer to.</p>
          ) : (
            <div className="member-list">
              {recipients.map(member => (
                <div
                  key={member.id}
                  className={`member-option ${recipient === member.playerName ? 'selected' : ''}`}
                  onClick={() => setRecipient(member.playerName)}
                >
                  <span className="member-name">{member.playerName}</span>
                  <span className={`role-badge ${member.role}`}>{member.role}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="resource-inputs">
        <div className="resource-grid">
          <div className="resource-input">
            <label>Resource</label>
            <select value={resource} onChange={(e) => setResource(e.target.value as 'credits' | 'quantum_crystals')}>
              {TRANSFERABLE.map(t => <option key={t.key} value={t.key}>{t.label}</option>)}
            </select>
          </div>
          <div className="resource-input">
            <label>
              Amount
              {avail !== null && <span className="available"> ({avail.toLocaleString()} available)</span>}
            </label>
            <input
              type="number"
              min="0"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              placeholder="0"
            />
          </div>
        </div>

        {status && (
          <div className={status.kind === 'ok' ? 'success-message' : 'form-error'} role="alert">
            {status.text}
          </div>
        )}

        <button
          className="transfer-button"
          onClick={handleSubmit}
          disabled={loading || !amount || (operation === 'transfer' && !recipient)}
        >
          {loading ? 'Processing…'
            : operation === 'deposit' ? 'Deposit to Treasury'
            : operation === 'withdraw' ? 'Withdraw from Treasury'
            : 'Transfer to Member'}
        </button>
      </div>
    </div>
  );
};
