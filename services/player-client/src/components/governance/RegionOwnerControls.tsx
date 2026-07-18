import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { useGame } from '../../contexts/GameContext';
import { regionOwnerAPI } from '../../services/api';
import RegionInvitePanel from './RegionInvitePanel';
import RegionTradeDockPanel from './RegionTradeDockPanel';

/**
 * RegionOwnerControls — self-contained region/governance/owner-tools bundle
 * (WO-UI0-STATUSBAR sub-part b). Relocated verbatim out of GameDashboard's
 * `id="location"` HudChip (state was GameDashboard.tsx:697-753, the buttons
 * were :2466-2537, the portals were :3630-3665) so it can mount inside the
 * new status bar's LocationDropdown — an ANCESTOR of GameDashboard where the
 * old chip-local `useState`s couldn't reach. Carries its own state/effect and
 * both portal modals; zero required props.
 *
 * AUTH NOTE: every gate below (`currentSector.region_id`, `isRegionOwner`,
 * `ownedRegionChoices.length > 0`) is a pure reflection of the
 * `regionOwnerAPI.getMyRegion()` HTTP response — the real RBAC for invite/
 * tradedock/governance is enforced SERVER-SIDE at those endpoints regardless
 * of where this DOM lives. Relocating it does not change auth. Preserved
 * exactly as-is; not tightened or loosened.
 */
