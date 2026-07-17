import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import PageHeader from '../ui/PageHeader';
import { api } from '../../utils/auth';
import './scopes-manager.css';

/** WO-RBAC-D frontend reminder — visually flag these four in the grant picker. */
const HIGH_IMPACT_SCOPES = new Set([
  'admin.galaxy.manage',
  'admin.players.adjust_credits',
  'admin.ships.manage',
  'admin.disputes.resolve',
]);

const META_SCOPES = new Set([
  'admin.scopes.grant',
  'admin.scopes.revoke',
  'admin.audit.view',
]);

interface ActiveGrant {
  scope: string;
  granted_at?: string | null;
  granted_by?: string | null;
}

interface ScopeHolder {
  user_id: string;
  username?: string | null;
  is_admin: boolean;
  scopes: ActiveGrant[];
}

interface ScopeCatalogItem {
  scope: string;
  description: string;
}

function scopeMissingMessage(err: any, fallback: string): string {
  const detail = err?.response?.data?.detail || err?.response?.data?.message;
  if (err?.response?.status === 403) {
    return typeof detail === 'string'
      ? detail
      : 'You lack admin.scopes.grant — cannot manage scopes.';
  }
  return typeof detail === 'string' ? detail : fallback;
}

function scopeHintId(scope: string): string {
  return `last-holder-hint-${scope.replace(/[^a-zA-Z0-9_-]/g, '-')}`;
}

