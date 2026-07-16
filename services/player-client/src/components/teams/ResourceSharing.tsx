import React, { useState, useEffect, useCallback } from 'react';
import { teamAPI } from '../../services/api';
import type { TeamMember, TreasuryBalanceApiResponse, TreasuryTransactionApiResponse } from '../../types/team';
import { useResourceCatalog } from '../../hooks/useResourceCatalog';
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
const TRANSFERABLE: Array<'credits' | 'quantum_crystals'> = ['credits', 'quantum_crystals'];

// Read-only resources surfaced when the treasury holds them (loot, etc.). The
// Team treasury is its OWN column schema (models/team.py treasury_*), not the
// resource registry — most of these keys (technology, luxury_items,
// precious_metals, raw_materials, plasma, bio_samples, dark_matter) have no
// registry row at all; `fuel`/`organics`/`equipment`/`quantum_crystals` do.
// Every label below is sourced through useResourceCatalog().getLabel, which
// returns the registry label where one exists and otherwise prettifies the
// key — verified byte-identical to this file's old local label table.
const READ_ONLY_KEYS: Array<keyof TreasuryBalanceApiResponse> = [
  'fuel', 'organics', 'equipment', 'technology', 'luxury_items',
  'precious_metals', 'raw_materials', 'plasma', 'bio_samples', 'dark_matter'
];

// How each ledger kind reads + which sign to show on the amount.
const KIND_META: Record<string, { label: string; sign: '+' | '−' | '' }> = {
  deposit: { label: 'Deposit', sign: '+' },
  withdraw: { label: 'Withdraw', sign: '−' },
  transfer: { label: 'Transfer Out', sign: '−' },
  tax: { label: 'Tax', sign: '−' },
  payout: { label: 'Payout', sign: '−' }
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
  const { getLabel } = useResourceCatalog();
  const [balance, setBalance] = useState<TreasuryBalanceApiResponse | null>(null);
  const [operation, setOperation] = useState<Operation>('deposit');
  const [resource, setResource] = useState<'credits' | 'quantum_crystals'>('credits');
  const [amount, setAmount] = useState('');
  const [recipient, setRecipient] = useState('');
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null);
  const [history, setHistory] = useState<TreasuryTransactionApiResponse[] | null>(null);

  const recipients = members.filter(m => m.playerId !== playerId);

  const loadBalance = useCallback(async () => {
    try {
      const data = await teamAPI.getTreasuryBalance(teamId) as TreasuryBalanceApiResponse;
      setBalance(data);
    } catch (error) {
      console.error('Failed to load treasury balance:', error);
    }
  }, [teamId]);

  const loadHistory = useCallback(async () => {
    try {
      const data = await teamAPI.getTreasuryHistory(teamId) as TreasuryTransactionApiResponse[];
      setHistory(data);
    } catch (error) {
      console.error('Failed to load treasury history:', error);
    }
  }, [teamId]);

  useEffect(() => {
    void loadBalance();
    void loadHistory();
  }, [loadBalance, loadHistory]);

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
      const label = getLabel(resource);
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
      await loadHistory();
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
        {TRANSFERABLE.map(key => (
          <div key={key} className="resource-item">
            <label>{getLabel(key)}</label>
            <span className="amount">{balance ? balance[key].toLocaleString() : '—'}</span>
          </div>
        ))}
        {balance && READ_ONLY_KEYS.filter(k => typeof balance[k] === 'number' && (balance[k] as number) > 0).map(k => (
          <div key={k} className="resource-item readonly">
            <label>{getLabel(k as string)}</label>
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
                  role="button"
                  tabIndex={0}
                  aria-pressed={recipient === member.playerName}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      setRecipient(member.playerName);
                    }
                  }}
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
              {TRANSFERABLE.map(key => <option key={key} value={key}>{getLabel(key)}</option>)}
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

      <div className="treasury-history">
        <h4>Transaction History</h4>
        {history === null ? (
          <p className="field-note">Loading history…</p>
        ) : history.length === 0 ? (
          <p className="field-note">No treasury activity yet.</p>
        ) : (
          <table className="history-table">
            <thead>
              <tr>
                <th>When</th>
                <th>Action</th>
                <th>Resource</th>
                <th className="num">Amount</th>
                <th className="num">Balance</th>
                <th>By</th>
              </tr>
            </thead>
            <tbody>
              {history.map(tx => {
                const meta = KIND_META[tx.kind] ?? { label: tx.kind, sign: '' as const };
                return (
                  <tr key={tx.id}>
                    <td className="when">{tx.created_at ? new Date(tx.created_at).toLocaleString() : '—'}</td>
                    <td><span className={`kind-badge ${tx.kind}`}>{meta.label}</span></td>
                    <td>{getLabel(tx.resource_type)}</td>
                    <td className={`num ${meta.sign === '+' ? 'credit' : 'debit'}`}>
                      {meta.sign}{tx.amount.toLocaleString()}
                    </td>
                    <td className="num">{tx.balance_after.toLocaleString()}</td>
                    <td>{tx.actor_name ?? 'Unknown'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
};
