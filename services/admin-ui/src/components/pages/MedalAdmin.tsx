import React, { useCallback, useEffect, useMemo, useState } from 'react';
import PageHeader from '../ui/PageHeader';
import { api } from '../../utils/auth';
import { useToast } from '../../contexts/ToastContext';
import './medal-admin.css';

/**
 * LEG-11 — MedalAdmin grant / revoke / catalog browse.
 * Canon: sw2102-docs/FEATURES/gameplay/medals.md § Admin actions.
 * Auth: shipped routes use PLAYERS_ADJUST_REP (grant/revoke) and PLAYERS_VIEW (catalog)
 * — not the doc's stale ADR-0027 is_admin-only note.
 */

type TabId = 'grant' | 'revoke' | 'catalog';

interface MedalCatalogItem {
  id: string;
  name?: string | null;
  category?: string | null;
  tier?: string | null;
  description?: string | null;
  criteria?: string | null;
}

interface PlayerOption {
  id: string;
  username: string;
}

interface CatalogResponse {
  items: MedalCatalogItem[];
  total: number;
}

interface AdminMedalActionResponse {
  success: boolean;
  changed: boolean;
  player_id: string;
  medal_id: string;
  message: string;
}

const REASON_MAX = 500;

function detailFromErr(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
  return typeof detail === 'string' ? detail : fallback;
}

