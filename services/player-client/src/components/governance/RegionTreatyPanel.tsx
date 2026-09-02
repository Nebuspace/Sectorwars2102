import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { regionOwnerAPI } from '../../services/api';
import type { OwnerTreaty } from '../../types/governance';
import './region-invite-panel.css';
import './region-treaty-panel.css';

/**
 * RegionTreatyPanel — owner treaty inbox (WO-ESCALATE-REGIONAL-TREATY-FLOW-PRIORITY).
 * Accept/reject/terminate against already-shipped regional_governance routes.
 * Primary actions (incoming proposed Accept|Reject) sit above the fold.
 */

interface RegionTreatyPanelProps {
  regionId: string;
  regionName?: string | null;
  onClose?: () => void;
}

const TREATY_TYPES = ['non_aggression', 'trade', 'defense', 'customs'] as const;

const mapTreatyErrCode = (msg: string): string | null => {
  switch (msg) {
    case 'ERR_REGION_NOT_FOUND':
      return 'Region not found.';
    case 'ERR_SAME_REGION_TREATY':
      return 'Cannot treaty with your own region.';
    case 'ERR_TREATY_ALREADY_EXISTS':
      return 'A treaty with that region already exists.';
    case 'ERR_TREATY_NOT_PROPOSED':
      return 'That treaty is no longer awaiting a decision.';
    case 'ERR_TREATY_NOT_ACTIVE':
      return 'That treaty is not active.';
    case 'ERR_NOT_REGION_OWNER':
      return 'You are not the owner of the required region.';
    default:
      return null;
  }
};

/** Transport collapse copy is not gameserver detail (LEG-3286 densify). */
const isNetworkCollapseMessage = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed)
  );
};

function httpStatus(err: unknown): number | undefined {
  if (err && typeof err === 'object') {
    const direct = (err as { status?: number }).status;
    if (typeof direct === 'number') return direct;
    const resp = (err as { response?: { status?: number } }).response;
    if (typeof resp?.status === 'number') return resp.status;
  }
  return undefined;
}

/** TypeError / network collapse → fallback; 403/429 + ERR_* densify (LEG-4018). */
export function formatRegionTreatyError(err: unknown, fallback: string): string {
  if (err instanceof TypeError) return fallback;
  const msg = err instanceof Error ? err.message : '';
  if (isNetworkCollapseMessage(msg)) return fallback;

  const status = httpStatus(err);
  const mapped = mapTreatyErrCode(msg);
  const hasServerDetail =
    msg.trim().length > 0 && !/^API Error: \d+$/.test(msg.trim());

  if (status === 403) {
    if (mapped) return mapped;
    if (hasServerDetail) return msg;
    return 'You do not have permission to manage region treaties.';
  }

  if (status === 429) {
    return 'Treaty action rate limit exceeded — wait a moment and try again.';
  }

  if (mapped) return mapped;
  return msg || fallback;
}

const partnerLabel = (t: OwnerTreaty, ownedName?: string | null): string => {
  const a = t.region_a_name || 'Region A';
  const b = t.region_b_name || 'Region B';
  if (ownedName && a === ownedName) return b;
  if (ownedName && b === ownedName) return a;
  return `${a} ↔ ${b}`;
};