export const ScopesManager: React.FC = () => {
  const [searchParams] = useSearchParams();
  const [holders, setHolders] = useState<ScopeHolder[]>([]);
  const [catalog, setCatalog] = useState<ScopeCatalogItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [grantScope, setGrantScope] = useState('');
  const [revokeTarget, setRevokeTarget] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isMutating, setIsMutating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);

  const pageRef = useRef<HTMLDivElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    setForbidden(false);
    try {
      const [holdersRes, catalogRes] = await Promise.all([
        api.get('/api/v1/admin/scopes/holders'),
        api.get('/api/v1/admin/scopes/catalog'),
      ]);
      setHolders(holdersRes.data as ScopeHolder[]);
      setCatalog(catalogRes.data as ScopeCatalogItem[]);
    } catch (err: any) {
      setHolders([]);
      setCatalog([]);
      if (err?.response?.status === 403) {
        setForbidden(true);
      }
      setError(scopeMissingMessage(err, 'Failed to load scope holders'));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Deep-link from Users → Scopes (?user=<id>)
  useEffect(() => {
    const uid = searchParams.get('user');
    if (!uid || holders.length === 0) return;
    if (holders.some((h) => h.user_id === uid)) {
      setSelectedId(uid);
    }
  }, [searchParams, holders]);

  // Focus trap for revoke confirmation dialog (Pixel INACCESSIBLE #1).
  useEffect(() => {
    if (!revokeTarget) return;

    previouslyFocused.current = document.activeElement as HTMLElement | null;
    const dialog = dialogRef.current;
    const page = pageRef.current;
    if (page) {
      page.setAttribute('aria-hidden', 'true');
      page.setAttribute('inert', '');
    }

    const focusables = () =>
      dialog
        ? Array.from(
            dialog.querySelectorAll<HTMLElement>(
              'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
            )
          )
        : [];

    const focusFirst = () => {
      const nodes = focusables();
      (nodes[0] || dialog)?.focus();
    };
    focusFirst();

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !isMutating) {
        e.preventDefault();
        setRevokeTarget(null);
        return;
      }
      if (e.key !== 'Tab' || !dialog) return;
      const nodes = focusables();
      if (nodes.length === 0) {
        e.preventDefault();
        return;
      }
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKeyDown);

    return () => {
      document.removeEventListener('keydown', onKeyDown);
      if (page) {
        page.removeAttribute('aria-hidden');
        page.removeAttribute('inert');
      }
      previouslyFocused.current?.focus?.();
    };
  }, [revokeTarget, isMutating]);

  const selected = useMemo(
    () => holders.find((h) => h.user_id === selectedId) ?? null,
    [holders, selectedId]
  );

  const holderCountByScope = useMemo(() => {
    const counts = new Map<string, number>();
    for (const h of holders) {
      for (const g of h.scopes) {
        counts.set(g.scope, (counts.get(g.scope) || 0) + 1);
      }
    }
    return counts;
  }, [holders]);

  const ownedScopes = useMemo(
    () => new Set(selected?.scopes.map((g) => g.scope) ?? []),
    [selected]
  );

  const grantable = useMemo(
    () => catalog.filter((c) => !ownedScopes.has(c.scope)),
    [catalog, ownedScopes]
  );

  const isLastHolderOf = (scope: string) =>
    META_SCOPES.has(scope) && (holderCountByScope.get(scope) || 0) <= 1;

  const handleGrant = async () => {
    if (!selected || !grantScope) return;
    setIsMutating(true);
    setActionError(null);
    try {
      await api.post('/api/v1/admin/scopes/grant', {
        user_id: selected.user_id,
        scope: grantScope,
      });
      setGrantScope('');
      await load();
    } catch (err: any) {
      setActionError(scopeMissingMessage(err, 'Grant failed'));
    } finally {
      setIsMutating(false);
    }
  };

  const handleRevokeConfirm = async () => {
    if (!selected || !revokeTarget) return;
    setIsMutating(true);
    setActionError(null);
    try {
      await api.post('/api/v1/admin/scopes/revoke', {
        user_id: selected.user_id,
        scope: revokeTarget,
      });
      setRevokeTarget(null);
      await load();
    } catch (err: any) {
      setActionError(scopeMissingMessage(err, 'Revoke failed'));
      setRevokeTarget(null);
    } finally {
      setIsMutating(false);
    }
  };

  return (
    <>
      <div className="scopes-page" ref={pageRef}>
        <PageHeader
          title="Admin Scopes"
          subtitle="Grant and revoke admin capability scopes. Server is authoritative on every guard."
        />

        {forbidden && (
          <div className="scopes-alert scopes-alert-forbidden" role="alert">
            {error || 'You lack admin.scopes.grant — cannot manage scopes.'}
          </div>
        )}

        {!forbidden && error && (
          <div className="scopes-alert scopes-alert-error" role="alert">
            {error}
          </div>
        )}

        {isLoading && <p className="scopes-muted">Loading scope holders…</p>}

        {!isLoading && !forbidden && (
          <div className="scopes-layout">
            <section className="scopes-panel" aria-label="Scope holders">
              <h2 className="scopes-panel-title">Holders</h2>
              {holders.length === 0 ? (
                <p className="scopes-muted">No active scope holders.</p>
              ) : (
                <ul className="scopes-holder-list">
                  {holders.map((h) => (
                    <li key={h.user_id}>
                      <button
                        type="button"
                        className={`scopes-holder-btn${selectedId === h.user_id ? ' active' : ''}`}
                        onClick={() => {
                          setSelectedId(h.user_id);
                          setActionError(null);
                          setGrantScope('');
                        }}
                      >
                        <span className="scopes-holder-name">
                          {h.username || h.user_id.slice(0, 8)}
                        </span>
                        <span className="scopes-holder-meta">
                          {h.scopes.length} scope{h.scopes.length === 1 ? '' : 's'}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="scopes-panel" aria-label="Selected holder scopes">
              {!selected ? (
                <p className="scopes-muted">Select a holder to grant or revoke scopes.</p>
              ) : (
                <>
                  <h2 className="scopes-panel-title">
                    {selected.username || selected.user_id}
                  </h2>

                  {actionError && (
                    <div className="scopes-alert scopes-alert-error" role="alert">
                      {actionError}
                    </div>
                  )}

                  <div className="scopes-grant-row">
                    <label htmlFor="scopes-grant-picker" className="scopes-label">
                      Grant scope
                    </label>
                    <select
                      id="scopes-grant-picker"
                      value={grantScope}
                      onChange={(e) => setGrantScope(e.target.value)}
                      disabled={isMutating || grantable.length === 0}
                    >
                      <option value="">Select a scope…</option>
                      {grantable.map((c) => (
                        <option
                          key={c.scope}
                          value={c.scope}
                          aria-label={
                            HIGH_IMPACT_SCOPES.has(c.scope)
                              ? `⚠ High-impact: ${c.scope}`
                              : c.scope
                          }
                        >
                          {HIGH_IMPACT_SCOPES.has(c.scope) ? '⚠ ' : ''}
                          {c.scope}
                        </option>
                      ))}
                    </select>
                    {grantScope && (
                      <p className="scopes-desc">
                        {catalog.find((c) => c.scope === grantScope)?.description}
                        {HIGH_IMPACT_SCOPES.has(grantScope) && (
                          <span className="scopes-high-impact"> High-impact — feeds the review queue.</span>
                        )}
                      </p>
                    )}
                    <button
                      type="button"
                      className="btn btn-primary"
                      disabled={!grantScope || isMutating}
                      onClick={handleGrant}
                    >
                      Grant
                    </button>
                  </div>

                  <h3 className="scopes-subhead">Active grants</h3>
                  {selected.scopes.length === 0 ? (
                    <p className="scopes-muted">No active grants.</p>
                  ) : (
                    <ul className="scopes-grant-list">
                      {selected.scopes.map((g) => {
                        const lastMeta = isLastHolderOf(g.scope);
                        const hintId = scopeHintId(g.scope);
                        return (
                          <li key={g.scope} className="scopes-grant-item">
                            <div>
                              <code>
                                {HIGH_IMPACT_SCOPES.has(g.scope) ? '⚠ ' : ''}
                                {g.scope}
                              </code>
                              {lastMeta && (
                                <p className="scopes-guard-hint" id={hintId}>
                                  Last holder of this meta-scope — revoke disabled in UI;
                                  server will also block.
                                </p>
                              )}
                            </div>
                            <button
                              type="button"
                              className="btn btn-secondary"
                              disabled={isMutating || lastMeta}
                              title={
                                lastMeta
                                  ? 'Cannot revoke the last holder of this meta-scope'
                                  : 'Revoke this scope'
                              }
                              aria-describedby={lastMeta ? hintId : undefined}
                              onClick={() => setRevokeTarget(g.scope)}
                            >
                              Revoke
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </>
              )}
            </section>
          </div>
        )}
      </div>

      {revokeTarget && selected && (
        <div
          className="scopes-modal-backdrop"
          onClick={() => !isMutating && setRevokeTarget(null)}
        >
          <div
            ref={dialogRef}
            className="scopes-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="scopes-revoke-title"
            tabIndex={-1}
            onClick={(e) => e.stopPropagation()}
          >
            <h2 id="scopes-revoke-title">Confirm revoke</h2>
            <p>
              Revoke <code>{revokeTarget}</code> from{' '}
              <strong>{selected.username || selected.user_id}</strong>? This is
              consequential — they lose that admin capability immediately.
            </p>
            <div className="scopes-modal-actions">
              <button
                type="button"
                className="btn btn-secondary"
                disabled={isMutating}
                onClick={() => setRevokeTarget(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary"
                disabled={isMutating}
                onClick={handleRevokeConfirm}
              >
                Revoke
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};

export default ScopesManager;
