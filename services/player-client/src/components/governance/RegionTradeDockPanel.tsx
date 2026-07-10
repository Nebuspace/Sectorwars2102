import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { regionOwnerAPI, constructionAPI } from '../../services/api';
import { formatCredits } from '../../utils/formatters';
import './region-tradedock-panel.css';

/**
 * RegionTradeDockPanel — region-owner console for region-funded TradeDock
 * construction (WO-TD-RGF-1).
 *
 * Canon (sw2102-docs FEATURES/economy/tradedock-shipyard.md, "Region-funded
 * construction"): an owner of a region with >= 500 sectors may fund one new
 * TradeDock — 90 real-time days, a fixed resource bundle, and a 5% ongoing
 * shipyard-fee cut into the region treasury once built.
 *
 * Contract: props = { regionId, regionName?, isOwner, onClose? }. This
 * panel self-gates on `isOwner` and renders null when false in addition to
 * the caller-side gate GameDashboard already applies (RegionInvitePanel
 * trusts its caller; this panel's data — the region treasury balance — is
 * more sensitive, so it gets its own belt-and-suspenders check). The server
 * re-checks ownership on every call regardless.
 *
 * API contract — verified against the actual WO-TD-RGF-1 backend lane
 * (services/gameserver/src/api/routes/regional_governance.py +
 * services/services/construction_service.py, read directly rather than
 * guessed, since both landed in this working tree mid-build):
 *
 *   POST /api/v1/regions/my-region/tradedock-construction
 *     body { station_id: uuid }. station_id must be an EXISTING TradeDock-
 *     tier station inside the caller's region (construction_service.
 *     _require_tradedock precondition) — there is currently no player-
 *     client endpoint that lists candidate stations, so this panel takes it
 *     as a manual field (see the input's help text). 403 not owner, 404 no
 *     player record / station not in any region, 409 <500 sectors OR a
 *     build already in progress at that station, 402 insufficient region
 *     treasury. Success body is FLAT: {message, reservation_id, station_id,
 *     region_id, total_cost, build_days, resources_required,
 *     region_fee_share_pct, state, cancel_refund_policy} — no `project` /
 *     `active_project` wrapper.
 *
 *   There is NO dedicated GET status route on regional_governance.py for
 *   this feature. The region-funded build is modelled as an ordinary
 *   ConstructionReservation (synthetic ship_type 'TRADEDOCK_CONSTRUCTION'),
 *   owned by the region-owner's Player row, so it shows up in the EXISTING,
 *   already-live GET /api/v1/construction/reservations/mine like a player
 *   ship build would — that's what this panel polls to discover/track an
 *   active project (constructionAPI.getMyReservations), rather than a
 *   fictional dedicated endpoint.
 *
 * Known integration gaps (flagged, not silently patched over — both are
 * outside this FE lane's scope):
 *  - Region.treasury_balance is consumed defensively when the owner
 *    my-region response provides it (live number vs the 50M threshold,
 *    met/unmet); when the server omits the field the row falls back to
 *    "unverified" and the server's 402 at submit time stays the real
 *    enforcement. The backend addition landed in the same WO.
 *  - No current seeding path sets Station.tradedock_tier for a player-owned
 *    region, so in practice there is no valid station_id to submit yet for
 *    an organically-created region — the POST will 400 ("has no TradeDock
 *    shipyard") until that seeding gap closes. This panel is contract-
 *    correct today and will start working the moment it does.
 */

interface ConstructionReservation {
  id: string;
  station_id?: string;
  ship_type?: string;
  state?: string;
  created_at?: string | null;
  overall_progress_percent?: number;
  paused?: boolean;
  needs?: string[];
}

interface RegionTradeDockPanelProps {
  regionId: string | null;
  regionName?: string | null;
  isOwner: boolean;
  onClose?: () => void;
}