const MedalAdmin: React.FC = () => {
  const toast = useToast();
  const [tab, setTab] = useState<TabId>('grant');
  const [catalog, setCatalog] = useState<MedalCatalogItem[]>([]);
  const [players, setPlayers] = useState<PlayerOption[]>([]);
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [loadingPlayers, setLoadingPlayers] = useState(true);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [playerError, setPlayerError] = useState<string | null>(null);

  const [playerQuery, setPlayerQuery] = useState('');
  const [selectedPlayerId, setSelectedPlayerId] = useState('');
  const [medalId, setMedalId] = useState('');
  const [reason, setReason] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [catalogFilter, setCatalogFilter] = useState('');

  const loadCatalog = useCallback(async () => {
    setLoadingCatalog(true);
    setCatalogError(null);
    try {
      const { data } = await api.get<CatalogResponse>('/api/v1/medals/admin/catalog');
      setCatalog(Array.isArray(data?.items) ? data.items : []);
    } catch (err: unknown) {
      setCatalog([]);
      setCatalogError(detailFromErr(err, 'Failed to load medal catalog'));
    } finally {
      setLoadingCatalog(false);
    }
  }, []);

  const loadPlayers = useCallback(async () => {
    setLoadingPlayers(true);
    setPlayerError(null);
    try {
      const { data } = await api.get<{ players?: Array<{ id: string; username?: string; nickname?: string }> }>(
        '/api/v1/admin/players/comprehensive?limit=1000'
      );
      const rows = (data?.players ?? [])
        .map((p) => ({
          id: String(p.id),
          username: String(p.username || p.nickname || p.id),
        }))
        .filter((p) => p.id);
      setPlayers(rows);
    } catch (err: unknown) {
      setPlayers([]);
      setPlayerError(detailFromErr(err, 'Failed to load players'));
    } finally {
      setLoadingPlayers(false);
    }
  }, []);

  useEffect(() => {
    void loadCatalog();
    void loadPlayers();
  }, [loadCatalog, loadPlayers]);

  const filteredPlayers = useMemo(() => {
    const q = playerQuery.trim().toLowerCase();
    if (!q) return players.slice(0, 50);
    return players
      .filter(
        (p) =>
          p.username.toLowerCase().includes(q) ||
          p.id.toLowerCase().includes(q)
      )
      .slice(0, 50);
  }, [players, playerQuery]);

  const filteredCatalog = useMemo(() => {
    const q = catalogFilter.trim().toLowerCase();
    if (!q) return catalog;
    return catalog.filter((m) => {
      const hay = [m.id, m.name, m.category, m.tier, m.description]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      return hay.includes(q);
    });
  }, [catalog, catalogFilter]);

  const selectedPlayer = players.find((p) => p.id === selectedPlayerId) ?? null;
  const selectedMedal = catalog.find((m) => m.id === medalId) ?? null;

  const reasonOk = reason.length <= REASON_MAX;
  const canSubmit =
    Boolean(selectedPlayerId) &&
    Boolean(medalId) &&
    reasonOk &&
    !submitting &&
    (tab === 'grant' || (tab === 'revoke' && reason.trim().length > 0));

  const submitAction = async () => {
    if (!canSubmit) return;
    if (tab === 'revoke' && !reason.trim()) {
      toast.error('Revocation requires a reason');
      return;
    }
    setSubmitting(true);
    try {
      const path =
        tab === 'grant' ? '/api/v1/medals/admin/grant' : '/api/v1/medals/admin/revoke';
      const payload = {
        player_id: selectedPlayerId,
        medal_id: medalId,
        reason: reason.trim() || null,
      };
      const { data } = await api.post<AdminMedalActionResponse>(path, payload);
      toast.success(data?.message || (tab === 'grant' ? 'Medal granted' : 'Medal revoked'));
      setReason('');
    } catch (err: unknown) {
      toast.error(detailFromErr(err, tab === 'grant' ? 'Grant failed' : 'Revoke failed'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="page-container medal-admin" data-testid="medal-admin">
      <PageHeader
        title="Medal Admin"
        subtitle="Grant or revoke medals; browse the catalog (PLAYERS_ADJUST_REP / PLAYERS_VIEW)"
      />

      <div className="page-content">
        <div className="medal-admin-tabs" role="tablist" aria-label="Medal admin tabs">
          {([
            ['grant', 'Grant'],
            ['revoke', 'Revoke'],
            ['catalog', 'Catalog'],
          ] as const).map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={tab === id}
              className={`medal-admin-tab${tab === id ? ' active' : ''}`}
              onClick={() => setTab(id)}
            >
              {label}
            </button>
          ))}
        </div>

        {(tab === 'grant' || tab === 'revoke') && (
          <section className="section medal-admin-form" aria-label={`${tab} medal`}>
            {playerError && (
              <div className="alert alert-error mb-3" role="alert">
                {playerError}
              </div>
            )}
            {catalogError && (
              <div className="alert alert-error mb-3" role="alert">
                {catalogError}
              </div>
            )}

            <div className="medal-admin-field">
              <label htmlFor="medal-player-search">Player search</label>
              <input
                id="medal-player-search"
                type="search"
                value={playerQuery}
                onChange={(e) => setPlayerQuery(e.target.value)}
                placeholder="Username or player id"
                autoComplete="off"
              />
              {loadingPlayers ? (
                <p className="medal-admin-muted">Loading players…</p>
              ) : (
                <select
                  aria-label="Select player"
                  value={selectedPlayerId}
                  onChange={(e) => setSelectedPlayerId(e.target.value)}
                  size={Math.min(8, Math.max(3, filteredPlayers.length || 3))}
                  className="medal-admin-select-list"
                >
                  <option value="">— select player —</option>
                  {filteredPlayers.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.username} ({p.id})
                    </option>
                  ))}
                </select>
              )}
              {selectedPlayer && (
                <p className="medal-admin-muted">
                  Selected: <strong>{selectedPlayer.username}</strong>
                </p>
              )}
            </div>

            <div className="medal-admin-field">
              <label htmlFor="medal-catalog-pick">Medal</label>
              {loadingCatalog ? (
                <p className="medal-admin-muted">Loading catalog…</p>
              ) : (
                <select
                  id="medal-catalog-pick"
                  value={medalId}
                  onChange={(e) => setMedalId(e.target.value)}
                >
                  <option value="">— pick medal —</option>
                  {catalog.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name || m.id}
                      {m.tier ? ` · ${m.tier}` : ''}
                      {m.category ? ` (${m.category})` : ''}
                    </option>
                  ))}
                </select>
              )}
              {selectedMedal?.description && (
                <p className="medal-admin-muted">{selectedMedal.description}</p>
              )}
            </div>

            <div className="medal-admin-field">
              <label htmlFor="medal-reason">
                Reason{tab === 'revoke' ? ' (required)' : ' (optional)'} — {reason.length}/
                {REASON_MAX}
              </label>
              <textarea
                id="medal-reason"
                value={reason}
                onChange={(e) => setReason(e.target.value.slice(0, REASON_MAX))}
                rows={3}
                maxLength={REASON_MAX}
                aria-invalid={!reasonOk}
              />
            </div>

            <button
              type="button"
              className={`btn ${tab === 'revoke' ? 'btn-danger' : 'btn-primary'}`}
              disabled={!canSubmit}
              onClick={() => void submitAction()}
            >
              {submitting
                ? 'Working…'
                : tab === 'grant'
                  ? 'Grant medal'
                  : 'Revoke medal'}
            </button>
          </section>
        )}

        {tab === 'catalog' && (
          <section className="section medal-admin-catalog" aria-label="Medal catalog">
            {catalogError && (
              <div className="alert alert-error mb-3" role="alert">
                {catalogError}
              </div>
            )}
            <div className="medal-admin-field">
              <label htmlFor="medal-catalog-filter">Filter catalog</label>
              <input
                id="medal-catalog-filter"
                type="search"
                value={catalogFilter}
                onChange={(e) => setCatalogFilter(e.target.value)}
                placeholder="Name, id, category…"
              />
            </div>
            {loadingCatalog ? (
              <p className="medal-admin-muted">Loading catalog…</p>
            ) : filteredCatalog.length === 0 ? (
              <p className="medal-admin-muted">No medals match.</p>
            ) : (
              <div className="levers-table-wrap">
                <table className="levers-table" data-testid="medal-catalog-table">
                  <thead>
                    <tr>
                      <th>ID</th>
                      <th>Name</th>
                      <th>Category</th>
                      <th>Tier</th>
                      <th>Description</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredCatalog.map((m) => (
                      <tr key={m.id}>
                        <td className="font-mono text-xs">{m.id}</td>
                        <td>{m.name || '—'}</td>
                        <td>{m.category || '—'}</td>
                        <td>{m.tier || '—'}</td>
                        <td className="text-sm">{m.description || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        )}
      </div>
    </div>
  );
};

export default MedalAdmin;