const RegionOwnerControls: React.FC = () => {
  const { currentSector } = useGame();
  const navigate = useNavigate();

  // Region-owner invite control (WO-IL4). There is no ownership flag on
  // PlayerState, so probe GET /api/v1/regions/my-region once on mount: 200 =>
  // this player owns a region (trigger + panel render); 404 => not an owner
  // (one quiet probe per session, no trigger ever shown). The owned region id
  // is taken from the probe response and handed to the invite panel.
  const [ownedRegionId, setOwnedRegionId] = useState<string | null>(null);
  const [ownedRegionName, setOwnedRegionName] = useState<string | null>(null);
  // WO-DRIFT-admin-gov-multiregion-owner-500: a 2+-region owner's unscoped
  // probe now 400s with a pick-list (never a silent guess) — these are the
  // choices offered until the player picks one via the switcher.
  const [ownedRegionChoices, setOwnedRegionChoices] = useState<
    Array<{ id: string; name: string; display_name?: string }>
  >([]);
  const [showRegionInvites, setShowRegionInvites] = useState(false);
  // Region-funded TradeDock construction (WO-TD-RGF-1). Reuses the ownership
  // probe/state above rather than re-probing — same owner, same region.
  const [showRegionTradeDock, setShowRegionTradeDock] = useState(false);
  // Pixel a11y REVISE #4 — the probe previously rendered nothing while
  // in-flight (buttons silently appeared/disappeared) and had no error
  // surface on a genuine transient failure, indistinguishable from the
  // expected "you don't own a region" 404. `loading` gates the
  // ownership-derived controls (picker / INVITE CONTROL / TRADEDOCK
  // CONSTRUCTION); `probeError` renders a brief line on a real failure.
  const [loading, setLoading] = useState(true);
  const [probeError, setProbeError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    (async () => {
      try {
        const region = await regionOwnerAPI.getMyRegion();
        if (cancelled || !region?.id) return;
        setOwnedRegionId(String(region.id));
        setOwnedRegionName(region.display_name || region.name || null);
        setProbeError(null);
      } catch (err) {
        if (cancelled) return;
        const regions = (err as any)?.regions as
          | Array<{ id: string; name: string; display_name?: string }>
          | undefined;
        if ((err as any)?.code === 'ERR_AMBIGUOUS_REGION_OWNER' && regions?.length) {
          // Multiple owned regions — surface the switcher instead of hiding
          // ownership entirely.
          setOwnedRegionChoices(regions);
          setOwnedRegionId(null);
          setOwnedRegionName(null);
          setProbeError(null);
          return;
        }
        setOwnedRegionId(null);
        setOwnedRegionName(null);
        setOwnedRegionChoices([]);
        // Distinguish the EXPECTED "not an owner" 404 (silent, as before)
        // from a genuine transient failure (network error, 500, etc — now
        // surfaced). apiRequest (services/api.ts) does not preserve the
        // HTTP status on the thrown Error, only `.message` — so this is a
        // text heuristic, not a status check: the gameserver's stable 404
        // detail string is "No region found for this user"
        // (regional_governance.py verify_region_owner), and this repo's own
        // test fixture for that case uses "Not Found" — both contain
        // "found" case-insensitively, which no real transient-error message
        // (network/500/parse failures) plausibly does.
        const message = String((err as any)?.message || '');
        setProbeError(message.toLowerCase().includes('found') ? null : 'Region status unavailable — try again shortly.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const isRegionOwner = ownedRegionId !== null;

  const selectOwnedRegion = (choice: { id: string; name: string; display_name?: string }) => {
    setOwnedRegionId(choice.id);
    setOwnedRegionName(choice.display_name || choice.name);
    setOwnedRegionChoices([]);
  };

  return (
    <>
      {/* Pixel a11y REVISE #4 — the probe previously rendered nothing while
          in-flight; a brief status line replaces the silent gap so the
          owner controls don't just silently pop in/out. */}
      {loading && (
        <div className="hud-region-probe-status" style={{ fontSize: '0.7rem', opacity: 0.7 }}>
          Loading region status…
        </div>
      )}
      {!loading && probeError && (
        <div className="hud-region-probe-error" style={{ fontSize: '0.7rem', color: '#ff8da3' }}>
          {probeError}
        </div>
      )}
      {currentSector?.region_id && (
        <button
          type="button"
          className="hud-region-governance-btn"
          onClick={() => navigate('/game/governance')}
          title="Open regional governance — elections, policies, treaties"
        >
          ◆ GOVERNANCE
        </button>
      )}
      {!loading && !isRegionOwner && ownedRegionChoices.length > 0 && (
        <select
          className="hud-region-owner-picker"
          defaultValue=""
          title="You own multiple regions — pick one to manage"
          onChange={(e) => {
            const choice = ownedRegionChoices.find((r) => r.id === e.target.value);
            if (choice) selectOwnedRegion(choice);
          }}
        >
          <option value="" disabled>
            ◆ SELECT REGION TO MANAGE
          </option>
          {ownedRegionChoices.map((r) => (
            <option key={r.id} value={r.id}>
              {r.display_name || r.name}
            </option>
          ))}
        </select>
      )}
      {!loading && isRegionOwner && (
        <button
          type="button"
          className="hud-region-invite-btn"
          onClick={() => setShowRegionInvites(true)}
          title="Manage region invites"
        >
          ◆ INVITE CONTROL
        </button>
      )}
      {!loading && isRegionOwner && (
        <button
          type="button"
          className="hud-region-tradedock-btn"
          onClick={() => setShowRegionTradeDock(true)}
          title="Fund region TradeDock construction"
        >
          ◆ TRADEDOCK CONSTRUCTION
        </button>
      )}

      {/* Region-owner invite control — portal overlay escapes any dropdown's
          stacking context, same pattern as the original chip-hosted modal.
          Gated on confirmed region ownership. */}
      {showRegionInvites && isRegionOwner && ownedRegionId && createPortal(
        <div
          className="region-invite-overlay"
          onClick={() => setShowRegionInvites(false)}
        >
          <div className="region-invite-shell" onClick={(e) => e.stopPropagation()}>
            <RegionInvitePanel
              regionId={ownedRegionId}
              regionName={ownedRegionName}
              onClose={() => setShowRegionInvites(false)}
            />
          </div>
        </div>,
        document.body
      )}

      {/* Region-funded TradeDock construction — same portal shell pattern,
          same ownership gate/state as the invite control above. */}
      {showRegionTradeDock && isRegionOwner && ownedRegionId && createPortal(
        <div
          className="region-tradedock-overlay"
          onClick={() => setShowRegionTradeDock(false)}
        >
          <div className="region-tradedock-shell" onClick={(e) => e.stopPropagation()}>
            <RegionTradeDockPanel
              regionId={ownedRegionId}
              regionName={ownedRegionName}
              isOwner={isRegionOwner}
              onClose={() => setShowRegionTradeDock(false)}
            />
          </div>
        </div>,
        document.body
      )}
    </>
  );
};

export default RegionOwnerControls;