const RegionTreatyPanel: React.FC<RegionTreatyPanelProps> = ({
  regionId,
  regionName,
  onClose,
}) => {
  const [treaties, setTreaties] = useState<OwnerTreaty[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [counterpartyId, setCounterpartyId] = useState('');
  const [treatyType, setTreatyType] = useState<string>(TREATY_TYPES[0]);
  const [proposing, setProposing] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await regionOwnerAPI.listMyTreaties(regionId);
      setTreaties(Array.isArray(data) ? data : []);
    } catch (err: any) {
      setError(formatRegionTreatyError(err, 'Failed to load treaties.'));
      setTreaties([]);
    } finally {
      setLoading(false);
    }
  }, [regionId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const proposed = useMemo(
    () => treaties.filter((t) => t.status === 'proposed'),
    [treaties],
  );
  const active = useMemo(
    () => treaties.filter((t) => t.status === 'active'),
    [treaties],
  );

  const runAction = async (id: string, action: 'accept' | 'reject' | 'terminate') => {
    setBusyId(id);
    setError(null);
    try {
      if (action === 'accept') await regionOwnerAPI.acceptTreaty(id);
      else if (action === 'reject') await regionOwnerAPI.rejectTreaty(id);
      else await regionOwnerAPI.terminateTreaty(id);
      await refresh();
    } catch (err: any) {
      setError(formatRegionTreatyError(err, `Failed to ${action} treaty.`));
    } finally {
      setBusyId(null);
    }
  };

  const onPropose = async (e: React.FormEvent) => {
    e.preventDefault();
    const counterparty = counterpartyId.trim();
    if (!counterparty) {
      setError('Enter the counterparty region id (UUID).');
      return;
    }
    setProposing(true);
    setError(null);
    try {
      await regionOwnerAPI.proposeTreaty(
        { counterparty_region_id: counterparty, treaty_type: treatyType, terms: {} },
        regionId,
      );
      setCounterpartyId('');
      await refresh();
    } catch (err: any) {
      setError(formatRegionTreatyError(err, 'Failed to propose treaty.'));
    } finally {
      setProposing(false);
    }
  };

  return (
    <div className="region-invite-panel region-treaty-panel" data-testid="region-treaty-panel">
      <header className="ri-hud-header">
        <span>TREATY INBOX</span>
        <span className="ri-hud-sub">
          {regionName || regionId.slice(0, 8)}
        </span>
        {onClose && (
          <button type="button" className="ri-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        )}
      </header>

      {error && (
        <div className="ri-error" role="alert">
          {error}
        </div>
      )}

      <section className="rt-primary" aria-label="Incoming proposals">
        <h3 className="rt-section-title">PENDING ({proposed.length})</h3>
        {loading && <p className="rt-muted">Loading…</p>}
        {!loading && proposed.length === 0 && (
          <p className="rt-muted">No pending proposals.</p>
        )}
        <ul className="rt-list">
          {proposed.map((t) => (
            <li key={t.id} className="rt-row">
              <div className="rt-row-meta">
                <strong>{t.treaty_type}</strong>
                <span>{partnerLabel(t, regionName)}</span>
              </div>
              <div className="rt-actions">
                <button
                  type="button"
                  className="rt-btn accept"
                  disabled={busyId === t.id}
                  onClick={() => void runAction(t.id, 'accept')}
                >
                  ACCEPT
                </button>
                <button
                  type="button"
                  className="rt-btn reject"
                  disabled={busyId === t.id}
                  onClick={() => void runAction(t.id, 'reject')}
                >
                  REJECT
                </button>
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section className="rt-secondary" aria-label="Active treaties">
        <h3 className="rt-section-title">ACTIVE ({active.length})</h3>
        <ul className="rt-list">
          {active.map((t) => (
            <li key={t.id} className="rt-row">
              <div className="rt-row-meta">
                <strong>{t.treaty_type}</strong>
                <span>{partnerLabel(t, regionName)}</span>
              </div>
              <div className="rt-actions">
                <button
                  type="button"
                  className="rt-btn terminate"
                  disabled={busyId === t.id}
                  onClick={() => void runAction(t.id, 'terminate')}
                >
                  TERMINATE
                </button>
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section className="rt-propose" aria-label="Propose treaty">
        <h3 className="rt-section-title">PROPOSE</h3>
        <form className="rt-form" onSubmit={(e) => void onPropose(e)}>
          <label>
            Counterparty region id
            <input
              value={counterpartyId}
              onChange={(e) => setCounterpartyId(e.target.value)}
              placeholder="uuid"
              autoComplete="off"
            />
          </label>
          <label>
            Type
            <select value={treatyType} onChange={(e) => setTreatyType(e.target.value)}>
              {TREATY_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <button type="submit" className="rt-btn propose" disabled={proposing}>
            {proposing ? 'SENDING…' : 'SEND PROPOSAL'}
          </button>
        </form>
      </section>
    </div>
  );
};

export default RegionTreatyPanel;
