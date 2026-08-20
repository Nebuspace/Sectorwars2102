import React, { useCallback, useEffect, useMemo, useState } from 'react';
import PageHeader from '../ui/PageHeader';
import { api } from '../../utils/auth';
import { formatAdminApiError } from '../../utils/adminApiError';
import { useToast } from '../../contexts/ToastContext';
import './medal-admin.css';

/**
 * LEG-11 + LEG-82 — MedalAdmin grant / revoke / catalog / bulk grant.
 * Canon: sw2102-docs/FEATURES/gameplay/medals.md § Admin actions.
 * Auth: PLAYERS_ADJUST_REP (grant/revoke/bulk) and PLAYERS_VIEW (catalog).
 * Bulk contract: LEG-81 POST /api/v1/medals/admin/bulk-grant (dry_run then commit).
 * Admin bulk rate-limit (10/min) is Design-only in OPERATIONS/admin-ui.md — not enforced server-side yet.
 */

type TabId = 'grant' | 'revoke' | 'catalog' | 'bulk';

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

/** LEG-81 AdminBulkGrantResponse — do not invent fields. */
interface BulkGrantInvalidSample {
  input: string;
  reason: string;
}

interface AdminBulkGrantResponse {
  dry_run: boolean;
  medal_id: string;
  valid_count: number;
  invalid_count: number;
  already_held_count: number;
  grantable_count: number;
  granted_count: number;
  invalid_samples: BulkGrantInvalidSample[];
  grant_batch_id?: string | null;
  toast_suppressed: boolean;
}

const REASON_MAX = 500;
const BULK_MAX_RECIPIENTS = 1000;

function medalActionError(err: unknown, fallback: string): string {
  return formatAdminApiError(err, {
    fallback,
    scopeHint:
      'admin.players.adjust_rep scope (PLAYERS_ADJUST_REP) required to grant or revoke medals',
  });
}

function medalCatalogError(err: unknown): string {
  return formatAdminApiError(err, {
    fallback: 'Failed to load medal catalog',
    scopeHint: 'admin.players.view scope (PLAYERS_VIEW) required to view the medal catalog',
    notFoundMessage:
      'Medal catalog route not found (404). The gameserver admin catalog endpoint is not on this deployment tip — see FEATURES/gameplay/medals.md.',
  });
}