// --- Canon / implemented constants ---
const SECTOR_THRESHOLD = 500;
const COST_CR = 50_000_000;
const DURATION_DAYS = 90;
const OPERATING_CUT_PCT = 5;
// The resource bundle actually charged by construction_service's
// REGION_TRADEDOCK_RESOURCES, which the backend itself flags as diverging
// from the canon doc's ore/technology/equipment split (this codebase's
// economy has no "technology" resource — see Station.commodities). Shown
// here as what the system will actually require, not the aspirational
// canon text; update both together once PENDING-RULING(region-bundle) lands.
const RESOURCE_BUNDLE: { label: string; amount: number }[] = [
  { label: 'ORE', amount: 500_000 },
  { label: 'EQUIPMENT', amount: 300_000 },
  { label: 'ORGANICS', amount: 200_000 },
];

// Real construction_service.TERMINAL_STATES. 'complete' is deliberately NOT
// in this set — for the synthetic TRADEDOCK_CONSTRUCTION ship_type, claim()
// is not wired (a documented backend gap), so a finished build sits at
// 'complete' rather than ever reaching the 'claimed' terminal state.
const TERMINAL_STATES = new Set(['claimed', 'cancelled', 'forfeited']);

const STATION_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// --- Helpers ---

// apiRequest (services/api.ts) surfaces the server's human `detail` string
// verbatim when present (every raise in this backend flow carries one — see
// verify_region_owner / ConstructionError call sites), and only falls back
// to the bare `API Error: {status}` text when it can't. Pass a real message
// straight through; translate the bare-status fallback into an honest,
// codebase-accurate per-status message so a bare 402/403/404/409 is never
// shown as a raw number.
const friendlyError = (msg: string, fallback: string): string => {
  const bare = /^API Error: (\d+)$/.exec(msg);
  if (bare) {
    switch (bare[1]) {
      case '402':
        return `Your region treasury does not hold the required ${formatCredits(COST_CR)}.`;
      case '403':
        return 'You are not the owner of this region.';
      case '404':
        return 'Station not found, or your account has no player record.';
      case '409':
        return 'That station already has a TradeDock construction in progress, or your region does not yet meet the 500-sector requirement.';
      default:
        return fallback;
    }
  }
  return msg || fallback;
};

