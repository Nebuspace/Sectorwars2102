import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { regionOwnerAPI } from '../../services/api';
import './region-invite-panel.css';

/**
 * RegionInvitePanel — the region OWNER's invite-management console (WO-IL4).
 *
 * A region owner can mint single-use (or limited-use) invite links that place a
 * new player in this region and make them an instant voting citizen. This panel
 * lets the owner LIST their invites (code, usage, status, expiry), CREATE one
 * (shows the returned code + a shareable join link), and REVOKE one.
 *
 * It wires to the live IL3 endpoints via regionOwnerAPI — no mock data.
 *
 * Contract: props = { regionId, regionName?, onClose? }. The caller is
 * responsible for confirming ownership (probing getMyRegion) before mounting
 * this; the panel trusts the regionId it is handed and the server re-checks
 * ownership on every call.
 */

// --- IL3 invite shape (regional_governance.py _serialize_invite) ---

type InviteStatus = 'active' | 'exhausted' | 'revoked' | 'expired' | string;

interface RegionInvite {
  id: string;
  code: string;
  region_id: string;
  created_by?: string | null;
  max_uses: number;
  uses: number;
  status: InviteStatus;
  expires_at: string;
  created_at: string;
  revoked_at?: string | null;
}

interface RegionInvitePanelProps {
  regionId: string;
  regionName?: string | null;
  onClose?: () => void;
}

// max_uses bounds mirror the server (region_invite_service.py DEFAULT_MAX_USES=1,
// MAX_MAX_USES=10). The UI clamps to keep create requests inside the server's
// accepted range; the server is still the authority.
const MIN_MAX_USES = 1;
const MAX_MAX_USES = 10;

// Expiry choices (days). Server default is 7 when expires_at is omitted; we send
// an explicit ISO timestamp for the chosen horizon. These are presentation
// presets only — the server validates the resulting expires_at.
const EXPIRY_PRESETS = [
  { label: '1 DAY', days: 1 },
  { label: '7 DAYS', days: 7 },
  { label: '30 DAYS', days: 30 },
] as const;

// --- Helpers ---

// Translate the server's ERR_* codes (surfaced verbatim as error.message) into
// owner-readable copy. Unknown codes fall through to the raw message so nothing
// is swallowed. The 409 cap codes are the ones the brief calls out as needing
// clear messaging.
const friendlyError = (msg: string, fallback: string): string => {
  switch (msg) {
    case 'ERR_NOT_REGION_OWNER':
      return 'You are not the owner of this region.';
    case 'ERR_INVALID_MAX_USES':
      return 'Use count is out of the allowed range (1–10).';
    case 'ERR_INVALID_EXPIRY':
      return 'The chosen expiry is invalid — pick a future date.';
    case 'ERR_ACTIVE_INVITE_CAP':
      return 'You have reached the maximum active invites for this region. Revoke or let one expire before minting another.';
    case 'ERR_REDEMPTION_CAP':
      return 'Your region has hit its recent-redemption limit. Try again later.';
    case 'ERR_CODE_COLLISION':
      return 'Mint hiccup — please try again.';
    case 'ERR_INVITE_NOT_FOUND':
      return 'That invite no longer exists.';
    case 'ERR_NOT_INVITE_OWNER':
      return 'You did not mint this invite.';
    default:
      return msg || fallback;
  }
};

const fmtDateTime = (iso?: string | null): string => {
  if (!iso) return '—';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '—';
  return new Date(t).toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
};

// Compact "expires in" / "expired" relative phrasing for the list.
const fmtRelativeExpiry = (iso: string, nowMs: number): { text: string; past: boolean } => {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return { text: '—', past: false };
  let diff = Math.floor((t - nowMs) / 1000);
  if (diff <= 0) return { text: 'EXPIRED', past: true };
  const days = Math.floor(diff / 86400);
  diff %= 86400;
  const hours = Math.floor(diff / 3600);
  diff %= 3600;
  const minutes = Math.floor(diff / 60);
  if (days > 0) return { text: `${days}d ${hours}h`, past: false };
  if (hours > 0) return { text: `${hours}h ${minutes}m`, past: false };
  return { text: `${minutes}m`, past: false };
};