/** Parse pasted IDs/usernames or CSV into recipient tokens (order preserved, empties dropped). */
export function parseBulkRecipients(raw: string): string[] {
  return raw
    .split(/[\n\r,;\t]+/)
    .map((t) => t.trim())
    .filter((t) => t.length > 0);
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

  const [bulkPaste, setBulkPaste] = useState('');
  const [bulkMedalId, setBulkMedalId] = useState('');
  const [bulkReason, setBulkReason] = useState('');
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkError, setBulkError] = useState<string | null>(null);
  const [dryRunResult, setDryRunResult] = useState<AdminBulkGrantResponse | null>(null);
  const [dryRunFingerprint, setDryRunFingerprint] = useState<string | null>(null);
  const [commitResult, setCommitResult] = useState<AdminBulkGrantResponse | null>(null);

  const loadCatalog = useCallback(async () => {
    setLoadingCatalog(true);
    setCatalogError(null);
    try {
      const { data } = await api.get<CatalogResponse>('/api/v1/medals/admin/catalog');
      setCatalog(Array.isArray(data?.items) ? data.items : []);
    } catch (err: unknown) {
      setCatalog([]);
      setCatalogError(medalCatalogError(err));
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
      setPlayerError(
        formatAdminApiError(err, {
          fallback: 'Failed to load players',
          scopeHint: 'admin.players.view scope (PLAYERS_VIEW)',
        })
      );
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
  const bulkSelectedMedal = catalog.find((m) => m.id === bulkMedalId) ?? null;

  const bulkRecipients = useMemo(() => parseBulkRecipients(bulkPaste), [bulkPaste]);
  const bulkOverCap = bulkRecipients.length > BULK_MAX_RECIPIENTS;
  const bulkReasonOk = bulkReason.length <= REASON_MAX;
  const bulkFormFingerprint = `${bulkMedalId}\0${bulkPaste}\0${bulkReason}`;
  const dryRunCurrent =
    dryRunResult !== null &&
    dryRunFingerprint === bulkFormFingerprint &&
    dryRunResult.dry_run === true;
  const canDryRun =
    Boolean(bulkMedalId) &&
    bulkRecipients.length > 0 &&
    !bulkOverCap &&
    bulkReasonOk &&
    !bulkBusy;
  const canCommit =
    dryRunCurrent &&
    dryRunResult.grantable_count > 0 &&
    !bulkBusy &&
    bulkReasonOk &&
    !bulkOverCap;

  const clearBulkPreview = () => {
    setDryRunResult(null);
    setDryRunFingerprint(null);
    setCommitResult(null);
    setBulkError(null);
  };

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
      toast.error(
        medalActionError(err, tab === 'grant' ? 'Grant failed' : 'Revoke failed')
      );
    } finally {
      setSubmitting(false);
    }
  };

  const runBulkDryRun = async () => {
    if (!canDryRun) return;
    setBulkBusy(true);
    setBulkError(null);
    setCommitResult(null);
    try {
      const { data } = await api.post<AdminBulkGrantResponse>('/api/v1/medals/admin/bulk-grant', {
        medal_id: bulkMedalId,
        recipients: bulkRecipients,
        reason: bulkReason.trim() || null,
        dry_run: true,
      });
      setDryRunResult(data);
      setDryRunFingerprint(bulkFormFingerprint);
      toast.info(
        `Dry-run: ${data.grantable_count} grantable, ${data.invalid_count} invalid, ${data.already_held_count} already held`
      );
    } catch (err: unknown) {
      setDryRunResult(null);
      setDryRunFingerprint(null);
      const bulkMsg = medalActionError(err, 'Bulk dry-run failed');
      setBulkError(bulkMsg);
      toast.error(bulkMsg);
    } finally {
      setBulkBusy(false);
    }
  };

  const runBulkCommit = async () => {
    if (!canCommit) return;
    setBulkBusy(true);
    setBulkError(null);
    try {
      const { data } = await api.post<AdminBulkGrantResponse>('/api/v1/medals/admin/bulk-grant', {
        medal_id: bulkMedalId,
        recipients: bulkRecipients,
        reason: bulkReason.trim() || null,
        dry_run: false,
      });
      setCommitResult(data);
      setDryRunResult(null);
      setDryRunFingerprint(null);
      const batch = data.grant_batch_id ? ` Batch id: ${data.grant_batch_id}.` : '';
      const toastNote = data.toast_suppressed
        ? ' Personal toasts suppressed (>50); players see medals at next-login splash.'
        : '';
      toast.success(`Granted ${data.granted_count} medal(s).${batch}${toastNote}`);
    } catch (err: unknown) {
      const bulkCommitMsg = medalActionError(err, 'Bulk commit failed');
      setBulkError(bulkCommitMsg);
      toast.error(bulkCommitMsg);
    } finally {
      setBulkBusy(false);
    }
  };

  return (
    <div className="page-container medal-admin" data-testid="medal-admin">
      <PageHeader
        title="Medal Admin"
        subtitle="Grant, revoke, or bulk-grant medals; browse the catalog (PLAYERS_ADJUST_REP / PLAYERS_VIEW)"
      />

      <div className="page-content">
        <div className="medal-admin-tabs" role="tablist" aria-label="Medal admin tabs">
          {([
            ['grant', 'Grant'],
            ['revoke', 'Revoke'],
            ['bulk', 'Bulk grant'],
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

        {tab === 'bulk' && (
          <section className="section medal-admin-form" aria-label="Bulk grant medals" data-testid="medal-bulk-panel">
            {catalogError && (
              <div className="alert alert-error mb-3" role="alert">
                {catalogError}
              </div>
            )}
            {bulkError && (
              <div className="alert alert-error mb-3" role="alert" data-testid="medal-bulk-error">
                {bulkError}
              </div>
            )}

            <p className="medal-admin-muted">
              Paste up to {BULK_MAX_RECIPIENTS} player IDs or usernames (newlines or CSV). Dry-run
              first; commit only after you confirm the summary. Shared{' '}
              <code>grant_batch_id</code> is returned on commit. Personal toasts are suppressed when
              grantable count &gt; 50. Admin bulk rate-limit (10/min) is Design-only — not enforced
              server-side yet.
            </p>

            <div className="medal-admin-field">
              <label htmlFor="medal-bulk-catalog-pick">Medal</label>
              {loadingCatalog ? (
                <p className="medal-admin-muted">Loading catalog…</p>
              ) : (
                <select
                  id="medal-bulk-catalog-pick"
                  aria-label="Bulk medal"
                  value={bulkMedalId}
                  onChange={(e) => {
                    setBulkMedalId(e.target.value);
                    clearBulkPreview();
                  }}
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
              {bulkSelectedMedal?.description && (
                <p className="medal-admin-muted">{bulkSelectedMedal.description}</p>
              )}
            </div>

            <div className="medal-admin-field">
              <label htmlFor="medal-bulk-recipients">
                Recipients — {bulkRecipients.length} parsed
                {bulkOverCap ? ` (over ${BULK_MAX_RECIPIENTS} max)` : ''}
              </label>
              <textarea
                id="medal-bulk-recipients"
                aria-label="Bulk recipients"
                value={bulkPaste}
                onChange={(e) => {
                  setBulkPaste(e.target.value);
                  clearBulkPreview();
                }}
                rows={8}
                placeholder={'player-uuid-or-username\nanother,third'}
                aria-invalid={bulkOverCap}
              />
              {bulkOverCap && (
                <p className="alert alert-error" role="alert">
                  Too many recipients ({bulkRecipients.length}); max {BULK_MAX_RECIPIENTS}.
                </p>
              )}
            </div>

            <div className="medal-admin-field">
              <label htmlFor="medal-bulk-reason">
                Reason (optional) — {bulkReason.length}/{REASON_MAX}
              </label>
              <textarea
                id="medal-bulk-reason"
                aria-label="Bulk reason"
                value={bulkReason}
                onChange={(e) => {
                  setBulkReason(e.target.value.slice(0, REASON_MAX));
                  clearBulkPreview();
                }}
                rows={2}
                maxLength={REASON_MAX}
              />
            </div>

            <div className="medal-admin-bulk-actions">
              <button
                type="button"
                className="btn btn-secondary"
                disabled={!canDryRun}
                onClick={() => void runBulkDryRun()}
                data-testid="medal-bulk-dry-run"
              >
                {bulkBusy && !commitResult ? 'Dry-running…' : 'Dry-run'}
              </button>
              <button
                type="button"
                className="btn btn-primary"
                disabled={!canCommit}
                onClick={() => void runBulkCommit()}
                data-testid="medal-bulk-commit"
              >
                {bulkBusy && dryRunCurrent ? 'Committing…' : 'Confirm & commit'}
              </button>
            </div>

            {dryRunCurrent && dryRunResult && (
              <div className="medal-admin-bulk-summary" data-testid="medal-bulk-dry-run-summary">
                <h3>Dry-run summary</h3>
                <ul>
                  <li>Valid: {dryRunResult.valid_count}</li>
                  <li>Invalid: {dryRunResult.invalid_count}</li>
                  <li>Already held: {dryRunResult.already_held_count}</li>
                  <li>Grantable: {dryRunResult.grantable_count}</li>
                </ul>
                {dryRunResult.invalid_samples?.length > 0 && (
                  <div data-testid="medal-bulk-invalid-samples">
                    <p className="medal-admin-muted">Invalid samples:</p>
                    <ul>
                      {dryRunResult.invalid_samples.map((s) => (
                        <li key={`${s.input}:${s.reason}`}>
                          <code>{s.input}</code> — {s.reason}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {dryRunResult.grantable_count === 0 && (
                  <p className="medal-admin-muted" role="status">
                    Nothing grantable — commit stays disabled.
                  </p>
                )}
              </div>
            )}

            {commitResult && !commitResult.dry_run && (
              <div className="medal-admin-bulk-summary" data-testid="medal-bulk-commit-result">
                <h3>Commit result</h3>
                <ul>
                  <li>Granted: {commitResult.granted_count}</li>
                  <li>
                    Batch id:{' '}
                    <code data-testid="medal-bulk-grant-batch-id">
                      {commitResult.grant_batch_id || '—'}
                    </code>
                  </li>
                  {commitResult.toast_suppressed && (
                    <li>Personal toasts suppressed (&gt;50 grantable).</li>
                  )}
                </ul>
              </div>
            )}
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