const fmtDateTime = (iso: string | null | undefined): string => {
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

const RegionTradeDockPanel: React.FC<RegionTradeDockPanelProps> = ({
  regionId,
  regionName,
  isOwner,
  onClose,
}) => {
  const [totalSectors, setTotalSectors] = useState<number | null>(null);
  // No existing GET currently returns this — stays null until the backend
  // gap above closes. Kept as its own field (not folded into "unknown"
  // eligibility) so it lights up automatically the moment it's available.
  const [treasuryBalance, setTreasuryBalance] = useState<number | null>(null);
  const [activeProject, setActiveProject] = useState<ConstructionReservation | null>(null);

  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [stationIdInput, setStationIdInput] = useState('');
  const [stationIdTouched, setStationIdTouched] = useState(false);
  const [confirmArmed, setConfirmArmed] = useState(false);
  const [initiating, setInitiating] = useState(false);
  const [initiateError, setInitiateError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      // Pass the already-known regionId prop through explicitly — a
      // 2+-region owner's unscoped getMyRegion() 400s (WO-DRIFT-admin-gov-
      // multiregion-owner-500); this panel already knows which region it's
      // scoped to, so there's no ambiguity to resolve here.
      const [region, reservations] = await Promise.all([
        regionOwnerAPI.getMyRegion(regionId || undefined),
        constructionAPI.getMyReservations(),
      ]);
      setTotalSectors(typeof region?.total_sectors === 'number' ? region.total_sectors : null);
      setTreasuryBalance(typeof region?.treasury_balance === 'number' ? region.treasury_balance : null);

      const list: ConstructionReservation[] = Array.isArray(reservations?.reservations)
        ? reservations.reservations
        : [];
      const tradedockReservations = list.filter((r) => r.ship_type === 'TRADEDOCK_CONSTRUCTION');
      // Prefer a non-terminal one; if none, fall back to the most recent
      // terminal one so a cancelled/forfeited history doesn't silently
      // vanish mid-refresh (list is already newest-first per the route).
      const active = tradedockReservations.find((r) => !TERMINAL_STATES.has(r.state || ''));
      setActiveProject(active || tradedockReservations[0] || null);
      setLoadError(null);
    } catch (e) {
      const raw = e instanceof Error ? e.message : '';
      console.error('TradeDock construction status error:', e);
      setLoadError(friendlyError(raw, 'TradeDock construction status unreachable. Try again.'));
    } finally {
      setLoading(false);
    }
  }, [regionId]);

  useEffect(() => {
    if (!isOwner || !regionId) return;
    refresh();
  }, [isOwner, regionId, refresh]);

  const projectIsActive = !!(activeProject && !TERMINAL_STATES.has(activeProject.state || ''));
  const projectIsComplete = activeProject?.state === 'complete';

  // Progress: prefer the server's authoritative overall_progress_percent
  // (accounts for phase gating / paused blockers, not just wall-clock
  // elapsed time). Fall back to an elapsed/duration estimate only if that
  // field is absent from the payload.
  const progressPct = useMemo(() => {
    if (!activeProject) return null;
    if (typeof activeProject.overall_progress_percent === 'number') {
      return Math.max(0, Math.min(100, activeProject.overall_progress_percent));
    }
    if (!activeProject.created_at) return null;
    const startedMs = Date.parse(activeProject.created_at);
    if (Number.isNaN(startedMs)) return null;
    const totalSpan = DURATION_DAYS * 86400 * 1000;
    const elapsed = Math.min(totalSpan, Math.max(0, Date.now() - startedMs));
    return Math.max(0, Math.min(100, (elapsed / totalSpan) * 100));
  }, [activeProject]);

  const meetsSectorThreshold = totalSectors !== null && totalSectors >= SECTOR_THRESHOLD;
  const stationIdValid = STATION_ID_PATTERN.test(stationIdInput.trim());
  const canInitiate = !loading && !projectIsActive && meetsSectorThreshold && stationIdValid;

  const handleInitiate = async () => {
    if (initiating) return;
    setInitiating(true);
    setInitiateError(null);
    try {
      const data = await regionOwnerAPI.initiateTradeDockConstruction(stationIdInput.trim());
      const reservationId: string | undefined = data?.reservation_id;
      let hydrated: ConstructionReservation | null = null;
      if (reservationId) {
        try {
          hydrated = await constructionAPI.getReservation(reservationId);
        } catch (fetchErr) {
          // The spend already succeeded server-side — don't lose that by
          // failing the whole flow over a follow-up read. Fall back to a
          // minimal record built from the POST response itself.
          console.error('Post-initiate reservation fetch failed:', fetchErr);
        }
      }
      setActiveProject(
        hydrated || {
          id: reservationId || 'pending',
          station_id: data?.station_id,
          ship_type: 'TRADEDOCK_CONSTRUCTION',
          state: data?.state || 'queued',
          created_at: null,
        }
      );
      setConfirmArmed(false);
      setStationIdInput('');
      setStationIdTouched(false);
      // Reconcile against the server's authoritative status in the background.
      refresh();
    } catch (e) {
      const raw = e instanceof Error ? e.message : '';
      setInitiateError(friendlyError(raw, 'TradeDock construction request rejected.'));
    } finally {
      setInitiating(false);
    }
  };

  // Belt-and-suspenders — see file docstring. Every hook above is safe to
  // call on every render (none depend on this branch), so this early return
  // can sit after them without violating the Rules of Hooks.
  if (!isOwner || !regionId) return null;

  return (
    <div className="region-tradedock-panel">
      <header className="rtd-hud-header">
        <span className="rtd-hud-title">REGION TRADEDOCK CONSTRUCTION</span>
        <span className="rtd-hud-sub">{regionName || 'YOUR REGION'}</span>
        {onClose && (
          <button
            type="button"
            className="rtd-close"
            onClick={onClose}
            aria-label="Close TradeDock construction control"
          >
            ✕
          </button>
        )}
      </header>

      <div className="rtd-body">
        <p className="rtd-intro">
          Fund construction of a new TradeDock in your region — a permanent, NPC-controlled
          shipyard and premium trading hub. Once built it operates identically to a
          galaxy-seeded TradeDock, and your region treasury earns a {OPERATING_CUT_PCT}% cut of
          every shipyard fee it generates.
        </p>

        {loading && totalSectors === null && !activeProject ? (
          <p className="rtd-state">Consulting the regional construction ledger…</p>
        ) : (
          <>
            {loadError && <div className="rtd-validation-strip">{loadError}</div>}

            {activeProject && (projectIsActive || projectIsComplete) ? (
              <section className="rtd-section">
                <h3 className="rtd-section-title">
                  {projectIsComplete ? 'CONSTRUCTION COMPLETE' : 'PROJECT UNDERWAY'}
                </h3>
                <div className="rtd-progress-card">
                  <div className="rtd-progress-track">
                    <div
                      className="rtd-progress-fill"
                      style={{ width: `${progressPct !== null ? progressPct.toFixed(1) : 0}%` }}
                    />
                  </div>
                  <div className="rtd-progress-meta">
                    <span className="rtd-meta-item">
                      <span className="rtd-meta-label">PROGRESS</span>
                      <span className="rtd-meta-value">
                        {progressPct !== null ? `${progressPct.toFixed(0)}%` : '—'}
                      </span>
                    </span>
                    <span className="rtd-meta-item">
                      <span className="rtd-meta-label">STARTED</span>
                      <span className="rtd-meta-value">{fmtDateTime(activeProject.created_at)}</span>
                    </span>
                    <span className="rtd-meta-item">
                      <span className="rtd-meta-label">STATE</span>
                      <span className="rtd-meta-value">
                        {(activeProject.state || '—').replace(/_/g, ' ').toUpperCase()}
                      </span>
                    </span>
                  </div>
                  {activeProject.paused && activeProject.needs && activeProject.needs.length > 0 && (
                    <div className="rtd-validation-strip">
                      Blocked: {activeProject.needs.join('; ')}
                    </div>
                  )}
                  <p className="rtd-progress-note">
                    {projectIsComplete
                      ? 'The build is finished and awaiting activation as a live TradeDock — no further action is needed here.'
                      : `${formatCredits(COST_CR)} and the resource bundle are already committed from your region treasury. The dock comes online automatically at completion.`}
                  </p>
                </div>
              </section>
            ) : (
              <>
                <section className="rtd-section">
                  <h3 className="rtd-section-title">ELIGIBILITY</h3>
                  <ul className="rtd-eligibility-list">
                    <li className={`rtd-eligibility-row ${meetsSectorThreshold ? 'met' : 'unmet'}`}>
                      <span className="rtd-eligibility-icon">{meetsSectorThreshold ? '✓' : '✕'}</span>
                      <span className="rtd-eligibility-label">REGION SIZE</span>
                      <span className="rtd-eligibility-value">
                        {totalSectors !== null ? totalSectors.toLocaleString() : '—'} /{' '}
                        {SECTOR_THRESHOLD.toLocaleString()} sectors
                      </span>
                    </li>
                    <li
                      className={`rtd-eligibility-row ${
                        treasuryBalance === null
                          ? 'unknown'
                          : treasuryBalance >= COST_CR
                            ? 'met'
                            : 'unmet'
                      }`}
                    >
                      <span className="rtd-eligibility-icon">
                        {treasuryBalance === null ? '?' : treasuryBalance >= COST_CR ? '✓' : '✕'}
                      </span>
                      <span className="rtd-eligibility-label">TREASURY</span>
                      <span className="rtd-eligibility-value">
                        {treasuryBalance !== null
                          ? `${formatCredits(treasuryBalance)} / ${formatCredits(COST_CR)}`
                          : `Verified automatically on submit (need ${formatCredits(COST_CR)})`}
                      </span>
                    </li>
                  </ul>
                </section>

                <section className="rtd-section">
                  <h3 className="rtd-section-title">PROJECT TERMS</h3>
                  <ul className="rtd-terms-list">
                    <li>
                      <span className="rtd-terms-label">COST</span>
                      <span className="rtd-terms-value">{formatCredits(COST_CR)}</span>
                    </li>
                    <li>
                      <span className="rtd-terms-label">DURATION</span>
                      <span className="rtd-terms-value">{DURATION_DAYS} real-time days</span>
                    </li>
                    <li>
                      <span className="rtd-terms-label">RESOURCE BUNDLE</span>
                      <span className="rtd-terms-value">
                        {RESOURCE_BUNDLE.map((r) => `${r.amount.toLocaleString()} ${r.label}`).join(
                          ' + '
                        )}
                      </span>
                    </li>
                    <li>
                      <span className="rtd-terms-label">OPERATING CUT</span>
                      <span className="rtd-terms-value">
                        {OPERATING_CUT_PCT}% of shipyard fees, to your region treasury
                      </span>
                    </li>
                  </ul>
                </section>

                <section className="rtd-section">
                  <h3 className="rtd-section-title">TARGET STATION</h3>
                  <div className="rtd-field">
                    <input
                      type="text"
                      className={`rtd-station-input ${
                        stationIdTouched && !stationIdValid && stationIdInput ? 'invalid' : ''
                      }`}
                      placeholder="Target station ID (e.g. 3fa85f64-5717-4562-b3fc-2c963f66afa6)"
                      value={stationIdInput}
                      disabled={confirmArmed || initiating}
                      onChange={(e) => setStationIdInput(e.target.value)}
                      onBlur={() => setStationIdTouched(true)}
                    />
                    <p className="rtd-field-help">
                      Enter the ID of an existing TradeDock-tier station already inside your
                      region — a picker isn't available yet. The server verifies the station and
                      region ownership before spending anything.
                    </p>
                  </div>
                </section>

                {initiateError && <div className="rtd-validation-strip">{initiateError}</div>}

                <section className="rtd-section rtd-action-section">
                  {!confirmArmed ? (
                    <button
                      type="button"
                      className="rtd-btn primary"
                      disabled={!canInitiate}
                      onClick={() => setConfirmArmed(true)}
                      title={
                        !meetsSectorThreshold
                          ? `Your region needs ${SECTOR_THRESHOLD.toLocaleString()} sectors to be eligible.`
                          : !stationIdValid
                            ? 'Enter a valid target station ID.'
                            : undefined
                      }
                    >
                      INITIATE CONSTRUCTION
                    </button>
                  ) : (
                    <div className="rtd-confirm-card">
                      <p className="rtd-confirm-text">
                        This commits {formatCredits(COST_CR)} and the full resource bundle from
                        your region treasury, starting a {DURATION_DAYS}-day build at station{' '}
                        <code>{stationIdInput.trim()}</code>. Confirm?
                      </p>
                      <div className="rtd-confirm-row">
                        <button
                          type="button"
                          className="rtd-btn primary commit"
                          disabled={initiating}
                          onClick={handleInitiate}
                        >
                          {initiating ? 'COMMITTING…' : 'CONFIRM CONSTRUCTION'}
                        </button>
                        <button
                          type="button"
                          className="rtd-btn ghost"
                          disabled={initiating}
                          onClick={() => setConfirmArmed(false)}
                        >
                          CANCEL
                        </button>
                      </div>
                    </div>
                  )}
                </section>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
};

export default RegionTradeDockPanel;