// The effective status accounting for client-side TTL: the server only flips
// status to 'expired' lazily, so an 'active' invite whose expires_at has passed
// should READ as expired in the owner's list. Never downgrades a terminal state.
const effectiveStatus = (invite: RegionInvite, nowMs: number): InviteStatus => {
  if (invite.status === 'active' && Date.parse(invite.expires_at) <= nowMs) {
    return 'expired';
  }
  return invite.status;
};

// Build the shareable join URL from the code. The /join route is the IL6
// signup-wiring target (Max-gated, not yet live), so the link is forward-looking
// — labelled as such in the UI. Host comes from the running origin.
const joinUrlFor = (code: string): string => {
  const origin =
    typeof window !== 'undefined' && window.location ? window.location.origin : '';
  return `${origin}/join?invite=${encodeURIComponent(code)}`;
};

const RegionInvitePanel: React.FC<RegionInvitePanelProps> = ({
  regionId,
  regionName,
  onClose,
}) => {
  // --- Server data ---
  const [invites, setInvites] = useState<RegionInvite[] | null>(null);
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState<string | null>(null);

  // --- Create flow ---
  const [maxUses, setMaxUses] = useState<number>(MIN_MAX_USES);
  const [expiryDays, setExpiryDays] = useState<number>(7);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  // The invite just minted — surfaced prominently with copy affordances.
  const [minted, setMinted] = useState<RegionInvite | null>(null);

  // --- Revoke flow (inline confirm, per invite) ---
  const [armedRevokeId, setArmedRevokeId] = useState<string | null>(null);
  const [revokeBusyId, setRevokeBusyId] = useState<string | null>(null);
  const [revokeErrors, setRevokeErrors] = useState<Record<string, string>>({});

  // --- Copy feedback (per target key) ---
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  // Ticking clock for relative expiry display
  const [nowMs, setNowMs] = useState<number>(Date.now());

  // --- Fetching ---

  const fetchInvites = useCallback(async () => {
    setListLoading(true);
    try {
      const data = await regionOwnerAPI.listInvites(regionId);
      const list = Array.isArray(data?.invites) ? (data.invites as RegionInvite[]) : [];
      setInvites(list);
      setListError(null);
    } catch (e) {
      const raw = e instanceof Error ? e.message : '';
      console.error('Region invite list error:', e);
      setListError(friendlyError(raw, 'Invite registry unreachable. Try again.'));
    } finally {
      setListLoading(false);
    }
  }, [regionId]);

  // On open + whenever the region changes
  useEffect(() => {
    fetchInvites();
  }, [fetchInvites]);

  // 1s tick while any active invite is counting toward expiry
  const hasActiveCountdowns = useMemo(
    () => (invites ?? []).some((i) => i.status === 'active'),
    [invites]
  );
  useEffect(() => {
    if (!hasActiveCountdowns) return;
    const interval = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(interval);
  }, [hasActiveCountdowns]);

  // Clear the transient "copied" flash after a moment
  useEffect(() => {
    if (!copiedKey) return;
    const t = setTimeout(() => setCopiedKey(null), 1800);
    return () => clearTimeout(t);
  }, [copiedKey]);

  // --- Derived ---

  // Active (non-terminal, non-expired) invites count — bounds the per-owner cap
  // messaging (server enforces ERR_ACTIVE_INVITE_CAP at 10).
  const activeCount = useMemo(
    () => (invites ?? []).filter((i) => effectiveStatus(i, nowMs) === 'active').length,
    [invites, nowMs]
  );

  // --- Actions ---

  const handleCreate = async () => {
    if (creating) return;
    setCreating(true);
    setCreateError(null);
    try {
      const clampedUses = Math.min(MAX_MAX_USES, Math.max(MIN_MAX_USES, maxUses));
      const expiresAt = new Date(Date.now() + expiryDays * 86400 * 1000).toISOString();
      const data = await regionOwnerAPI.createInvite(regionId, {
        max_uses: clampedUses,
        expires_at: expiresAt,
      });
      const invite = data?.invite as RegionInvite | undefined;
      if (invite) {
        setMinted(invite);
        // Optimistically prepend so the new code is visible without a round-trip,
        // then reconcile against the server's authoritative list.
        setInvites((prev) => (prev ? [invite, ...prev] : [invite]));
      }
      await fetchInvites();
    } catch (e) {
      const raw = e instanceof Error ? e.message : '';
      setCreateError(friendlyError(raw, 'Invite mint rejected.'));
    } finally {
      setCreating(false);
    }
  };

  const handleRevoke = async (inviteId: string) => {
    if (revokeBusyId) return;
    setRevokeBusyId(inviteId);
    setRevokeErrors((prev) => {
      const next = { ...prev };
      delete next[inviteId];
      return next;
    });
    try {
      await regionOwnerAPI.revokeInvite(regionId, inviteId);
      setArmedRevokeId(null);
      // If the just-minted card is the one revoked, drop the spotlight.
      setMinted((m) => (m && m.id === inviteId ? null : m));
      await fetchInvites();
    } catch (e) {
      const raw = e instanceof Error ? e.message : '';
      setRevokeErrors((prev) => ({ ...prev, [inviteId]: friendlyError(raw, 'Revoke rejected.') }));
    } finally {
      setRevokeBusyId(null);
    }
  };

  const copyToClipboard = useCallback(async (text: string, key: string) => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        // Fallback for non-secure contexts: a transient textarea + execCommand.
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      setCopiedKey(key);
    } catch (e) {
      console.error('Clipboard copy failed:', e);
    }
  }, []);

  // --- Render helpers ---

  const renderStatusBadge = (status: InviteStatus) => {
    const cls =
      status === 'active'
        ? 'active'
        : status === 'exhausted'
          ? 'exhausted'
          : status === 'revoked'
            ? 'revoked'
            : status === 'expired'
              ? 'expired'
              : 'unknown';
    return <span className={`ri-badge ${cls}`}>{String(status).toUpperCase()}</span>;
  };

  const renderInviteRow = (invite: RegionInvite) => {
    const status = effectiveStatus(invite, nowMs);
    const isActive = status === 'active';
    const expiry = fmtRelativeExpiry(invite.expires_at, nowMs);
    const armed = armedRevokeId === invite.id;
    const busy = revokeBusyId === invite.id;
    const rowError = revokeErrors[invite.id];
    const joinUrl = joinUrlFor(invite.code);

    return (
      <li key={invite.id} className={`ri-invite-row status-${status}`}>
        <div className="ri-invite-main">
          <div className="ri-invite-code-block">
            <span className="ri-invite-code" title={invite.code}>
              {invite.code}
            </span>
            <button
              type="button"
              className="ri-copy-btn"
              onClick={() => copyToClipboard(invite.code, `code-${invite.id}`)}
              aria-label="Copy invite code"
            >
              {copiedKey === `code-${invite.id}` ? 'COPIED' : 'COPY CODE'}
            </button>
            <button
              type="button"
              className="ri-copy-btn link"
              onClick={() => copyToClipboard(joinUrl, `link-${invite.id}`)}
              aria-label="Copy shareable join link"
            >
              {copiedKey === `link-${invite.id}` ? 'COPIED' : 'COPY LINK'}
            </button>
          </div>
          <div className="ri-invite-meta">
            {renderStatusBadge(status)}
            <span className="ri-meta-item">
              <span className="ri-meta-label">USES</span>
              <span className="ri-meta-value">
                {invite.uses} / {invite.max_uses}
              </span>
            </span>
            <span className="ri-meta-item">
              <span className="ri-meta-label">{expiry.past ? 'EXPIRY' : 'EXPIRES'}</span>
              <span className={`ri-meta-value ${expiry.past ? 'past' : ''}`}>
                {isActive ? expiry.text : fmtDateTime(invite.expires_at)}
              </span>
            </span>
          </div>
        </div>

        <div className="ri-invite-actions">
          {isActive ? (
            !armed ? (
              <button
                type="button"
                className="ri-btn danger"
                disabled={busy}
                onClick={() => setArmedRevokeId(invite.id)}
              >
                REVOKE
              </button>
            ) : (
              <div className="ri-confirm-row">
                <button
                  type="button"
                  className="ri-btn danger commit"
                  disabled={busy}
                  onClick={() => handleRevoke(invite.id)}
                >
                  {busy ? 'REVOKING…' : 'CONFIRM'}
                </button>
                <button
                  type="button"
                  className="ri-btn ghost"
                  disabled={busy}
                  onClick={() => setArmedRevokeId(null)}
                >
                  KEEP
                </button>
              </div>
            )
          ) : (
            <span className="ri-terminal-note">
              {status === 'exhausted'
                ? 'FULLY REDEEMED'
                : status === 'revoked'
                  ? `REVOKED ${invite.revoked_at ? fmtDateTime(invite.revoked_at) : ''}`.trim()
                  : 'EXPIRED'}
            </span>
          )}
        </div>

        {rowError && <div className="ri-validation-strip">{rowError}</div>}
      </li>
    );
  };

  return (
    <div className="region-invite-panel">
      <header className="ri-hud-header">
        <span className="ri-hud-title">REGION INVITE CONTROL</span>
        <span className="ri-hud-sub">{regionName || 'YOUR REGION'}</span>
        {onClose && (
          <button
            type="button"
            className="ri-close"
            onClick={onClose}
            aria-label="Close region invite control"
          >
            ✕
          </button>
        )}
      </header>

      <div className="ri-body">
        <p className="ri-intro">
          Mint an invite link to seat a new pilot directly in your region — they arrive as an
          instant citizen on your voter roll. Codes are single-use by default and always carry
          an expiry.
        </p>

        {/* ============ CREATE ============ */}
        <section className="ri-section">
          <h3 className="ri-section-title">MINT NEW INVITE</h3>

          <div className="ri-create-grid">
            <div className="ri-field">
              <span className="ri-field-label">MAX USES</span>
              <div className="ri-stepper">
                <button
                  type="button"
                  className="ri-step-btn"
                  disabled={creating || maxUses <= MIN_MAX_USES}
                  onClick={() => setMaxUses((v) => Math.max(MIN_MAX_USES, v - 1))}
                >
                  −
                </button>
                <span className="ri-stepper-value">{maxUses}</span>
                <button
                  type="button"
                  className="ri-step-btn"
                  disabled={creating || maxUses >= MAX_MAX_USES}
                  onClick={() => setMaxUses((v) => Math.min(MAX_MAX_USES, v + 1))}
                >
                  +
                </button>
              </div>
            </div>

            <div className="ri-field">
              <span className="ri-field-label">EXPIRES IN</span>
              <div className="ri-segmented">
                {EXPIRY_PRESETS.map((p) => (
                  <button
                    key={p.days}
                    type="button"
                    className={`ri-seg-btn ${expiryDays === p.days ? 'selected' : ''}`}
                    disabled={creating}
                    onClick={() => setExpiryDays(p.days)}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {createError && <div className="ri-validation-strip">{createError}</div>}

          <button
            type="button"
            className="ri-btn primary mint"
            disabled={creating}
            onClick={handleCreate}
          >
            {creating ? 'MINTING…' : 'MINT INVITE'}
          </button>

          {/* Spotlight on the freshly-minted code */}
          {minted && (
            <div className="ri-minted-card">
              <div className="ri-minted-title">INVITE MINTED — SHARE IT</div>
              <div className="ri-minted-code-row">
                <span className="ri-minted-code">{minted.code}</span>
                <button
                  type="button"
                  className="ri-copy-btn"
                  onClick={() => copyToClipboard(minted.code, 'minted-code')}
                >
                  {copiedKey === 'minted-code' ? 'COPIED' : 'COPY CODE'}
                </button>
              </div>
              <div className="ri-minted-link-row">
                <span className="ri-minted-link" title={joinUrlFor(minted.code)}>
                  {joinUrlFor(minted.code)}
                </span>
                <button
                  type="button"
                  className="ri-copy-btn link"
                  onClick={() => copyToClipboard(joinUrlFor(minted.code), 'minted-link')}
                >
                  {copiedKey === 'minted-link' ? 'COPIED' : 'COPY LINK'}
                </button>
              </div>
              <p className="ri-minted-note">
                The join link activates with the next game update. Until then, share the code —
                your invitee enters it at signup to start in {regionName || 'your region'}.
              </p>
            </div>
          )}
        </section>

        {/* ============ LIST ============ */}
        <section className="ri-section ri-list-section">
          <div className="ri-list-head">
            <h3 className="ri-section-title">YOUR INVITES</h3>
            <span className="ri-active-tally">
              {activeCount} / {MAX_MAX_USES} ACTIVE
            </span>
          </div>

          {listLoading && invites === null ? (
            <p className="ri-state">Consulting the invite registry…</p>
          ) : invites === null ? (
            <div className="ri-validation-strip">{listError}</div>
          ) : (
            <>
              {listError && <div className="ri-validation-strip">{listError}</div>}
              {invites.length === 0 ? (
                <p className="ri-state">No invites yet. Mint one above to seat a new citizen.</p>
              ) : (
                <ul className="ri-invite-list">{invites.map(renderInviteRow)}</ul>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
};

export default RegionInvitePanel;
