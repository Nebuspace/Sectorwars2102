import React, { useCallback, useEffect, useRef, useState, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { useGame, type MoveOption, type SpecialFormationSummary } from '../../contexts/GameContext';
import { useAutopilot } from '../../contexts/AutopilotContext';
import { useFirstLogin } from '../../contexts/FirstLoginContext';
import { useWebSocket } from '../../contexts/WebSocketContext';
import { useShellSlots } from '../layouts/ShellContext';
// import { useTheme } from '../../themes/ThemeProvider'; // Available for future use
import TradingInterface from '../trading/TradingInterface';
import SpaceDockInterface from '../spacedock/SpaceDockInterface';
import PortOfficeVenue from '../spacedock/PortOfficeVenue';
import ContractBoardVenue from '../spacedock/ContractBoardVenue';
import PopulationCenterInterface from '../planetary/PopulationCenterInterface';
import TacticalCard from '../tactical/TacticalCard';
import SolarSystemViewscreen from '../tactical/SolarSystemViewscreen';
import PlanetPortPair from '../tactical/PlanetPortPair';
import NavigationMap from '../tactical/NavigationMap';
import { chartToNavSectors } from '../tactical/navChartTransform';
import Galaxy3DRenderer from '../galaxy/Galaxy3DRenderer';
import QuantumDriveConsole from '../quantum/QuantumDriveConsole';
import GatewrightPanel from '../gatewright/GatewrightPanel';
import TacticalMonitor from '../tactical/TacticalMonitor';
import SolarSalvagePage from '../tactical/pages/SolarSalvagePage';
import CockpitColonyManagement from '../cockpit/CockpitColonyManagement';
import DeckPageTabs from '../cockpit/DeckPageTabs';
import type { ProductionLine } from '../cockpit/ProductionPanel';
import type { PerColonistRates, ProdRole } from '../cockpit/CoupledColonistSliders';
import SafeVaultPanel from '../cockpit/SafeVaultPanel';
import { navAPI, type NavChartResponse, sectorAPI, type SectorWreck } from '../../services/api';
import apiClient from '../../services/apiClient';
import { useResourceCatalog } from '../../hooks/useResourceCatalog';
import { TurnsIcon } from '../icons/TurnsIcon';
import './game-dashboard.css';
import './cockpit.css';
import '../tactical/tactical-layout.css';
import '../quantum/quantum-drive.css';
import '../galaxy/styles/galaxy-3d.css';

// Planet type icons (shared by the landed console and the claim ceremony)
const PLANET_TYPE_ICONS: Record<string, string> = {
  'terra': '🌍', 'm_class': '🌎', 'terran': '🌍', 'oceanic': '🌊',
  'l_class': '🏔️', 'mountainous': '🏔️', 'o_class': '🌊',
  'k_class': '🏜️', 'desert': '🏜️', 'h_class': '🌋', 'volcanic': '🌋',
  'd_class': '🌑', 'barren': '🌑', 'c_class': '❄️', 'frozen': '❄️',
  'ice': '🧊', 'jungle': '🌴', 'gas_giant': '🪐'
};

const getPlanetIcon = (type?: string): string =>
  PLANET_TYPE_ICONS[type?.toLowerCase() || ''] || '🪐';

// One accent class per planet type — the colors live in cockpit.css
const getPlanetTintClass = (type?: string): string =>
  `planet-tint-${(type || 'unknown').toLowerCase().replace(/[^a-z_]+/g, '_')}`;

/**
 * HudChip — windshield HUD glass chip.
 *
 * Wraps the existing chip content in a pointer-events:none glass panel whose
 * only interactive surface is a tiny minimize tab. Minimizing collapses the
 * chip to a compact pill carrying its essential datum; the state persists
 * per-chip in localStorage (cockpit.hud.<id>.min). Ghost-on-approach fading
 * is driven by the windshield mousemove listener in GameDashboard via the
 * data-hud-chip attribute (chips can't :hover — clicks pass through them).
 */
interface HudChipProps {
  /** chip identity — drives the localStorage key cockpit.hud.<id>.min */
  id: string;
  /** compact pill content shown while minimized */
  pill: React.ReactNode;
  /** positioning / variant classes (top-left, top-right hazard, …) */
  className?: string;
  children: React.ReactNode;
}

const hudMinKey = (id: string): string => `cockpit.hud.${id}.min`;

const HudChip: React.FC<HudChipProps> = ({ id, pill, className = '', children }) => {
  const [minimized, setMinimized] = useState<boolean>(() => {
    try {
      return localStorage.getItem(hudMinKey(id)) === '1';
    } catch {
      return false;
    }
  });

  const toggleMinimized = () => {
    setMinimized(prev => {
      const next = !prev;
      try {
        localStorage.setItem(hudMinKey(id), next ? '1' : '0');
      } catch {
        /* storage unavailable (private mode) — state stays session-local */
      }
      return next;
    });
  };

  return (
    <div
      className={`hud-overlay ${className}${minimized ? ' hud-minimized' : ''}`}
      data-hud-chip={id}
    >
      <button
        type="button"
        className="hud-chip-tab"
        onClick={toggleMinimized}
        aria-label={minimized ? 'Restore HUD readout' : 'Minimize HUD readout'}
        title={minimized ? 'Restore' : 'Minimize'}
      >
        {minimized ? '+' : '–'}
      </button>
      {minimized ? <div className="hud-pill">{pill}</div> : children}
    </div>
  );
};

/**
 * QuantumRefineryStrip — the docked counterpart to the Quantum Drive console.
 * Refining shards into charges requires being docked (Class-3+/SpaceDock,
 * server-enforced), but the full drive console only mounts while undocked —
 * so the jump loop would dead-end without this. A slim strip above the
 * trading monitor closes the loop. Reuses the qd-inventory CRT styling.
 */
interface QuantumRefineryStripProps {
  status: import('../../contexts/GameContext').QuantumStatus | null;
  onRefine: () => Promise<{ quantum_charges: number; quantum_shards: number }>;
}

const QuantumRefineryStrip: React.FC<QuantumRefineryStripProps> = ({ status, onRefine }) => {
  const [isRefining, setIsRefining] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const ready = !!status;
  const shards = status?.quantum_shards ?? 0;
  const charges = status?.quantum_charges ?? 0;

  const handleRefine = async () => {
    if (isRefining || shards < 1) return;
    setIsRefining(true);
    setError(null);
    setNotice(null);
    try {
      const result = await onRefine();
      setNotice(`Charge refined — ${result.quantum_charges} loaded, ${result.quantum_shards} shards remain.`);
    } catch (e: any) {
      setError(e?.response?.data?.detail || 'Charge refinement failed');
    } finally {
      setIsRefining(false);
    }
  };

  return (
    <div className="qd-refinery-strip">
      <div className="qd-refinery-head">QUANTUM DRIVE — REFINERY</div>
      {!ready ? (
        <div className="qd-inventory qd-inventory-linking" role="status" aria-live="polite">
          <span className="qd-linking-text">LINKING DRIVE…</span>
          <span className="qd-linking-spinner" aria-hidden="true">⟳</span>
        </div>
      ) : (
        <>
          <div className="qd-inventory">
            <div className="qd-inv-item" title="Quantum shards (raw)">
              <span className="qd-inv-icon">💠</span>
              <span className="qd-inv-count">{shards}</span>
              <span className="qd-inv-label">SHARDS</span>
            </div>
            <div className="qd-inv-item" title="Refined charges loaded in the drive">
              <span className="qd-inv-icon">⚡</span>
              <span className="qd-inv-count">{charges}</span>
              <span className="qd-inv-label">CHARGES</span>
            </div>
            <button
              className="qd-refine-btn"
              onClick={handleRefine}
              disabled={isRefining || shards < 1}
              title={shards < 1 ? 'No shards to refine' : 'Refine 1 shard into 1 drive charge'}
            >
              {isRefining ? 'REFINING…' : 'REFINE CHARGE'}
            </button>
          </div>
          {error && <div className="qd-inline-error">{error}</div>}
          {notice && <div className="qd-refinery-notice">{notice}</div>}
        </>
      )}
    </div>
  );
};

/**
 * TerraformHeaderPanel — the terraform readout in the planetary-ops header.
 *
 * Replaces the old fiction (a `terraform_level` column that does not exist
 * plus an invented growth-bonus table) with the real terraforming pipeline:
 *   GET  /planets/{id}/terraforming/status  (owner-only; lazily advances
 *        population-scaled ticks server-side)
 *   POST /planets/{id}/terraforming/start   (level 1-5, real ladder costs)
 * Non-owners see the real habitability score only. Reuses the existing
 * header-terra-* CRT styling from cockpit.css.
 */
const getApiBaseUrl = () => {
  if (import.meta.env.VITE_API_URL) {
    return import.meta.env.VITE_API_URL;
  }
  // Current origin leverages the Vite proxy (same pattern as GameContext)
  return window.location.origin;
};

interface TerraformLevelInfo {
  level: number;
  name: string;
  creditCost: number;
  durationHours: number;
  habitabilityBoost: number;
  organicsCost: number;
  equipmentCost: number;
}

interface TerraformStatus {
  active: boolean;
  currentHabitability: number;
  terraformingTarget: number | null;
  progress: number | null;
  level?: number | null;
  levelName?: string | null;
  estimatedTicksRemaining?: number | null;
  tickPeriodHours?: number | null;
  estimatedCompletion?: string | null;
  populationBonus?: string;
  availableLevels?: Record<string, TerraformLevelInfo>;
}

// Terraforming becomes unavailable once habitability reaches this score
// (server enforces the same MIN_TARGET; mirrored here for the inline reason).
const TERRAFORM_MAX_HABITABILITY = 90;

// Render an absolute estimatedCompletion (ISO) as a compact, human countdown
// ("~3h 20m left" / "~12m left"). Falls back to null when the field is absent
// (legacy projects) so the caller can degrade to the tick readout.
const formatTimeRemaining = (estimatedCompletion?: string | null): string | null => {
  if (!estimatedCompletion) return null;
  const ms = new Date(estimatedCompletion).getTime() - Date.now();
  if (Number.isNaN(ms)) return null;
  if (ms <= 0) return 'completing…';
  const totalMinutes = Math.round(ms / 60000);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours >= 24) {
    const days = Math.floor(hours / 24);
    const remHours = hours % 24;
    return `~${days}d ${remHours}h left`;
  }
  if (hours > 0) return `~${hours}h ${minutes}m left`;
  return `~${minutes}m left`;
};

const TerraformHeaderPanel: React.FC<{
  planetId?: string;
  isOwned: boolean;
  habitability: number;
  // Player's spendable credits and the LANDED planet's current resource
  // stockpiles. The backend debits creditCost from the player but consumes
  // organicsCost/equipmentCost from the PLANET's stock, so we need all three
  // to gate the START button and explain the requirement before the click.
  credits: number;
  planetOrganics: number;
  planetEquipment: number;
}> = ({ planetId, isOwned, habitability, credits, planetOrganics, planetEquipment }) => {
  // refreshPlayerState re-pulls credits after a terraforming START debits the
  // ladder cost server-side.
  const { refreshPlayerState } = useGame();
  const [status, setStatus] = useState<TerraformStatus | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [selectedLevel, setSelectedLevel] = useState(1);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setConfirming(false);
    if (!planetId || !isOwned) {
      setStatus(null);
      return;
    }
    const token = localStorage.getItem('accessToken');
    const load = () => {
      fetch(`${getApiBaseUrl()}/api/v1/planets/${planetId}/terraforming/status`, {
        headers: { Authorization: `Bearer ${token}` }
      })
        .then(r => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
        .then((data: TerraformStatus) => { if (!cancelled) setStatus(data); })
        .catch(() => { if (!cancelled) setStatus(null); });
    };
    load();
    return () => { cancelled = true; };
  }, [planetId, isOwned, refresh]);

  // Modest poll while a project is active so the server's lazy-advanced
  // progress and countdown stay live without hammering the endpoint. Only
  // polls when active; idle/non-owner panels never poll.
  useEffect(() => {
    if (!planetId || !isOwned || !status?.active) return;
    const id = setInterval(() => setRefresh(n => n + 1), 60000);
    return () => clearInterval(id);
  }, [planetId, isOwned, status?.active]);

  const handleStart = async () => {
    if (!planetId || busy) return;
    setBusy(true);
    setError(null);
    try {
      const token = localStorage.getItem('accessToken');
      const resp = await fetch(`${getApiBaseUrl()}/api/v1/planets/${planetId}/terraforming/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ target_level: selectedLevel })
      });
      const data = await resp.json().catch(() => null);
      if (!resp.ok) {
        // Surface the server's 400 detail verbatim (exact shortfall rules)
        throw new Error(data?.detail || `Terraforming start failed (HTTP ${resp.status})`);
      }
      setRefresh(n => n + 1);
      // START debited the ladder credit cost server-side; re-pull player
      // state so the cockpit credit readout reflects the spend immediately.
      try { await refreshPlayerState(); } catch { /* non-fatal */ }
    } catch (e: any) {
      setError(e?.message || 'Terraforming start failed');
    } finally {
      setConfirming(false);
      setBusy(false);
    }
  };

  const hab = status?.currentHabitability ?? habitability;
  const active = !!status?.active;
  const target = status?.terraformingTarget ?? null;
  const progress = typeof status?.progress === 'number' ? status.progress : null;

  // 5 segments: active projects fill by tick progress (20%/seg); idle
  // panels fill by habitability itself (20 points/seg) — both real values.
  const fillFraction = active && progress !== null ? progress / 100 : hab / 100;
  const segsFilled = Math.min(5, Math.floor(fillFraction * 5));

  const levels: TerraformLevelInfo[] = status?.availableLevels
    ? Object.values(status.availableLevels).sort((a, b) => a.level - b.level)
    : [];
  const selectedInfo = levels.find(l => l.level === selectedLevel) || null;

  // Reconcile selectedLevel with the server's actual availableLevels set. The
  // default useState(1) can desync when the server omits level 1 (e.g. the
  // planet's habitability is already past the L1 tier): selectedInfo would
  // resolve to null, the <select value={1}> would have no matching <option>,
  // and START could otherwise POST target_level:1 and re-trigger a 400. Snapping
  // to the first available level keeps the dropdown, the cost breakdown, and the
  // gate all pointed at a real, valid tier. (No conditional hooks precede this,
  // so placing the effect here is safe.)
  const levelKey = levels.map(l => l.level).join(',');
  useEffect(() => {
    if (levels.length === 0) return;
    if (!levels.some(l => l.level === selectedLevel)) {
      setSelectedLevel(levels[0].level);
    }
    // levelKey captures the available-level set; selectedLevel is read inside.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [levelKey]);

  // Affordability gate. The server debits credits from the player but consumes
  // organics + equipment from the PLANET's stockpile, so a freshly-colonized
  // planet (0 stock) cannot start a project even when the player is rich. We
  // surface the full requirement and disable START until every cost is met,
  // turning the old confusing 400 ("Insufficient organics on planet…") into an
  // upfront, explained condition.
  const haveCredits = !!selectedInfo && credits >= selectedInfo.creditCost;
  const haveOrganics = !!selectedInfo && planetOrganics >= selectedInfo.organicsCost;
  const haveEquipment = !!selectedInfo && planetEquipment >= selectedInfo.equipmentCost;
  // Defense-in-depth: a null/invalid selection is NOT affordable, so START stays
  // disabled rather than submitting an unvalidated target_level.
  const canAfford = !!selectedInfo && haveCredits && haveOrganics && haveEquipment;
  // Inline reason shown when something is short — credits first, then the
  // planet-stock resources the player likely didn't know were required.
  const shortfallMsg = selectedInfo && !canAfford
    ? (!haveCredits
        ? `Need ${selectedInfo.creditCost.toLocaleString()} cr (you have ${credits.toLocaleString()}).`
        : `Requires ${selectedInfo.organicsCost.toLocaleString()} organics + ${selectedInfo.equipmentCost.toLocaleString()} equipment (planet has ${Math.floor(planetOrganics).toLocaleString()} / ${Math.floor(planetEquipment).toLocaleString()}). Build up production first.`)
    : null;

  return (
    <div className="header-terraform">
      <div className="header-terra-top">
        <span className="header-terra-icon">🌱</span>
        {active ? (
          <>
            <span className="header-terra-name">{status?.levelName || 'TERRAFORMING'}</span>
            <span className="header-terra-level">{status?.level ? `L${status.level}` : ''} ACTIVE</span>
          </>
        ) : (
          <>
            <span className="header-terra-name">HABITABILITY</span>
            <span className="header-terra-level">{hab}%</span>
          </>
        )}
      </div>
      <div className="header-terra-bar">
        {[0, 1, 2, 3, 4].map(seg => (
          <div
            key={seg}
            className={`header-terra-seg ${seg < segsFilled ? 'filled' : ''} ${active && seg === segsFilled && segsFilled < 5 ? 'current' : ''}`}
          />
        ))}
      </div>
      {active ? (
        <>
          <div className="header-terra-desc">
            {hab}% → target {target ?? '—'}%
            {(() => {
              // Prefer the real countdown (estimatedCompletion); degrade to the
              // raw tick estimate only when the server omits it (legacy data).
              const remaining = formatTimeRemaining(status?.estimatedCompletion);
              if (remaining) return ` • ${remaining}`;
              return typeof status?.estimatedTicksRemaining === 'number'
                ? ` • ~${status.estimatedTicksRemaining} ticks left`
                : '';
            })()}
          </div>
          {status?.populationBonus && (
            <div className="header-terra-bonus">{status.populationBonus}</div>
          )}
        </>
      ) : (
        <>
          <div className="header-terra-desc">
            {isOwned
              ? (status ? 'No active terraforming project' : 'Terraforming telemetry unavailable')
              : 'Terraforming — owner telemetry only'}
          </div>
          {isOwned && status && hab >= TERRAFORM_MAX_HABITABILITY ? (
            // Server rejects START at/above MIN_TARGET; surface the reason
            // inline rather than presenting a control that always 400s.
            <div className="header-terra-bonus" style={{ opacity: 0.8 }}>
              Habitability {hab}% — terraforming unavailable at or above {TERRAFORM_MAX_HABITABILITY}%
            </div>
          ) : isOwned && status && levels.length > 0 && (
            confirming ? (
              <div className="header-terra-bonus">
                <button
                  className="header-terra-btn"
                  onClick={handleStart}
                  disabled={busy || !canAfford}
                  title={selectedInfo
                    ? `${selectedInfo.name}: ${selectedInfo.creditCost.toLocaleString()} cr + ${selectedInfo.organicsCost.toLocaleString()} organics + ${selectedInfo.equipmentCost.toLocaleString()} equipment (planet stock), +${selectedInfo.habitabilityBoost} hab over ${selectedInfo.durationHours}h`
                    : undefined}
                >
                  {busy ? 'STARTING…' : '✓ Confirm'}
                </button>
                <button className="header-terra-btn" onClick={() => setConfirming(false)} disabled={busy}>
                  ✕
                </button>
              </div>
            ) : (
              <>
                <div className="header-terra-bonus">
                  <select
                    value={selectedLevel}
                    onChange={e => setSelectedLevel(Number(e.target.value))}
                    style={{
                      background: 'rgba(0, 100, 50, 0.3)', color: '#00ff41',
                      border: '1px solid rgba(0, 255, 100, 0.4)', borderRadius: '3px',
                      font: 'inherit', fontSize: '0.6rem', padding: '0.15rem'
                    }}
                    aria-label="Terraforming level"
                  >
                    {levels.map(l => (
                      <option key={l.level} value={l.level}>
                        L{l.level} {l.name} — {l.creditCost.toLocaleString()} cr
                      </option>
                    ))}
                  </select>
                  <button
                    className="header-terra-btn"
                    onClick={() => setConfirming(true)}
                    disabled={!canAfford}
                    title={selectedInfo
                      ? `+${selectedInfo.habitabilityBoost} habitability over ${selectedInfo.durationHours}h — costs ${selectedInfo.creditCost.toLocaleString()} cr and consumes ${selectedInfo.organicsCost.toLocaleString()} organics + ${selectedInfo.equipmentCost.toLocaleString()} equipment from planet stock`
                      : undefined}
                  >
                    START
                  </button>
                </div>
                {/* Full cost breakdown for the selected level — all THREE costs
                    (credits debited from the player; organics + equipment
                    consumed from the planet's stock) plus duration and the
                    habitability gain. Each resource line shows the planet's
                    current stockpile next to the requirement, and turns red
                    when short, so the player understands the requirement
                    BEFORE clicking rather than hitting a 400. */}
                {selectedInfo && (
                  <div className="header-terra-desc" style={{ lineHeight: 1.5 }}>
                    <div style={{ color: haveCredits ? undefined : '#ff6b6b' }}>
                      💰 {selectedInfo.creditCost.toLocaleString()} cr
                      {!haveCredits && ` (have ${credits.toLocaleString()})`}
                    </div>
                    <div style={{ color: haveOrganics ? undefined : '#ff6b6b' }}>
                      🌿 {selectedInfo.organicsCost.toLocaleString()} organics
                      {' '}(planet has {Math.floor(planetOrganics).toLocaleString()})
                    </div>
                    <div style={{ color: haveEquipment ? undefined : '#ff6b6b' }}>
                      🔧 {selectedInfo.equipmentCost.toLocaleString()} equipment
                      {' '}(planet has {Math.floor(planetEquipment).toLocaleString()})
                    </div>
                    <div style={{ opacity: 0.85 }}>
                      +{selectedInfo.habitabilityBoost} hab over {selectedInfo.durationHours}h
                    </div>
                  </div>
                )}
                {shortfallMsg && (
                  <div className="header-terra-bonus" role="status" style={{ color: '#ff6b6b', opacity: 0.95 }}>
                    {shortfallMsg}
                  </div>
                )}
              </>
            )
          )}
        </>
      )}
      {error && (
        <div className="header-terra-desc" role="alert" style={{ color: '#ff6b6b' }}>
          {error}
        </div>
      )}
    </div>
  );
};

const GameDashboard: React.FC = () => {
  const {
    playerState,
    currentShip,
    currentSector,
    planetsInSector,
    stationsInSector,
    availableMoves,
    moveToSector,
    dockAtStation,
    undockFromStation,
    claimPlanet,
    landOnPlanet,
    leavePlanet,
    renamePlanet,
    getPlanetDetails,
    transferColonists,
    updatePlanetAllocation,
    getCitadelInfo,
    upgradeCitadel,
    cancelCitadelUpgrade,
    getDefenseBuildings,
    buildDefenseBuilding,
    depositToSafe,
    withdrawFromSafe,
    depositCommodityToSafe,
    withdrawCommodityFromSafe,
    setCitadelAutoDeposit,
    getPlanetDefenseInfo,
    upgradeShields,
    exploreCurrentLocation,
    getAvailableMoves,
    refreshPlayerState,
    quantumStatus,
    refineQuantumCharge,
    error
  } = useGame();
  const { getIcon: getResourceIcon, getLabel: getResourceLabel } = useResourceCatalog();

  const autopilot = useAutopilot();

  const { requiresFirstLogin } = useFirstLogin();
  const { sectorPlayers } = useWebSocket();

  // Autopilot plot input state (NAV monitor destination field)
  const [plotTarget, setPlotTarget] = useState('');

  const [movementResult, setMovementResult] = useState<any>(null);
  const [dockingResult, setDockingResult] = useState<any>(null);
  const [landingResult, setLandingResult] = useState<any>(null);

  // Asteroid-harvest feedback (WO-UI-MINING): the harvest action result banner.
  // {success} carries the yield (ore/pm/shards), turns spent + remaining, and a
  // flag for the unlicensed-AM rep penalty; on failure it carries a player-facing
  // gate message keyed off the service reason code. Auto-clears like the helm
  // banners below. Separate busy latch so a harvest can't double-fire and so the
  // button reads "MINING…" without dimming the rest of the rail.
  const [harvestResult, setHarvestResult] = useState<any>(null);
  const [harvestBusy, setHarvestBusy] = useState(false);

  // Special-formation investigation (WO-UI-ANOMALY): which discovered formations
  // this player has already investigated this session (the chip disables once
  // investigated; a 409 from the server also feeds this set so a stale chip is
  // corrected), the in-flight latch (per-formation id), and the reward banner.
  const [investigatedFormationIds, setInvestigatedFormationIds] = useState<Set<string>>(new Set());
  const [investigatingFormationId, setInvestigatingFormationId] = useState<string | null>(null);
  const [investigateResult, setInvestigateResult] = useState<any>(null);

  // Shell portal targets (WO-UI0-SHELL-TRANSPLANT): GameLayout's `.band`/
  // `.deck` slots, published via context. `bandEl`/`deckEl` are null until
  // GameLayout mounts them (or if this component is ever rendered without a
  // real GameLayout ancestor, e.g. the GameDashboard.*.test.tsx suite, which
  // mocks GameLayout out entirely) -- the two portal sites below fall back
  // to rendering their content INLINE (exactly where it always rendered) in
  // that case, so those tests keep seeing the identical DOM shape they
  // always have; in production the very next commit after GameLayout's
  // callback-refs fire portals correctly, same commit as first paint.
  const { bandEl, deckEl } = useShellSlots();

  // Ghost-on-approach for the HUD glass chips: they are pointer-events:none
  // (clicks pass through to the scene), so :hover can never fire on them.
  // Instead, measure cursor proximity to each chip's rect on windshield
  // mousemove and toggle .hud-ghost (CSS fades the chip to ~0.12 opacity).
  // rAF-throttled; classes are applied imperatively so there is no per-move
  // React state churn.
  const windshieldRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const band = windshieldRef.current;
    if (!band) return;
    const PROXIMITY = 20;
    let rafId: number | null = null;
    let cx = -1e6;
    let cy = -1e6;
    const apply = () => {
      rafId = null;
      band.querySelectorAll<HTMLElement>('[data-hud-chip]').forEach((chip) => {
        const r = chip.getBoundingClientRect();
        const near =
          cx >= r.left - PROXIMITY && cx <= r.right + PROXIMITY &&
          cy >= r.top - PROXIMITY && cy <= r.bottom + PROXIMITY;
        chip.classList.toggle('hud-ghost', near);
      });
    };
    const schedule = () => {
      if (rafId === null) rafId = requestAnimationFrame(apply);
    };
    const onMove = (e: MouseEvent) => {
      cx = e.clientX;
      cy = e.clientY;
      schedule();
    };
    const onLeave = () => {
      cx = -1e6;
      cy = -1e6;
      schedule();
    };
    band.addEventListener('mousemove', onMove);
    band.addEventListener('mouseleave', onLeave);
    return () => {
      band.removeEventListener('mousemove', onMove);
      band.removeEventListener('mouseleave', onLeave);
      if (rafId !== null) cancelAnimationFrame(rafId);
    };
  }, []);

  // Helm-rail busy latch: the dock/land/undock/liftoff actions each round-trip
  // the server (and change is_docked/is_landed, which re-renders the whole
  // rail). Guard against double-fire — a second click before the state flips
  // would issue a duplicate transition — by disabling+dimming the rail while
  // any one of them is in flight.
  const [helmBusy, setHelmBusy] = useState(false);

  // Docked trading-station terminal: trade desk or the Port Office registry.
  // SpaceDocks/TradeDocks reach the Port Office through their own venue hub.
  const [stationTerminal, setStationTerminal] = useState<'trade' | 'portoffice' | 'contracts'>('trade');
  useEffect(() => {
    setStationTerminal('trade');
  }, [playerState?.current_port_id]);

  // Same pattern for the planet-surface scene: a few seconds after landing the
  // low-value surface vista collapses to a thin strip, handing the band to the
  // planetary console (parity with the docked station bay). Resets on liftoff.
  const [landedChromeMin, setLandedChromeMin] = useState(false);
  useEffect(() => {
    if (!playerState?.is_landed) { setLandedChromeMin(false); return; }
    setLandedChromeMin(false); // start expanded so the surface "lands" visibly
    const t = window.setTimeout(() => setLandedChromeMin(true), 3500);
    return () => window.clearTimeout(t);
  }, [playerState?.is_landed, playerState?.current_planet_id]);

  // NAV monitor mode (WO-UI2-DECK-RECONCILE, §05: [COURSE · CHART · DRIVE]):
  // COURSE (adjacent-exit MOVE + plotted-course PLOT/ENGAGE, its own page --
  // was crammed into the shared header), CHART (the astrogation chart, was
  // "WARP GRAPH"), DRIVE (Warp-Jumper-only quantum console, was "QUANTUM
  // DRIVE" -- still WJ-gated, non-WJ hulls never see this tab).
  const isWarpJumper = currentShip?.type === 'WARP_JUMPER';
  const [navMode, setNavMode] = useState<'course' | 'chart' | 'drive'>('course');
  // CHART render mode -- 2D force-graph (NavigationMap, default) or 3D
  // (Galaxy3DRenderer). Independent of navMode: only meaningful while
  // navMode==='chart' -- WO-UI2-CHART-MONITOR.
  const [navChartMode, setNavChartMode] = useState<'2d' | '3d'>('2d');

  // SOLAR SYSTEM monitor mode (WO-UI2-DECK-RECONCILE, §05: [SYSTEM ·
  // SALVAGE · SIGNALS]): SYSTEM (the planet/station list, hazard/radiation
  // readout folded in -- no more standalone HAZARDS page), SALVAGE (wreck
  // rows), SIGNALS (discovered formations -- was surfaced on the old
  // HAZARDS page, now its own tab; was "BODIES"/"HAZARDS").
  const [systemPage, setSystemPage] = useState<'system' | 'salvage' | 'signals'>('system');
  const [showGatewright, setShowGatewright] = useState(false);

  // Region-owner invite/tradedock/governance state + probe RELOCATED to
  // components/governance/RegionOwnerControls.tsx (WO-UI0-STATUSBAR
  // integration) — it now mounts inside StatusBar's LocationDropdown, an
  // ancestor of this component, and is fully self-contained there.

  // Swapping off the Warp Jumper drops back to COURSE and closes the
  // Gatewright panel — neither the DRIVE page nor Gatewright exist without
  // the quantum drive.
  useEffect(() => {
    if (!isWarpJumper) {
      setNavMode('course');
      setShowGatewright(false);
    }
  }, [isWarpJumper]);

  // NAV scan loading state: show a spinner while sector telemetry loads,
  // then fall back to a retry affordance if nothing arrives within 10s.
  const [navScanTimedOut, setNavScanTimedOut] = useState(false);
  const [navScanAttempt, setNavScanAttempt] = useState(0);

  useEffect(() => {
    if (currentSector) {
      setNavScanTimedOut(false);
      return;
    }
    const timer = setTimeout(() => setNavScanTimedOut(true), 10000);
    return () => clearTimeout(timer);
  }, [currentSector, navScanAttempt]);

  const handleRetryScan = async () => {
    setNavScanTimedOut(false);
    setNavScanAttempt(attempt => attempt + 1); // re-arm the 10s timeout
    await Promise.allSettled([exploreCurrentLocation(), getAvailableMoves()]);
  };

  // COMMS contacts: merge live WebSocket presence with the sector snapshot
  // from the API (players_present), excluding ourselves, de-duplicated.
  const sectorContacts = useMemo(() => {
    const contacts = new Map<string, any>();
    const addContact = (contact: any) => {
      if (!contact) return;
      // Real players surface twice — once from live WS presence (keyed only
      // by user_id) and once from the API snapshot (carries player_id +
      // username, the hailable form). Keying real players on a normalized
      // (lowercased) username collapses both into one row; NPC presence
      // entries carry their NPCCharacter id in player_id and have no stable
      // username, so key those on player_id to keep same-named captains
      // distinct. Fall back to user_id/id only when neither is available.
      const key = contact.is_npc
        ? String(contact.player_id || contact.user_id || contact.id || '')
        : String(
            (contact.username && contact.username.toLowerCase()) ||
            contact.user_id || contact.id || ''
          );
      if (!key) return;
      const isSelf = playerState && (
        key === String(playerState.id) ||
        (contact.username && playerState.username &&
         contact.username.toLowerCase() === playerState.username.toLowerCase())
      );
      if (isSelf) return;
      const existing = contacts.get(key);
      if (!existing) {
        contacts.set(key, contact);
      } else if (!existing.player_id && contact.player_id) {
        // Prefer the entry carrying player_id so the surviving row is
        // hailable — merge the snapshot's player_id (and richer fields)
        // over the bare WS-presence entry without losing either source.
        contacts.set(key, { ...existing, ...contact });
      }
    };
    sectorPlayers.forEach(addContact);
    (currentSector?.players_present || []).forEach(addContact);
    return Array.from(contacts.values());
  }, [sectorPlayers, currentSector?.players_present, playerState]);

  // Ships present in the sector for the windshield viewport — the API snapshot
  // entries that carry ship telemetry (ship_id/ship_name/ship_type), excluding
  // our own ship. WS-presence rows have no ship fields, so we read
  // players_present directly rather than reusing the merged contact list.
  const shipsInSector = useMemo(() => {
    const self = playerState;
    return (currentSector?.players_present || []).filter((p: any) => {
      if (!p || !p.ship_id) return false;
      const isSelf = self && (
        String(p.player_id || '') === String(self.id) ||
        (p.username && self.username &&
         p.username.toLowerCase() === self.username.toLowerCase())
      );
      return !isSelf;
    });
  }, [currentSector?.players_present, playerState]);

  // COMMS↔viewport link: ship_id of the contact the player picked in the Comms
  // window. Its glyph gets a selection reticle in the cockpit viewport. Cleared
  // automatically when that ship is no longer present (it warped out, or the
  // player changed sector).
  const [selectedShipId, setSelectedShipId] = useState<string | null>(null);
  useEffect(() => {
    if (selectedShipId &&
        !shipsInSector.some((s: any) => String(s.ship_id) === selectedShipId)) {
      setSelectedShipId(null);
    }
  }, [shipsInSector, selectedShipId]);

  // SCAN layer feed (WO-UI2-LIVING-WINDSHIELD): the flight windshield's SCAN
  // toggle (SolarSystemViewscreen-local) renders sector wrecks alongside
  // special_formations. Also the SOLAR SYSTEM monitor's SALVAGE page (WO-
  // UI2-DECK-RECONCILE) -- one shared fetch, not two, `refetchSectorWrecks`
  // exposed so a completed/failed salvage can refresh the list without a
  // second independent GET. No context cache exists for wrecks (unlike
  // planets/stations/ships) -- mirrors the navChart effect above
  // (cancelled-flag guard, keyed on sector_id) but a failed/absent fetch
  // resolves to [] rather than keeping stale data, since a wreck list has no
  // meaningful "last known" fallback across a sector change.
  const [sectorWrecks, setSectorWrecks] = useState<SectorWreck[]>([]);
  const refetchSectorWrecks = useCallback(() => {
    if (currentSector?.sector_id == null) {
      setSectorWrecks([]);
      return;
    }
    sectorAPI.sectorWrecks(currentSector.sector_id)
      .then((rows) => setSectorWrecks(rows))
      .catch(() => setSectorWrecks([]));
  }, [currentSector?.sector_id]);
  useEffect(() => {
    let cancelled = false;
    if (currentSector?.sector_id == null) {
      setSectorWrecks([]);
      return;
    }
    sectorAPI.sectorWrecks(currentSector.sector_id)
      .then((rows) => { if (!cancelled) setSectorWrecks(rows); })
      .catch(() => { if (!cancelled) setSectorWrecks([]); });
    return () => { cancelled = true; };
  }, [currentSector?.sector_id]);

  // NAV map sectors: one node per destination sector. A sector reachable by
  // BOTH a warp and a tunnel used to be listed twice (duplicate React keys in
  // NavigationMap + phantom overlapping nodes), so build through a Map keyed
  // on sector_id — warp entries win, so the real sector type beats the
  // synthetic 'nebula' tunnel styling, which tunnel-only destinations keep.
  const navSectors = useMemo(() => {
    if (!currentSector) return [];

    // Sector number first so label truncation keeps it;
    // region suffix only when crossing regions
    const destinationName = (move: MoveOption): string => {
      const showRegion = move.region_id && move.region_id !== currentSector.region_id;
      return showRegion
        ? `Sector ${move.sector_number || move.sector_id} · ${move.region_name}`
        : `Sector ${move.sector_number || move.sector_id}`;
    };

    const byId = new Map<number, { id: number; name: string; type?: string; connected_sectors: number[]; depth?: number }>();

    // Current sector (connections deduped — a dual warp+tunnel destination
    // would otherwise draw the same edge twice)
    byId.set(currentSector.sector_id, {
      id: currentSector.sector_id,
      name: `Sector ${currentSector.sector_number || currentSector.sector_id}`,
      type: currentSector.type,
      connected_sectors: [...new Set([
        ...availableMoves.warps.map(w => w.sector_id),
        ...availableMoves.tunnels.map(t => t.sector_id)
      ])],
      depth: 0
    });

    // Available warp destinations
    availableMoves.warps.forEach(warp => {
      if (byId.has(warp.sector_id)) return;
      byId.set(warp.sector_id, {
        id: warp.sector_id,
        name: destinationName(warp),
        type: warp.type,
        connected_sectors: [currentSector.sector_id],
        depth: 1
      });
    });

    // Available tunnel destinations — skipped when the same sector is
    // already reachable by warp (the warp entry wins)
    availableMoves.tunnels.forEach(tunnel => {
      if (byId.has(tunnel.sector_id)) return;
      byId.set(tunnel.sector_id, {
        id: tunnel.sector_id,
        name: destinationName(tunnel),
        type: 'nebula',
        connected_sectors: [currentSector.sector_id],
        depth: 1
      });
    });

    return Array.from(byId.values());
  }, [currentSector, availableMoves]);

  // Deep known-graph feed (WO-NAV-MULTIHOP-FEED sub-part b): GET /nav/chart
  // returns the player's FULL known-space graph (visited ∪ corp-shared ∪
  // current), scanner-bounded and node-capped by chartToNavSectors.
  // `bounded=true` (WO-NAV-CHART-POLISH sub-part e) opts into the SERVER's
  // own scanner-depth bound (CHART_BOUNDED_DEPTH_CEILING=12) on top of the
  // client-side BFS+cap chartToNavSectors already applies -- a real ship
  // with a narrower effective scanner range now gets a visibly narrower
  // chart response, not just a client-truncated view of an unbounded one.
  // No-ship players (never true for a piloted cockpit view) are unaffected
  // per nav_service's own no-ship-is-unbounded guard. Fetched on mount and
  // refetched whenever the current sector changes (a warp/tunnel move can
  // grow the known set). scannerRange (the client-side BFS depth cap
  // passed to chartToNavSectors below) is passed as `undefined` (util
  // defaults to 12) -- neither ship.scanner_range nor ShipUpgradeService.
  // effective_scanner_range is exposed anywhere in the player-client API
  // surface today (grepped services/api.ts + contexts/), so there is no
  // live client-side value to pass; the server-side bound above is real
  // regardless. On a failed refetch the PREVIOUS chart is kept rather than
  // cleared -- still-valid known data, and the merge below always falls
  // back to the unaffected 1-hop `navSectors` regardless of chart state,
  // so the map is never blanked.
  const [navChart, setNavChart] = useState<NavChartResponse | null>(null);
  useEffect(() => {
    let cancelled = false;
    navAPI.getChart(true)
      .then((chart) => { if (!cancelled) setNavChart(chart); })
      .catch(() => { /* non-fatal -- keep last-known chart, see comment above */ });
    return () => { cancelled = true; };
  }, [currentSector?.sector_id]);

  // Scanner-bounded BFS neighborhood + frontier stubs + one-way edges, per
  // chartToNavSectors. Frontier rendering is ON (WO-NAV-CHART-POLISH) --
  // deepChartSectors' own `type: 'frontier'` entries flow straight into
  // the merge below now, rendered by NavigationMap as a distinct glyph.
  const { sectors: deepChartSectors, oneWayEdges } = useMemo(() => {
    if (!navChart || !currentSector) {
      return { sectors: [], frontierIds: [] as number[], truncated: false, oneWayEdges: [] as { from: number; to: number }[] };
    }
    return chartToNavSectors(navChart, currentSector.sector_id);
  }, [navChart, currentSector?.sector_id]);

  // MERGE (not replace) the deep graph into the 1-hop navSectors feed.
  // A literal replace would break Accept #4: /nav/chart classifies a
  // player's UNVISITED adjacent destinations as frontier, so a fresh
  // player's map would go nearly empty and unvisited-but-adjacent moves
  // (still clickable via availableMoves) would vanish. The 1-hop entries
  // are kept byte-for-byte (name/type), only their connected_sectors gets
  // unioned with the deep graph's -- deeper nodes render (untraversable,
  // since availableMoves only ever lists true 1-hop destinations) but the
  // existing adjacency/click-to-move surface is untouched. A deep-chart
  // entry whose id ALREADY has a clickable 1-hop entry (the Accept #4
  // collision -- nav_service classifies an unvisited adjacent destination
  // as frontier even though availableMoves lists it as directly warpable)
  // never downgrades that entry to a frontier stub; a genuinely
  // frontier-ONLY id (no 1-hop entry) is added as a new distinct-glyph
  // node (WO-NAV-CHART-POLISH sub-parts b/d).
  const mergedNavSectors = useMemo(() => {
    const byId = new Map<number, { id: number; name: string; type?: string; connected_sectors: number[]; depth?: number }>();
    navSectors.forEach(s => byId.set(s.id, { ...s, connected_sectors: [...s.connected_sectors] }));
    deepChartSectors.forEach(s => {
      const existing = byId.get(s.id);
      if (existing) {
        existing.connected_sectors = Array.from(new Set([...existing.connected_sectors, ...s.connected_sectors]));
        if (existing.depth === undefined) existing.depth = s.depth;
      } else {
        byId.set(s.id, { id: s.id, name: s.name, type: s.type, connected_sectors: [...s.connected_sectors], depth: s.depth });
      }
    });
    return Array.from(byId.values());
  }, [navSectors, deepChartSectors]);

  // Stable identity for the NavigationMap prop: an inline array literal would
  // be a new reference every render, re-running the map's node-init effect.
  const affordableMoveIds = useMemo(() => [
    ...availableMoves.warps.filter(w => w.can_afford).map(w => w.sector_id),
    ...availableMoves.tunnels.filter(t => t.can_afford).map(t => t.sector_id)
  ], [availableMoves]);

  // Turn cost per destination for the NAV map's warp prompt. Warps first,
  // tunnels skip sectors already present — the warp entry wins for
  // dual-reachable destinations, mirroring the navSectors dedup rule above.
  const moveCosts = useMemo(() => {
    const costs: Record<number, number> = {};
    availableMoves.warps.forEach(warp => {
      if (!(warp.sector_id in costs)) costs[warp.sector_id] = warp.turn_cost;
    });
    availableMoves.tunnels.forEach(tunnel => {
      if (!(tunnel.sector_id in costs)) costs[tunnel.sector_id] = tunnel.turn_cost;
    });
    return costs;
  }, [availableMoves]);

  // NAV[COURSE] adjacent-exit rows (WO-UI2-DECK-RECONCILE, §05: "COURSE:
  // adjacent exits → MOVE ▸ (1 click = 1 hop)"). One row per 1-hop
  // destination, warp entries win over a same-sector tunnel entry (mirrors
  // moveCosts' + navSectors' dedup rule above) -- the destination name
  // gets a region suffix only when the exit crosses region boundaries,
  // same display rule navSectors' own destinationName applies.
  const adjacentExits = useMemo(() => {
    if (!currentSector) return [] as MoveOption[];
    const byId = new Map<number, MoveOption>();
    const nameFor = (move: MoveOption): string => {
      const showRegion = move.region_id && move.region_id !== currentSector.region_id;
      return showRegion
        ? `${move.name} · ${move.region_name}`
        : move.name;
    };
    availableMoves.warps.forEach(warp => {
      if (!byId.has(warp.sector_id)) byId.set(warp.sector_id, { ...warp, name: nameFor(warp) });
    });
    availableMoves.tunnels.forEach(tunnel => {
      if (!byId.has(tunnel.sector_id)) byId.set(tunnel.sector_id, { ...tunnel, name: nameFor(tunnel) });
    });
    return Array.from(byId.values());
  }, [currentSector, availableMoves]);

  // Latch the first real (non-zero) sector_id so the docked/landed canvas
  // scenes seed from it on a COLD load instead of seeding from 0 and then
  // popping to a different terrain once currentSector arrives a tick later.
  // The flight scene fetches a per-sector snapshot so it's immune; only the
  // pure-paint docked/landed scenes (seeded straight off the id) pop.
  const latchedSceneSectorIdRef = useRef(0);
  if (currentSector?.sector_id && !latchedSceneSectorIdRef.current) {
    latchedSceneSectorIdRef.current = currentSector.sector_id;
  }
  const sceneSectorId = currentSector?.sector_id ?? latchedSceneSectorIdRef.current;

  // Landed planet — used by the windshield viewport and the colonist transfer UI
  const landedPlanet = useMemo(() => (
    playerState?.is_landed
      ? planetsInSector?.find((p: any) => p.id === playerState?.current_planet_id) || null
      : null
  ), [playerState?.is_landed, playerState?.current_planet_id, planetsInSector]);

  const isLandedPlanetMine = !!(landedPlanet && playerState && landedPlanet.owner_id === playerState.id);

  // Colonists riding in the current ship's cargo.
  // Cargo shape from /player/ships is {used, capacity, contents: {colonists: N}}
  // (the legacy flat shape is kept as a fallback).
  const shipColonists = useMemo(() => {
    const cargo = currentShip?.cargo as any;
    const aboard = cargo?.contents?.colonists ?? cargo?.colonists;
    return typeof aboard === 'number' && aboard > 0 ? Math.floor(aboard) : 0;
  }, [currentShip]);

  // opsRefresh: a shared "ops changed, refetch" signal bumped after the player's
  // own mutations (colonist transfer, citadel ops, building work) so the landed
  // console reflects them without a full reload. Declared here so BOTH the
  // landed-detail poll below and the citadel/defense telemetry effect can depend
  // on it. (WO-COCKPIT-UX A — refresh on own mutations.)
  const [opsRefresh, setOpsRefresh] = useState(0);

  // Detailed planet data (colonists / maxColonists) — the detail endpoint only
  // answers for planets the player owns, so render gracefully when absent
  const [landedPlanetDetail, setLandedPlanetDetail] = useState<any>(null);
  useEffect(() => {
    let cancelled = false;
    if (!landedPlanet || !isLandedPlanetMine) {
      setLandedPlanetDetail(null);
      return;
    }
    const planetId = landedPlanet.id;
    const fetchDetail = () => {
      getPlanetDetails(planetId)
        .then((detail: any) => { if (!cancelled) setLandedPlanetDetail(detail); })
        .catch(() => { if (!cancelled) setLandedPlanetDetail(null); });
    };
    fetchDetail();
    // Poll ~15s so production / stockpile / colonist / citadel accrual shows in
    // the landed cockpit without a full browser reload; pause while the tab is
    // hidden to avoid background churn. opsRefresh in deps re-fetches on the
    // player's own mutations. (WO-COCKPIT-UX A)
    const iv = setInterval(() => { if (!document.hidden) fetchDetail(); }, 15000);
    return () => { cancelled = true; clearInterval(iv); };
  }, [landedPlanet?.id, isLandedPlanetMine, opsRefresh]);

  // Colonists on the landed planet (detail when owned, sector snapshot otherwise)
  const landedPlanetColonists: number =
    typeof landedPlanetDetail?.colonists === 'number'
      ? landedPlanetDetail.colonists
      : landedPlanet?.population || 0;

  // Production allocation budget = the citadel WORKFORCE cap, never the raw
  // colonist headcount. Only `maxColonists` colonists can actually be assigned
  // to production; when colonists exceed that cap the surplus is idle, so the
  // sliders must not let you allocate past the workforce ("/ N" capacity shown).
  // Falls back to the colonist count when the cap is unknown.
  const allocBudget: number = (() => {
    const cap = Number(landedPlanetDetail?.maxColonists);
    return Number.isFinite(cap) && cap > 0
      ? Math.min(landedPlanetColonists, cap)
      : landedPlanetColonists;
  })();

  // --- Citadel + defense telemetry for the landed console ---
  // GET /planets/{id}/citadel answers only for the owner (400 otherwise);
  // GET /planets/{id}/defenses answers for anyone (scouting is allowed).
  // opsRefresh bumps re-fetch both after upgrades change them server-side.
  const [citadelInfo, setCitadelInfo] = useState<any>(null);
  const [defenseInfo, setDefenseInfo] = useState<any>(null);
  const [defenseBuildings, setDefenseBuildings] = useState<any[]>([]);
  const [buildingBusy, setBuildingBusy] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (!landedPlanet) {
      setCitadelInfo(null);
      setDefenseInfo(null);
      setDefenseBuildings([]);
      return;
    }
    getPlanetDefenseInfo(landedPlanet.id)
      .then((info: any) => { if (!cancelled) setDefenseInfo(info); })
      .catch(() => { if (!cancelled) setDefenseInfo(null); });
    if (isLandedPlanetMine) {
      getCitadelInfo(landedPlanet.id)
        .then((info: any) => { if (!cancelled) setCitadelInfo(info); })
        .catch(() => { if (!cancelled) setCitadelInfo(null); });
      getDefenseBuildings(landedPlanet.id)
        .then((res: any) => { if (!cancelled) setDefenseBuildings(Array.isArray(res?.buildings) ? res.buildings : []); })
        .catch(() => { if (!cancelled) setDefenseBuildings([]); });
    } else {
      setCitadelInfo(null);
      setDefenseBuildings([]);
    }
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [landedPlanet?.id, isLandedPlanetMine, opsRefresh]);

  // Live construction clock: while a citadel upgrade is in progress, tick every
  // second so the landed-console indicator counts down in real time, and bump
  // opsRefresh once the timer elapses so the finished level appears on its own.
  const [nowMs, setNowMs] = useState<number>(() => Date.now());
  // Any defense building still under construction this tick.
  const defenseBuildActive = defenseBuildings.some((b: any) => (b.queued_count ?? 0) > 0);
  useEffect(() => {
    if (!citadelInfo?.is_upgrading && !defenseBuildActive) return;
    // Soonest completion across the citadel upgrade AND every queued defense build.
    const times: number[] = [];
    if (citadelInfo?.is_upgrading && citadelInfo?.upgrade_complete_at) {
      times.push(new Date(citadelInfo.upgrade_complete_at).getTime());
    }
    defenseBuildings.forEach((b: any) =>
      (b.in_progress || []).forEach((p: any) => {
        if (p?.complete_at) times.push(new Date(p.complete_at).getTime());
      })
    );
    const soonest = times.length ? Math.min(...times) : null;
    const id = window.setInterval(() => {
      const t = Date.now();
      setNowMs(t);
      if (soonest && t >= soonest) {
        window.clearInterval(id);
        setOpsRefresh(n => n + 1); // re-fetch → server settles the finished build
      }
    }, 1000);
    return () => window.clearInterval(id);
  }, [citadelInfo?.is_upgrading, citadelInfo?.upgrade_complete_at, defenseBuildActive, defenseBuildings]);

  // Short "2d 4h" / "3h 12m" / "47s" countdown for the construction indicator.
  const fmtBuildCountdown = (ms: number): string => {
    const s = Math.max(0, Math.floor(ms / 1000));
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (d > 0) return `${d}d ${h}h`;
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${s % 60}s`;
    return `${s}s`;
  };

  // Live production projection: tick a clock every second while landed so the
  // displayed stockpiles climb on screen between polls (server accrues lazily
  // on read; we project base + rate×elapsed from lastProductionAt locally).
  const [prodNow, setProdNow] = useState<number>(() => Date.now());
  useEffect(() => {
    if (!landedPlanetDetail?.lastProductionAt) return;
    const id = window.setInterval(() => setProdNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [landedPlanetDetail?.lastProductionAt]);

  const projectedStock = (key: 'fuel' | 'organics' | 'equipment'): number => {
    const base = Number(landedPlanetDetail?.stockpiles?.[key] ?? 0);
    const rate = Number(landedPlanetDetail?.productionRates?.[key] ?? 0); // per day
    const anchorIso = landedPlanetDetail?.lastProductionAt;
    if (!anchorIso || rate <= 0) return base;
    // Clamp the projection to the SERVER's 24h accrual window so the on-screen
    // climb never out-runs what the server would actually grant on the next read
    // (the server caps lazy accrual at 24h of elapsed time).
    const elapsedS = Math.min(86400, Math.max(0, (prodNow - new Date(anchorIso).getTime()) / 1000));
    const projected = base + (rate / 86400) * elapsedS;
    // And never display above the storage cap — output past the cap is wasted, not
    // banked, so the readout would otherwise show stock that doesn't exist.
    const cap = Number(landedPlanetDetail?.storageCap ?? 0);
    return cap > 0 ? Math.min(projected, cap) : projected;
  };

  // WO-STORAGEDTO: the enforced per-resource storage cap (storageCap) and the
  // last server-stamped overflow event (overflowWarning) come straight from the
  // planet DTO. 0/absent = uncapped (un-citadeled colony) — show no bar in that
  // case (matches the server, which leaves an L0 colony unclamped). The bar +
  // warning let a player see they're about to start WASTING production at the cap.
  const storageCap: number = Number(landedPlanetDetail?.storageCap ?? 0);
  // A storage status per commodity: fill ratio (0..1), days until the cap is hit
  // at the current production rate (null = never / no rate / no cap), and the
  // at/near-cap flags that drive the warning badge.
  type StorageStatus = {
    capped: boolean;       // a positive cap is in force
    ratio: number;         // 0..1 fill against the cap
    daysUntilFull: number | null;
    atCap: boolean;        // already at/over the cap — production is being WASTED
    nearCap: boolean;      // >= 90% — about to start wasting
  };
  const storageStatus = (key: 'fuel' | 'organics' | 'equipment'): StorageStatus => {
    if (!(storageCap > 0)) {
      return { capped: false, ratio: 0, daysUntilFull: null, atCap: false, nearCap: false };
    }
    const stock = projectedStock(key);
    const ratio = Math.min(1, Math.max(0, stock / storageCap));
    const rate = Number(landedPlanetDetail?.productionRates?.[key] ?? 0); // per day
    const atCap = stock >= storageCap;
    const room = Math.max(0, storageCap - stock);
    const daysUntilFull = !atCap && rate > 0 ? room / rate : null;
    return { capped: true, ratio, daysUntilFull, atCap, nearCap: ratio >= 0.9 };
  };
  // Whether the server flagged any commodity overflow at the most recent tick —
  // an authoritative "you ARE losing production" signal (vs the projected nearCap).
  const overflowWarning = landedPlanetDetail?.overflowWarning;
  const overflowResources: string[] = overflowWarning && typeof overflowWarning === 'object'
    ? Object.keys(overflowWarning.resources || {})
    : [];
  const fmtDaysUntilFull = (d: number | null): string => {
    if (d === null) return '';
    if (d < 1) {
      const hrs = Math.max(1, Math.round(d * 24));
      return `~${hrs}h to cap`;
    }
    return `~${Math.round(d)}d to cap`;
  };

  // Planetary-ops notice (upgrade/safe outcomes), auto-dismissed like the
  // colonist transfer notice
  const [opsNotice, setOpsNotice] = useState<{ type: 'success' | 'error'; message: string } | null>(null);
  useEffect(() => {
    if (!opsNotice) return;
    const timer = setTimeout(() => setOpsNotice(null), 10000);
    return () => clearTimeout(timer);
  }, [opsNotice]);

  // Inline (non-native) confirm step for the two upgrade actions
  const [confirmUpgrade, setConfirmUpgrade] = useState<'shields' | 'citadel' | null>(null);
  const [upgradeBusy, setUpgradeBusy] = useState(false);
  // Two-step inline cancel for an in-progress citadel upgrade (no native dialog)
  const [cancelArmed, setCancelArmed] = useState(false);
  const [cancelBusy, setCancelBusy] = useState(false);

  // Inline planet rename (lives in the planetary-ops console; the old
  // native prompt() is gone — native dialogs freeze browser automation)
  const [renamingPlanet, setRenamingPlanet] = useState(false);
  const [renameValue, setRenameValue] = useState('');
  useEffect(() => {
    setRenamingPlanet(false);
  }, [landedPlanet?.id]);

  const handleUpgradeShields = async () => {
    if (!landedPlanet || upgradeBusy) return;
    setUpgradeBusy(true);
    try {
      const result = await upgradeShields(landedPlanet.id);
      const gen = result?.shieldGenerator;
      // ADR-0086: the upgrade is now time-based — credits are charged now, the
      // level advances when the build timer (target level x 6h) elapses.
      const hrs = Number(result?.buildHours || 0);
      setOpsNotice({
        type: 'success',
        message: `Shield generator upgrade to L${gen?.toLevel ?? '?'}${gen?.name ? ` (${gen.name})` : ''} started — ${Number(result?.creditsCost || 0).toLocaleString()} credits spent, ready in ${hrs}h.`
      });
      setOpsRefresh(n => n + 1);
    } catch (error: any) {
      // Surface the server's 400 detail verbatim (e.g. exact credit shortfall)
      setOpsNotice({
        type: 'error',
        message: error?.response?.data?.detail || 'Shield generator upgrade failed'
      });
    } finally {
      setConfirmUpgrade(null);
      setUpgradeBusy(false);
    }
  };

  const handleUpgradeCitadel = async () => {
    if (!landedPlanet || upgradeBusy) return;
    setUpgradeBusy(true);
    try {
      const result = await upgradeCitadel(landedPlanet.id);
      setOpsNotice({ type: 'success', message: result?.message || 'Citadel upgrade started.' });
      setOpsRefresh(n => n + 1);
    } catch (error: any) {
      // 400 detail carries the real rule (defense prerequisites, credit or
      // resource shortfalls, upgrade already running) — show it verbatim
      setOpsNotice({
        type: 'error',
        message: error?.response?.data?.detail || 'Citadel upgrade failed'
      });
    } finally {
      setConfirmUpgrade(null);
      setUpgradeBusy(false);
    }
  };

  const handleCancelCitadel = async () => {
    if (!landedPlanet || cancelBusy) return;
    setCancelBusy(true);
    try {
      const result = await cancelCitadelUpgrade(landedPlanet.id);
      setOpsNotice({ type: 'success', message: result?.message || 'Citadel upgrade cancelled.' });
      setOpsRefresh(n => n + 1);
    } catch (error: any) {
      setOpsNotice({ type: 'error', message: error?.response?.data?.detail || 'Cancel failed' });
    } finally {
      setCancelArmed(false);
      setCancelBusy(false);
    }
  };

  const handleBuildBuilding = async (buildingType: string) => {
    if (!landedPlanet || buildingBusy) return;
    setBuildingBusy(buildingType);
    try {
      const result = await buildDefenseBuilding(landedPlanet.id, buildingType);
      setOpsNotice({ type: 'success', message: result?.message || 'Defense building constructed.' });
      setOpsRefresh(n => n + 1);
    } catch (error: any) {
      setOpsNotice({ type: 'error', message: error?.response?.data?.detail || 'Construction failed' });
    } finally {
      setBuildingBusy(null);
    }
  };

  // --- Citadel safe — credit deposit / withdraw ---
  // The safe is ONE shared credit-equivalent vault: it holds credits AND
  // commodities under a single cap (no per-resource safe caps). These handlers
  // feed the unified SafeVaultPanel on the Safe tab; the panel owns the amount +
  // direction inputs and calls these with a validated amount.
  const [safeBusy, setSafeBusy] = useState(false);

  const safeCredits: number = Number(citadelInfo?.safe_credits ?? 0);
  const safeCapacity: number = Number(citadelInfo?.safe_storage ?? 0);
  // Total cr-equivalent value in the safe (credits + commodities) for the cap bar.
  const safeTotalValue: number = Number(citadelInfo?.safe_total_value ?? safeCredits);

  // Shared credit deposit/withdraw runner. `delta` is signed in cr-equivalent
  // terms (+amount on deposit, -amount on withdraw) so the cap bar's total tracks
  // the move even when the server response omits safe_total_value.
  const runSafeCredits = async (
    dir: 'deposit' | 'withdraw',
    amount: number,
  ) => {
    if (!landedPlanet || safeBusy || amount < 1) return;
    setSafeBusy(true);
    try {
      const result = dir === 'deposit'
        ? await depositToSafe(landedPlanet.id, amount)
        : await withdrawFromSafe(landedPlanet.id, amount);
      const delta = dir === 'deposit' ? amount : -amount;
      // safe_balance in the response is authoritative for the credit side; keep
      // the cr-equivalent total in step (use the server figure if it sends one).
      setCitadelInfo((prev: any) => prev ? {
        ...prev,
        safe_credits: typeof result?.safe_balance === 'number' ? result.safe_balance : prev.safe_credits + delta,
        safe_total_value: typeof result?.safe_total_value === 'number'
          ? result.safe_total_value
          : Number(prev.safe_total_value ?? prev.safe_credits ?? 0) + delta,
      } : prev);
      setOpsNotice({ type: 'success', message: result?.message || 'Vault transaction complete.' });
      // depositToSafe/withdrawFromSafe already refreshPlayerState() internally
      // (the wallet-bounded credit max recomputes off that) — no extra fetch here.
    } catch (error: any) {
      // Show the server's gating message verbatim (capacity, balance, level)
      setOpsNotice({
        type: 'error',
        message: error?.response?.data?.detail || 'Vault transaction failed'
      });
    } finally {
      setSafeBusy(false);
    }
  };

  const handleDepositCredits = (amount: number) => runSafeCredits('deposit', amount);
  const handleWithdrawCredits = (amount: number) => runSafeCredits('withdraw', amount);

  // --- Commodity safe storage (move planet stockpile <-> protected safe) ---
  const [commodityBusy, setCommodityBusy] = useState<string | null>(null);
  const [autoDepositBusy, setAutoDepositBusy] = useState(false);

  // Toggle "auto-deposit production into safe" (opt-in, default OFF). The server
  // is authoritative on the resulting flag — merge it back into citadelInfo so
  // the checkbox reflects the persisted state.
  const handleToggleAutoDeposit = async (enabled: boolean) => {
    if (!landedPlanet || autoDepositBusy) return;
    setAutoDepositBusy(true);
    try {
      const result = await setCitadelAutoDeposit(landedPlanet.id, enabled);
      const next = typeof result?.auto_deposit === 'boolean' ? result.auto_deposit : enabled;
      setCitadelInfo((prev: any) => (prev ? { ...prev, auto_deposit: next } : prev));
      setOpsNotice({
        type: 'success',
        message: next
          ? 'Auto-deposit enabled — production will be swept into the safe.'
          : 'Auto-deposit disabled.',
      });
    } catch (error: any) {
      setOpsNotice({ type: 'error', message: error?.response?.data?.detail || 'Could not change auto-deposit' });
    } finally {
      setAutoDepositBusy(false);
    }
  };
  // Planet stockpile keys (fuel/organics/equipment) -> safe commodity keys.
  // The SET stays a literal array — it's ADR-0082's fixed 3-commodity
  // safe-storable list, not the open resource catalog — but icon/name for
  // each key now come from the shared catalog (WO-ARCH-RES-3-FE-CATALOG)
  // instead of a locally-duplicated dict.
  const SAFE_COMMODITIES: { stock: 'fuel' | 'organics' | 'equipment'; safe: string; icon: string; name: string }[] = [
    { stock: 'fuel', safe: 'fuel_ore', icon: getResourceIcon('fuel_ore'), name: getResourceLabel('fuel_ore') },
    { stock: 'organics', safe: 'organics', icon: getResourceIcon('organics'), name: getResourceLabel('organics') },
    { stock: 'equipment', safe: 'equipment', icon: getResourceIcon('equipment'), name: getResourceLabel('equipment') },
  ];

  const moveCommoditySafe = async (dir: 'store' | 'take', safeKey: string, amount: number) => {
    if (!landedPlanet || commodityBusy || amount < 1) return;
    setCommodityBusy(safeKey);
    try {
      const result = dir === 'store'
        ? await depositCommodityToSafe(landedPlanet.id, safeKey, amount)
        : await withdrawCommodityFromSafe(landedPlanet.id, safeKey, amount);
      // Response is authoritative for the safe side; bump opsRefresh for the stockpile.
      setCitadelInfo((prev: any) => prev ? {
        ...prev,
        safe_commodities: result?.safe_commodities ?? prev.safe_commodities,
        safe_total_value: typeof result?.safe_total_value === 'number' ? result.safe_total_value : prev.safe_total_value,
      } : prev);
      const detail = await getPlanetDetails(landedPlanet.id).catch(() => null);
      if (detail) setLandedPlanetDetail(detail);
      setOpsNotice({ type: 'success', message: result?.message || 'Vault transaction complete.' });
    } catch (error: any) {
      setOpsNotice({ type: 'error', message: error?.response?.data?.detail || 'Vault transaction failed' });
    } finally {
      setCommodityBusy(null);
    }
  };

  // Store a planet-stockpile resource straight into the citadel safe from the
  // Production panel — reuses the EXISTING deposit-commodity flow (moveCommoditySafe
  // → depositCommodityToSafe), mapping the stockpile key to its safe key. No new
  // endpoint; identical to the Safe tab's "Store" button.
  const storeStockToSafe = (key: 'fuel' | 'organics' | 'equipment', amount: number) => {
    const safeKey = SAFE_COMMODITIES.find((c) => c.stock === key)?.safe;
    if (!safeKey || amount < 1) return;
    moveCommoditySafe('store', safeKey, amount);
  };

  // --- Colonist transfer modal (quantity pattern mirrors the trading modal) ---
  const [transferModal, setTransferModal] = useState<'disembark' | 'embark' | null>(null);
  const [transferQuantity, setTransferQuantity] = useState(1);
  const [isTransferring, setIsTransferring] = useState(false);
  const [transferNotice, setTransferNotice] = useState<{ type: 'success' | 'error'; message: string } | null>(null);

  // Colonist disembark ceiling = the LOWER of the citadel headcount cap
  // (baseMaxColonists) and the habitability demographic cap (maxPopulation) —
  // the server enforces BOTH (planets.py:982 citadel + :993 habitability; settle
  // clamps to min). NOT maxColonists/effectiveMaxColonists (habitability-scaled
  // DISPLAY value, not enforced) and NOT maxPopulation alone (misses the citadel
  // cap). Using baseMaxColonists alone over-filled the Max preset → server 400 when
  // habitability was the binding cap. (WO-LANDED-VITALS-FIX)
  const colonistHardCap = Math.min(
    Number(landedPlanetDetail?.baseMaxColonists ?? Infinity),
    Number(landedPlanetDetail?.maxPopulation ?? Infinity),
  );
  // Colonists you can still unload before hitting that lower cap. null until the
  // landed detail (with the cap fields) has loaded, so the readout shows '—'
  // instead of a transient/misleading 0.
  const transferHeadroom: number | null = (landedPlanetDetail && Number.isFinite(colonistHardCap))
    ? Math.max(0, colonistHardCap - Number(landedPlanetDetail?.colonists ?? 0))
    : null;

  const transferMax = useMemo(() => {
    if (transferModal === 'disembark') {
      let max = shipColonists;
      // Clamp to colonistHardCap (the lower of citadel & habitability caps) so the
      // Max preset, the "Room to add" readout, and the server all agree.
      if (Number.isFinite(colonistHardCap) && typeof landedPlanetDetail?.colonists === 'number') {
        max = Math.min(max, Math.max(0, colonistHardCap - landedPlanetDetail.colonists));
      }
      return max;
    }
    if (transferModal === 'embark') {
      return landedPlanetColonists;
    }
    return 0;
  }, [transferModal, shipColonists, landedPlanetDetail, landedPlanetColonists, colonistHardCap]);

  // Default the modal to a full load when it opens
  useEffect(() => {
    if (transferModal) setTransferQuantity(Math.max(1, transferMax));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [transferModal]);

  // Auto-dismiss transfer notices
  useEffect(() => {
    if (!transferNotice) return;
    const timer = setTimeout(() => setTransferNotice(null), 8000);
    return () => clearTimeout(timer);
  }, [transferNotice]);

  const clampTransferQuantity = (value: number) =>
    Math.max(1, Math.min(Math.max(1, transferMax), value));

  const openTransferModal = (action: 'disembark' | 'embark') => {
    setTransferNotice(null);
    setTransferModal(action);
  };

  const handleTransferConfirm = async () => {
    if (!landedPlanet || !transferModal || transferQuantity < 1 || isTransferring) return;
    const action = transferModal;
    setIsTransferring(true);
    try {
      const result = await transferColonists(landedPlanet.id, action, transferQuantity);
      setTransferNotice({
        type: 'success',
        message: result?.message || (action === 'disembark'
          ? `${transferQuantity.toLocaleString()} colonists unloaded to ${landedPlanet.name}`
          : `${transferQuantity.toLocaleString()} colonists loaded from ${landedPlanet.name}`)
      });
      // Sync the detail panel from the authoritative server response
      setLandedPlanetDetail((prev: any) => prev ? {
        ...prev,
        colonists: typeof result?.planet_colonists === 'number' ? result.planet_colonists : prev.colonists,
        maxColonists: typeof result?.max_colonists === 'number' ? result.max_colonists : prev.maxColonists
      } : prev);
      // Re-fetch authoritative detail + citadel/defense telemetry after the
      // mutation (production rates, morale, derived fields the optimistic merge
      // above doesn't cover). (WO-COCKPIT-UX A — refresh on own mutation.)
      setOpsRefresh((n) => n + 1);
      setTransferModal(null);
    } catch (error: any) {
      setTransferNotice({
        type: 'error',
        message: error?.response?.data?.detail || error?.response?.data?.message || 'Colonist transfer failed'
      });
      setTransferModal(null);
    } finally {
      setIsTransferring(false);
    }
  };

  // --- Claim ceremony / refusal notice ---
  const [claimCelebration, setClaimCelebration] = useState<{
    planetName: string;
    planetType: string;
    colonistsSettled?: number;
    creditsSpent?: number;
  } | null>(null);
  const [claimNotice, setClaimNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!claimCelebration) return;
    const timer = setTimeout(() => setClaimCelebration(null), 10000);
    return () => clearTimeout(timer);
  }, [claimCelebration]);

  // --- Special-formation discovery beat (WO-SFM) ---------------------------
  // When the current-sector poll/move returns a formation that has just flipped
  // is_discovered False→True (i.e. an ID we have never seen as discovered), fire
  // a brief celebration. We track previously-discovered IDs in a ref so the beat
  // fires ONCE on the flip, not on every 5 s poll that re-returns the same
  // discovered formation. The first render seeds the ref silently so revisiting
  // an already-known formation never celebrates.
  const seenDiscoveredFormationIdsRef = useRef<Set<string> | null>(null);
  const [formationDiscovery, setFormationDiscovery] = useState<{
    name: string;
    type?: string | null;
  } | null>(null);

  useEffect(() => {
    const formations = currentSector?.special_formations || [];
    const discoveredIds = formations.filter(f => f.is_discovered).map(f => f.id);

    // First observation: seed silently, no celebration for pre-known formations.
    if (seenDiscoveredFormationIdsRef.current === null) {
      seenDiscoveredFormationIdsRef.current = new Set(discoveredIds);
      return;
    }

    const seen = seenDiscoveredFormationIdsRef.current;
    const newlyDiscovered = formations.find(
      f => f.is_discovered && !seen.has(f.id)
    );
    // Record every currently-discovered id so we never re-fire for the same one.
    discoveredIds.forEach(id => seen.add(id));

    if (newlyDiscovered) {
      setFormationDiscovery({
        name: newlyDiscovered.name || 'UNNAMED FORMATION',
        type: newlyDiscovered.type,
      });
    }
  }, [currentSector?.special_formations]);

  useEffect(() => {
    if (!formationDiscovery) return;
    const timer = setTimeout(() => setFormationDiscovery(null), 7000);
    return () => clearTimeout(timer);
  }, [formationDiscovery]);

  useEffect(() => {
    if (!claimNotice) return;
    const timer = setTimeout(() => setClaimNotice(null), 10000);
    return () => clearTimeout(timer);
  }, [claimNotice]);

  // --- Production allocation (colonist HEADCOUNTS, not percentages) ---
  // The backend (PlanetaryService.allocate_colonists) stores fuel/organics/
  // equipment allocations as colonist counts and rejects totals beyond
  // planet.colonists; unassigned colonists are allowed. There are no ore or
  // terraform allocations server-side.
  const [allocations, setAllocations] = useState({ fuel: 0, organics: 0, equipment: 0 });
  const [allocRates, setAllocRates] = useState<any>(null);
  const [allocError, setAllocError] = useState<string | null>(null);
  const [allocSyncing, setAllocSyncing] = useState(false);
  // Last server-confirmed allocation — the revert target for optimistic edits
  const confirmedAllocations = useRef({ fuel: 0, organics: 0, equipment: 0 });
  const allocTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Seed from the authoritative planet detail (GET /planets/{id} returns
  // allocations {fuel, organics, equipment, unused} + productionRates)
  useEffect(() => {
    let seeded = {
      fuel: Number(landedPlanetDetail?.allocations?.fuel ?? 0),
      organics: Number(landedPlanetDetail?.allocations?.organics ?? 0),
      equipment: Number(landedPlanetDetail?.allocations?.equipment ?? 0)
    };
    // A planet over-allocated under the old (raw-colonist) budget would seed a
    // total above the workforce cap; scale it back proportionally so the sliders
    // show a valid state (the next persist writes the corrected values).
    const cap = Number(landedPlanetDetail?.maxColonists);
    const budget = Number.isFinite(cap) && cap > 0
      ? Math.min(Number(landedPlanetDetail?.colonists ?? cap), cap)
      : Number(landedPlanetDetail?.colonists ?? Infinity);
    const sum = seeded.fuel + seeded.organics + seeded.equipment;
    if (Number.isFinite(budget) && sum > budget && sum > 0) {
      const k = budget / sum;
      seeded = {
        fuel: Math.floor(seeded.fuel * k),
        organics: Math.floor(seeded.organics * k),
        equipment: Math.floor(seeded.equipment * k)
      };
    }
    setAllocations(seeded);
    confirmedAllocations.current = seeded;
    setAllocRates(landedPlanetDetail?.productionRates ?? null);
    setAllocError(null);
  }, [landedPlanetDetail]);

  // Cancel any pending allocation write when the planet changes / unmounts
  useEffect(() => () => {
    if (allocTimerRef.current) clearTimeout(allocTimerRef.current);
  }, [landedPlanet?.id]);

  // Debounced (~800ms) persist to PUT /planets/{id}/allocate with optimistic
  // UI: the sliders move immediately; on failure they snap back to the last
  // server-confirmed values and the server's error detail is shown verbatim.
  const persistAllocations = (planetId: string, next: { fuel: number; organics: number; equipment: number }) => {
    if (allocTimerRef.current) clearTimeout(allocTimerRef.current);
    allocTimerRef.current = setTimeout(async () => {
      setAllocSyncing(true);
      try {
        const result = await updatePlanetAllocation(planetId, next);
        confirmedAllocations.current = {
          fuel: Number(result?.allocations?.fuel ?? next.fuel),
          organics: Number(result?.allocations?.organics ?? next.organics),
          equipment: Number(result?.allocations?.equipment ?? next.equipment)
        };
        // +N/day readouts come from the server's confirmed rates
        if (result?.productionRates) setAllocRates(result.productionRates);
        setAllocError(null);
      } catch (error: any) {
        setAllocations(confirmedAllocations.current);
        setAllocError(error?.response?.data?.detail || 'Allocation update failed');
      } finally {
        setAllocSyncing(false);
      }
    }, 800);
  };

  // Set all three allocations at once (coupled sliders + presets). The coupling
  // math already conserves the workforce budget exactly; we defensively clamp the
  // SUM to allocBudget (proportional scale-down on the rare overshoot) and route
  // through the SAME optimistic/debounced/revert-on-fail persister.
  const handleSetAllocations = (next: { fuel: number; organics: number; equipment: number }) => {
    if (!landedPlanet || !isLandedPlanetMine) return;
    let { fuel, organics, equipment } = {
      fuel: Math.max(0, Math.round(next.fuel)),
      organics: Math.max(0, Math.round(next.organics)),
      equipment: Math.max(0, Math.round(next.equipment)),
    };
    const sum = fuel + organics + equipment;
    if (allocBudget > 0 && sum > allocBudget) {
      const k = allocBudget / sum;
      fuel = Math.floor(fuel * k);
      organics = Math.floor(organics * k);
      equipment = Math.floor(equipment * k);
    }
    const clamped = { fuel, organics, equipment };
    setAllocations(clamped);
    persistAllocations(landedPlanet.id, clamped);
  };

  // Per-colonist baseline yield for the sliders' HONEST, drag-tracking preview.
  // Built from the STABLE server-confirmed pair: the last persisted allocation
  // (confirmedAllocations.current — the optimistic-edit revert target) and the
  // productionRates that pair with it (allocRates, refreshed only on persist /
  // seed; falls back to the live poll's rates before the first persist). Both
  // reflect the SAME persisted state, so rate/allocation is the true per-colonist
  // yield. The slider then multiplies by the LIVE head-count, so the preview is
  // linear in the dragged value instead of collapsing to the stale current rate.
  // A role whose confirmed allocation is 0 (or whose rate is non-finite) has no
  // measured signal → null → the UI shows "—". Recomputed when the confirmed
  // baseline changes (keyed on allocRates + the poll), which is exactly when the
  // ref is updated alongside setAllocRates.
  const perColonistRates: PerColonistRates = useMemo(() => {
    const rates = allocRates ?? landedPlanetDetail?.productionRates;
    if (!rates) return null;
    const base = confirmedAllocations.current;
    const roles: ProdRole[] = ['fuel', 'organics', 'equipment'];
    const out: Partial<Record<ProdRole, number | null>> = {};
    for (const role of roles) {
      const alloc = Number(base?.[role] ?? 0);
      const rate = Number(rates?.[role] ?? 0);
      out[role] = alloc > 0 && Number.isFinite(rate) ? rate / alloc : null;
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allocRates, landedPlanetDetail]);

  // The station we're docked at — drives the docked scene HUD chips and the
  // helm rail legend (same resolution order the old bay header used).
  const dockedStation = useMemo(() => (
    playerState?.is_docked
      ? stationsInSector?.find((s: any) => s.id === playerState?.current_port_id) ||
        stationsInSector?.[0] || null
      : null
  ), [playerState?.is_docked, playerState?.current_port_id, stationsInSector]);

  // Determine if player is docked at a SpaceDock (has special services like genesis_dealer)
  const isDockedAtSpaceDock = useMemo(() => {
    if (!playerState?.is_docked || !playerState?.current_port_id || !stationsInSector) {
      return false;
    }

    const dockedStation = stationsInSector.find(
      (s: any) => s.id === playerState.current_port_id
    );

    if (!dockedStation) {
      return false;
    }

    // Check for SpaceDock indicators
    const services = dockedStation.services || {};
    return (
      services.genesis_dealer === true ||
      services.ship_dealer === true ||
      dockedStation.type?.toLowerCase() === 'shipyard' ||
      dockedStation.name?.toLowerCase().includes('spacedock') ||
      dockedStation.name?.toLowerCase().includes('tradedock')
    );
  }, [playerState?.is_docked, playerState?.current_port_id, stationsInSector]);
  
  useEffect(() => {
    // Clear results when sector changes
    setMovementResult(null);
    setDockingResult(null);
    setLandingResult(null);
  }, [currentSector?.id]);

  // Auto-dismiss cockpit alerts after 5 seconds
  const movementTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dockingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const landingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (movementTimerRef.current) clearTimeout(movementTimerRef.current);
    if (movementResult) {
      movementTimerRef.current = setTimeout(() => setMovementResult(null), 5000);
    }
    return () => { if (movementTimerRef.current) clearTimeout(movementTimerRef.current); };
  }, [movementResult]);

  useEffect(() => {
    if (dockingTimerRef.current) clearTimeout(dockingTimerRef.current);
    if (dockingResult) {
      dockingTimerRef.current = setTimeout(() => setDockingResult(null), 5000);
    }
    return () => { if (dockingTimerRef.current) clearTimeout(dockingTimerRef.current); };
  }, [dockingResult]);

  useEffect(() => {
    if (landingTimerRef.current) clearTimeout(landingTimerRef.current);
    if (landingResult) {
      landingTimerRef.current = setTimeout(() => setLandingResult(null), 5000);
    }
    return () => { if (landingTimerRef.current) clearTimeout(landingTimerRef.current); };
  }, [landingResult]);

  // Harvest + formation-investigate banners share the helm auto-dismiss cadence.
  const harvestTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (harvestTimerRef.current) clearTimeout(harvestTimerRef.current);
    if (harvestResult) {
      harvestTimerRef.current = setTimeout(() => setHarvestResult(null), 6000);
    }
    return () => { if (harvestTimerRef.current) clearTimeout(harvestTimerRef.current); };
  }, [harvestResult]);

  const investigateTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (investigateTimerRef.current) clearTimeout(investigateTimerRef.current);
    if (investigateResult) {
      investigateTimerRef.current = setTimeout(() => setInvestigateResult(null), 7000);
    }
    return () => { if (investigateTimerRef.current) clearTimeout(investigateTimerRef.current); };
  }, [investigateResult]);


  const handleMove = async (sectorId: number) => {
    try {
      const result = await moveToSector(sectorId);
      setMovementResult(result);
    } catch (error) {
      console.error('Error moving to sector:', error);
    }
  };
  
  const handleDock = async (stationId: string) => {
    if (helmBusy) return;
    autopilot.abort('manual helm action');
    setHelmBusy(true);
    try {
      const result = await dockAtStation(stationId);
      // Full station: dockAtStation surfaces the 409 payload as
      // {full: true, detail, queue_position, ...} — we were auto-enqueued,
      // NOT docked. Don't render the success banner for it.
      if (result?.full) {
        setDockingResult({
          full: true,
          message:
            (result.detail || 'All docking slips are occupied.') +
            (result.queue_position
              ? ` You are #${result.queue_position} in the docking queue.`
              : ''),
        });
        return;
      }
      setDockingResult(result);
    } catch (error) {
      console.error('Error docking at port:', error);
    } finally {
      setHelmBusy(false);
    }
  };

  const handleLand = async (planetId: string) => {
    if (helmBusy) return;
    autopilot.abort('manual helm action');
    setHelmBusy(true);
    try {
      const result = await landOnPlanet(planetId);
      setLandingResult(result);
    } catch (error) {
      console.error('Error landing on planet:', error);
    } finally {
      setHelmBusy(false);
    }
  };

  const handleClaim = async (planetId: string) => {
    setClaimNotice(null);
    // Capture the planet before the claim refreshes the sector snapshot
    const planet = planetsInSector?.find((p: any) => p.id === planetId);
    try {
      const result = await claimPlanet(planetId);
      setClaimCelebration({
        planetName: result?.planet_name || planet?.name || 'Unknown Planet',
        planetType: result?.planet_type || planet?.type || '',
        colonistsSettled: typeof result?.colonists_settled === 'number' ? result.colonists_settled : undefined,
        creditsSpent: typeof result?.credits_spent === 'number' ? result.credits_spent : undefined
      });
    } catch (error: any) {
      console.error('Error claiming planet:', error);
      // 403 carries the population-hub refusal fiction; 400 carries the
      // requirements message — show the server's words inline. Other
      // failures (500/network) already surface via the global cockpit
      // error alert; don't double-display them here.
      const statusCode = error?.response?.status;
      if (statusCode === 400 || statusCode === 403) {
        const detail = error?.response?.data?.detail || error?.response?.data?.message;
        setClaimNotice(typeof detail === 'string' && detail
          ? detail
          : 'Claim refused — 10,000 credits and at least 100 colonists aboard are required.');
      }
    }
  };

  const handleRenamePlanet = async (planetId: string, newName: string) => {
    try {
      await renamePlanet(planetId, newName);
      // Refresh the sector data to show the new name
      await exploreCurrentLocation();
      setOpsNotice({ type: 'success', message: `Planet registry updated — now designated "${newName}".` });
    } catch (error: any) {
      console.error('Error renaming planet:', error);
      // Inline notice, never a native alert (native dialogs freeze automation)
      setOpsNotice({
        type: 'error',
        message: error?.response?.data?.detail || 'Failed to rename planet. Please try again.'
      });
    }
  };

  const handleLeavePlanet = async () => {
    if (helmBusy) return;
    autopilot.abort('manual helm action');
    setHelmBusy(true);
    try {
      const result = await leavePlanet();
      setLandingResult(null); // Clear landing result on successful departure
      setMovementResult({
        message: result.message || 'Successfully departed from planet'
      });
    } catch (error) {
      console.error('Error leaving planet:', error);
    } finally {
      setHelmBusy(false);
    }
  };

  const handleUndock = async () => {
    if (helmBusy) return;
    autopilot.abort('manual helm action');
    setHelmBusy(true);
    try {
      await undockFromStation();
    } catch (error) {
      console.error('Error undocking:', error);
    } finally {
      setHelmBusy(false);
    }
  };

  // WO-UI-MINING — asteroid harvest. POST /api/v1/mining/harvest {ship_id}; the
  // server is authoritative (locks, turn spend, cargo grant, AM rep). Success
  // returns the yield + remaining turns; a failed gate returns a stable reason
  // code in the HTTP detail, which we translate to player-facing copy. Refresh
  // player state after a successful harvest so the cockpit turns/cargo reflect
  // the spend immediately. Uses the raw apiClient (the GameContext pattern).
  const HARVEST_GATE_COPY: Record<string, string> = {
    no_mining_laser: 'No mining laser equipped — fit one at a TradeDock to extract ore.',
    must_be_undocked: 'You must be undocked and in open space to deploy the mining laser.',
    cargo_full: 'Cargo hold is full — no room for ore. Sell or jettison before mining.',
    insufficient_turns: 'Not enough turns to run a harvest cycle.',
    not_an_asteroid_field: 'No asteroids here — harvesting requires an asteroid field.',
    ship_not_found: 'Active ship not found — re-select a ship and try again.',
  };

  const handleHarvest = async () => {
    if (harvestBusy) return;
    const shipId = currentShip?.id;
    if (!shipId) {
      setHarvestResult({ success: false, message: 'No active ship to mine with.' });
      return;
    }
    autopilot.abort('manual helm action');
    setHarvestBusy(true);
    try {
      const response = await apiClient.post('/api/v1/mining/harvest', { ship_id: shipId });
      setHarvestResult({ success: true, ...response.data });
      // Turns + cargo changed server-side — pull the fresh player state.
      await refreshPlayerState();
    } catch (error: any) {
      const reason = error?.response?.data?.detail;
      const message =
        (typeof reason === 'string' && (HARVEST_GATE_COPY[reason] || reason)) ||
        'Harvest failed. Please try again.';
      setHarvestResult({ success: false, message });
    } finally {
      setHarvestBusy(false);
    }
  };

  // WO-UI-ANOMALY — investigate a discovered special-formation. POST
  // /api/v1/player/formations/{id}/investigate grants a one-time reward; 404 if
  // the formation isn't discovered (no control is ever shown in that case), 409
  // if already investigated. On success or a 409 we mark the id investigated so
  // the chip's control disables; the 404 is surfaced (shouldn't normally happen
  // since the control only renders on discovered formations).
  const handleInvestigateFormation = async (formationId: string) => {
    if (investigatingFormationId) return;
    if (investigatedFormationIds.has(formationId)) return;
    setInvestigatingFormationId(formationId);
    try {
      const response = await apiClient.post(`/api/v1/player/formations/${formationId}/investigate`);
      setInvestigatedFormationIds(prev => new Set(prev).add(formationId));
      setInvestigateResult({ success: true, ...response.data });
    } catch (error: any) {
      const statusCode = error?.response?.status;
      if (statusCode === 409) {
        // Already investigated — reconcile the chip and tell the player.
        setInvestigatedFormationIds(prev => new Set(prev).add(formationId));
        setInvestigateResult({ success: false, message: 'This formation has already been investigated.' });
      } else if (statusCode === 404) {
        setInvestigateResult({ success: false, message: 'Formation not found or not yet discovered.' });
      } else {
        const detail = error?.response?.data?.detail;
        setInvestigateResult({
          success: false,
          message: (typeof detail === 'string' && detail) || 'Investigation failed. Please try again.',
        });
      }
    } finally {
      setInvestigatingFormationId(null);
    }
  };

  // Shared formation-badge-with-Investigate-control list — the windshield's
  // FORMATIONS HudChip and the SOLAR SYSTEM monitor's HAZARDS page (WO-
  // UI2-DECK-MONITORS) show the SAME currentSector.special_formations data;
  // this closure (over investigatedFormationIds/investigatingFormationId/
  // handleInvestigateFormation) is the one place that logic lives, reused
  // by both instead of a second hand-copied block.
  const renderFormationList = (formations: SpecialFormationSummary[]) => (
    <div className="hud-features">
      {formations.map(f => {
        const investigated = investigatedFormationIds.has(f.id);
        const investigating = investigatingFormationId === f.id;
        return (
          <div
            key={f.id}
            style={{ display: 'flex', alignItems: 'center', gap: '0.3rem' }}
          >
            <span
              className={`hud-badge${f.is_discovered ? '' : ' undiscovered'}`}
              title={f.is_discovered ? f.type?.replace(/_/g, ' ') : 'Unidentified anomaly — scan or explore to reveal'}
            >
              {f.is_discovered
                ? `${(f.name || 'UNNAMED').toUpperCase()}${f.type ? ` · ${f.type.replace(/_/g, ' ').toUpperCase()}` : ''}`
                : '❔ UNKNOWN ANOMALY'}
            </span>
            {f.is_discovered && (
              <button
                type="button"
                onClick={() => handleInvestigateFormation(f.id)}
                disabled={investigated || investigating}
                title={investigated
                  ? 'Already investigated'
                  : 'Investigate this anomaly for a one-time reward'}
                style={{
                  pointerEvents: 'auto',
                  padding: '0.15rem 0.45rem',
                  fontSize: '0.6rem',
                  fontWeight: 700,
                  letterSpacing: '0.08em',
                  textTransform: 'uppercase',
                  fontFamily: "'Courier New', monospace",
                  borderRadius: '3px',
                  background: investigated ? 'rgba(120, 130, 150, 0.1)' : 'rgba(0, 217, 255, 0.15)',
                  border: `1px solid ${investigated ? 'rgba(120, 130, 150, 0.35)' : 'rgba(0, 217, 255, 0.45)'}`,
                  color: investigated ? 'rgba(180, 190, 210, 0.6)' : '#00d9ff',
                  textShadow: investigated ? 'none' : '0 0 6px rgba(0, 217, 255, 0.5)',
                  cursor: (investigated || investigating) ? 'not-allowed' : 'pointer',
                  whiteSpace: 'nowrap',
                }}
              >
                {investigating ? '🔬 …' : investigated ? '✓ INVESTIGATED' : '🔬 INVESTIGATE'}
              </button>
            )}
          </div>
        );
      })}
    </div>
  );

  // If the player needs to complete the first login experience, the FirstLoginContainer
  // component will be shown by the App component, so we don't need to render the dashboard
  if (requiresFirstLogin) {
    return null;
  }

  return (
    <div className="game-dashboard cockpit-mode">
      {/* WO-INVERTED-L: the windshield band is ALWAYS present (the 34px
          green-bar collapse is retired). Docked/landed now EXPAND the console
          via .console-expand on the container (GameLayout), not by collapsing
          the scene — so .docked-min/.landed-min are no longer applied here. */}
        {/* System Alerts - Float over cockpit */}
        {error && (
          <div className="cockpit-alert error">
            <div className="alert-header">⚠️ SYSTEM ALERT</div>
            <div className="alert-message">{error}</div>
          </div>
        )}

        {claimNotice && (
          <div className="cockpit-alert claim-denied" role="alert">
            <div className="alert-header">
              <span>🛑 CLAIM DENIED</span>
              <button
                className="alert-dismiss"
                onClick={() => setClaimNotice(null)}
                aria-label="Dismiss claim notice"
              >
                ×
              </button>
            </div>
            <div className="alert-message">{claimNotice}</div>
          </div>
        )}

        {claimCelebration && (
          <div
            className="claim-celebration-overlay"
            role="dialog"
            aria-label="Colony established"
            onClick={() => setClaimCelebration(null)}
          >
            <div className="claim-celebration-card">
              <div className="claim-scanline" aria-hidden="true"></div>
              <div className="claim-banner">🏴 COLONY ESTABLISHED</div>
              <div className="claim-planet-icon">{getPlanetIcon(claimCelebration.planetType)}</div>
              <div className="claim-planet-name">{claimCelebration.planetName}</div>
              {typeof claimCelebration.colonistsSettled === 'number' && (
                <div className="claim-detail">👥 {claimCelebration.colonistsSettled.toLocaleString()} colonists settled</div>
              )}
              {typeof claimCelebration.creditsSpent === 'number' && (
                <div className="claim-detail">💰 {claimCelebration.creditsSpent.toLocaleString()} credits invested</div>
              )}
              <div className="claim-dismiss-hint">Click anywhere to continue</div>
            </div>
          </div>
        )}

        {movementResult && (
          <div className="cockpit-alert success">
            <div className="alert-header">✅ NAVIGATION COMPLETE</div>
            <div className="alert-message">{movementResult.message}</div>
            {movementResult.encounters && movementResult.encounters.length > 0 && (
              <div className="encounter-log">
                <div className="log-header">ENCOUNTER LOG:</div>
                <ul className="encounter-list">
                  {movementResult.encounters.map((encounter: any, index: number) => (
                    <li key={`encounter-${encounter.type}-${index}`} className="encounter-item">
                      {encounter.type === 'players' && (
                        <span>
                          👥 PLAYERS DETECTED: {encounter.players.length}
                        </span>
                      )}
                      {encounter.type === 'sector_hazard' && (
                        <span>
                          ⚠️ HAZARD: {encounter.hazard.toUpperCase()} (THREAT: {encounter.threat_level})
                        </span>
                      )}
                      {encounter.type === 'drones' && (
                        <span>
                          🤖 DEFENSE DRONES: {encounter.count} (THREAT: {encounter.threat_level})
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {dockingResult && (
          <div className={`cockpit-alert ${dockingResult.full ? 'claim-denied' : 'success'}`}>
            <div className="alert-header">
              {dockingResult.full ? '🛑 DOCKING QUEUE' : '🚀 DOCKING SUCCESSFUL'}
            </div>
            <div className="alert-message">{dockingResult.message}</div>
          </div>
        )}

        {landingResult && (
          <div className="cockpit-alert success">
            <div className="alert-header">🪐 PLANETARY LANDING</div>
            <div className="alert-message">{landingResult.message}</div>
            <div className="landing-details">
              <span>🌍 {landingResult.planet_name}</span>
              <span>🏷️ {landingResult.planet_type?.toUpperCase()}</span>
              <span>👥 Pop: {landingResult.population?.toLocaleString()}</span>
            </div>
          </div>
        )}

        {/* Special-formation discovery beat (WO-SFM): fires once when a formation
            in the current sector flips to discovered. Click anywhere on the
            alert to dismiss early; auto-clears after 7 s. */}
        {formationDiscovery && (
          <div
            className="cockpit-alert success"
            role="status"
            onClick={() => setFormationDiscovery(null)}
          >
            <div className="alert-header">🌀 FORMATION DISCOVERED</div>
            <div className="alert-message">
              {formationDiscovery.name.toUpperCase()}
              {formationDiscovery.type
                ? ` · ${formationDiscovery.type.replace(/_/g, ' ').toUpperCase()}`
                : ''}
            </div>
          </div>
        )}

        {/* Asteroid-harvest result (WO-UI-MINING): success shows the yield + turn
            spend; failure shows the translated gate message. Click to dismiss. */}
        {harvestResult && (
          <div
            className={`cockpit-alert ${harvestResult.success ? 'success' : 'error'}`}
            role="status"
            onClick={() => setHarvestResult(null)}
          >
            <div className="alert-header">
              {harvestResult.success ? '⛏️ HARVEST COMPLETE' : '⛏️ HARVEST FAILED'}
            </div>
            {harvestResult.success ? (
              <>
                <div className="alert-message">
                  +{harvestResult.ore ?? 0} ORE
                  {harvestResult.precious_metals ? ` · +${harvestResult.precious_metals} PRECIOUS METALS` : ''}
                  {harvestResult.quantum_shards ? ` · +${harvestResult.quantum_shards} QUANTUM SHARDS` : ''}
                  {' · '}<TurnsIcon /> {harvestResult.turns_spent ?? 0} SPENT
                  {typeof harvestResult.remaining_turns === 'number'
                    ? <>{' · '}<TurnsIcon /> {harvestResult.remaining_turns} LEFT</>
                    : null}
                </div>
                {harvestResult.am_rep_delta < 0 && (
                  <div className="alert-message">
                    ⚠️ UNLICENSED EXTRACTION — Astral Mining reputation {harvestResult.am_rep_delta}.
                  </div>
                )}
              </>
            ) : (
              <div className="alert-message">{harvestResult.message}</div>
            )}
          </div>
        )}

        {/* Formation-investigation result (WO-UI-ANOMALY): success shows the
            reward credits; failure (e.g. already investigated) shows the message.
            Click to dismiss. */}
        {investigateResult && (
          <div
            className={`cockpit-alert ${investigateResult.success ? 'success' : 'error'}`}
            role="status"
            onClick={() => setInvestigateResult(null)}
          >
            <div className="alert-header">
              {investigateResult.success ? '🔬 ANOMALY INVESTIGATED' : '🔬 INVESTIGATION FAILED'}
            </div>
            {investigateResult.success ? (
              <div className="alert-message">
                {investigateResult.formation?.name
                  ? `${String(investigateResult.formation.name).toUpperCase()} — `
                  : ''}
                REWARD: +{investigateResult.reward?.credits ?? 0} CR
                {typeof investigateResult.credits_remaining === 'number'
                  ? ` · BALANCE ${investigateResult.credits_remaining.toLocaleString()} CR`
                  : ''}
              </div>
            ) : (
              <div className="alert-message">{investigateResult.message}</div>
            )}
          </div>
        )}

        {/* WINDSHIELD - Full immersive viewport with HUD overlays.
            WO-UI0-SHELL-TRANSPLANT: portaled into GameLayout's `.band` slot
            (falls back to inline if bandEl isn't published yet — see the
            bandEl/deckEl doc-comment above). TWO changes only in this whole
            file per the WO: this IIFE wrap and the matching one around
            .cockpit-console below — none of the JSX between the open/close
            tags of either is touched. */}
        {(() => {
        const windshieldNode = (
        <div className="cockpit-windshield" ref={windshieldRef}>
          {/* LANDED STATE — planet-surface vista scene. GLASS LAW: the band
              hosts canvas scenery + absolutely-anchored HUD chips ONLY. The
              vitals/status/rename console moved to PLANETARY OPERATIONS
              COMMAND below; LIFT OFF lives on the green landed bar. */}
          {playerState?.is_landed && !playerState?.is_docked && (() => {
            const habitability = Math.max(0, Math.min(100, landedPlanet?.habitability_score ?? 0));
            // Only assert ownership once the planet record resolves —
            // before that, an em-dash, not a false "UNCLAIMED".
            const ownerText = !landedPlanet
              ? '—'
              : isLandedPlanetMine
                ? 'YOU'
                : landedPlanet.owner_name
                  ? landedPlanet.owner_name
                  : landedPlanet.owner_id ? 'OWNED' : 'UNCLAIMED';
            return (
            <>
              <SolarSystemViewscreen
                sectorId={sceneSectorId}
                scene="landed"
                planetType={landedPlanet?.type}
                habitability={landedPlanet?.habitability_score}
                citadelLevel={citadelInfo?.citadel_level ?? 0}
                landedPlanetId={playerState?.current_planet_id}
              />

              {/* Cockpit frame vignette */}
              <div className="windshield-frame">
                <div className="frame-corner top-left"></div>
                <div className="frame-corner top-right"></div>
                <div className="frame-corner bottom-left"></div>
                <div className="frame-corner bottom-right"></div>
              </div>

              {/* HUD chips — fixed anchors, never flow layout. The top-left
                  id="landed" chip (planet name/type + sector) was RETIRED at
                  the WO-UI0-STATUSBAR integration step — it was part of the
                  overlap-defect canvas-chip system; its readouts (planet
                  name/type, sector number) now live in StatusBar's
                  LocationDropdown (components/layouts/LocationDropdown.tsx),
                  which is scene-aware and shows them whenever is_landed. */}
              <HudChip id="owner" className="top-right" pill={<>👤 {ownerText}</>}>
                <div className="hud-label">👤 OWNER</div>
                <div className="hud-value hud-chip-name">{ownerText}</div>
              </HudChip>

              <HudChip
                id="habitability"
                className="bottom-right"
                pill={<>🌱 {landedPlanet ? `${habitability}%` : '—'}</>}
              >
                <div className="hud-label">HABITABILITY</div>
                {/* Habitability is only meaningful once the planet resolves —
                    a 0%/empty bar before that reads as a real (false) value. */}
                <div className="hud-value">{landedPlanet ? `${habitability}%` : '—'}</div>
                <div className="hud-bar">
                  <div className="hud-bar-fill" style={{ width: `${landedPlanet ? habitability : 0}%` }}></div>
                </div>
              </HudChip>

              {/* Expanded-surface corner controls — Minimize Surface + Lift Off
                  grouped as an absolute corner cluster OVER the vista (Max +
                  Orchestrator placement decision) so neither pushes/overlaps the
                  surface viewport. Scoped to the landed render, so the shared
                  docked-bay minimize button is unaffected. Lift Off must stay
                  reachable here because the green landed-min-bar (the other Lift
                  Off) is display:none until the surface minimizes — the two
                  chromes are mutually exclusive, so exactly one Lift Off shows. */}
              <div className="landed-surface-controls">
                <button
                  type="button"
                  className="bay-minimize-btn"
                  onClick={() => setLandedChromeMin(true)}
                  title="Minimize the surface view — expand the planetary console"
                >
                  ▴ MINIMIZE SURFACE
                </button>
                <button
                  type="button"
                  className="landed-min-liftoff expanded"
                  onClick={handleLeavePlanet}
                  disabled={helmBusy}
                  title="Lift off and depart this planet"
                >
                  {helmBusy ? '🚀 DEPARTING…' : '🚀 LIFT OFF & DEPART'}
                </button>
              </div>

              {/* Collapsed strip (shown only when minimized via CSS): planet
                  identity + expand affordance. */}
              <div className="landed-min-bar">
                <span className="landed-min-name">
                  🪐 LANDED — {(landedPlanet?.name || 'Planet').toUpperCase()}
                </span>
                {citadelInfo?.is_upgrading && (() => {
                  const startMs = citadelInfo.upgrade_started_at ? new Date(citadelInfo.upgrade_started_at).getTime() : null;
                  const endMs = citadelInfo.upgrade_complete_at ? new Date(citadelInfo.upgrade_complete_at).getTime() : null;
                  const pct = (startMs && endMs && endMs > startMs)
                    ? Math.min(100, Math.max(0, ((nowMs - startMs) / (endMs - startMs)) * 100)) : 0;
                  const remainMs = endMs ? Math.max(0, endMs - nowMs) : 0;
                  return <span className="landed-min-build" title="Citadel construction in progress">🏗️ {Math.round(pct)}% · {fmtBuildCountdown(remainMs)}</span>;
                })()}
                <span className="landed-min-actions">
                  <button
                    type="button"
                    className="landed-min-expand"
                    onClick={() => setLandedChromeMin(false)}
                    title="Expand the surface view"
                  >
                    ⤢ EXPAND SURFACE
                  </button>
                  {/* The single landing-level LIFT OFF lives here on the green
                      bar (WO 129-C); the gray helm-rail landed line and the
                      vitals-strip Lift Off were removed so there is exactly one. */}
                  <button
                    type="button"
                    className="landed-min-liftoff"
                    onClick={handleLeavePlanet}
                    disabled={helmBusy}
                    title="Lift off and depart this planet"
                  >
                    {helmBusy ? '🚀 DEPARTING…' : '🚀 LIFT OFF & DEPART'}
                  </button>
                </span>
              </div>
            </>
            );
          })()}

          {/* DOCKED STATE — station face, not a cockpit scene (WO-UI3-
              STATION-MODE). No SolarSystemViewscreen mount + no windshield-
              frame vignette here anymore — docked has its own flat identity
              band (`.station-face-bay-band`, game-layout.css, scoped under
              `.game-container.mode-station`) instead of a 3D bay canvas.
              Salvaged verbatim: the ⚓ CLAMPED HudChip (id="baystatus").
              DROPPED (not resurrected): the bay-minimize-btn/docked-min-bar
              pair that used to live here — already dead code before this WO
              (cockpit.css's own `.bay-minimize-btn { display: none
              !important; }` retired it when WO-INVERTED-L landed; the
              `.cockpit-mode.docked-min` class it targeted was never applied
              to any element). GameLayout's manual windshield-minimize/
              expand toggle (id=151, `--band-h`) this band used to
              collapse/grow with is ITSELF retired (WO-UI0-SHELL-TRANSPLANT
              — the band is now a fixed 8.5em height in station mode,
              cockpit-shell.css/game-layout.css, not a player-resizable
              one) — no minimize/expand affordance applies to this band
              anymore, by design. UNDOCK lives on the helm rail /
              SpaceDockInterface's persistent frame button, not here. */}
          {playerState?.is_docked && (
            <div className="station-face-bay-band" role="region" aria-label="Docked station">
              <span className="station-face-bay-band-name" role="heading" aria-level={2}>
                {isDockedAtSpaceDock ? '🚀' : '🏪'} DOCKED — {(dockedStation?.name || (isDockedAtSpaceDock ? 'SpaceDock' : 'Trading Station')).toUpperCase()}
              </span>
              <HudChip id="baystatus" className="top-right" pill={<>⚓ CLAMPED</>}>
                <div className="hud-label">BAY STATUS</div>
                <div className="hud-value hud-chip-name hud-chip-ok">CLAMPS ENGAGED</div>
              </HudChip>
            </div>
          )}

          {/* SPACE VIEW - Normal flight mode */}
          {!playerState?.is_docked && !playerState?.is_landed && currentSector && (
            <>
              {/* Space viewport - edge to edge */}
              <SolarSystemViewscreen
                sectorId={currentSector.sector_id}
                sectorType={currentSector.type?.toLowerCase() || 'normal'}
                sectorName={currentSector.name}
                hazardLevel={currentSector.hazard_level}
                radiationLevel={currentSector.radiation_level}
                stations={stationsInSector}
                planets={planetsInSector}
                ships={shipsInSector}
                onEntityClick={(entity) => {
                  // Legacy fallback viewport only (SectorViewport): the
                  // procedural scene now opens an info popup on click and
                  // routes actions through onRequestLand/onRequestDock.
                  if (entity.type === 'planet') {
                    handleLand(entity.id);
                  } else if (entity.type === 'station') {
                    handleDock(entity.id);
                  }
                }}
                onRequestLand={handleLand}
                onRequestDock={handleDock}
                selectedShipId={selectedShipId}
                onSelectShip={setSelectedShipId}
                wrecks={sectorWrecks}
                formations={currentSector.special_formations ?? []}
              />

              {/* Cockpit frame vignette */}
              <div className="windshield-frame">
                <div className="frame-corner top-left"></div>
                <div className="frame-corner top-right"></div>
                <div className="frame-corner bottom-left"></div>
                <div className="frame-corner bottom-right"></div>
              </div>

              {/* HUD Overlays. The top-left id="location" chip (sector/
                  region/CitizenshipBadge + region-owner controls) was
                  RETIRED at the WO-UI0-STATUSBAR integration step — it was
                  the overlap-defect canvas-chip system. Its location
                  readouts (sector number/type, region name, CitizenshipBadge)
                  now live in StatusBar's LocationDropdown
                  (components/layouts/LocationDropdown.tsx); its owner
                  controls (GOVERNANCE / region picker / INVITE CONTROL /
                  TRADEDOCK CONSTRUCTION + both portal modals) are now
                  components/governance/RegionOwnerControls.tsx, mounted
                  inside that same LocationDropdown. */}

              {currentSector.hazard_level > 0 && (
                <HudChip
                  id="hazard"
                  className="top-right hazard"
                  pill={<>⚠ {currentSector.hazard_level}/10</>}
                >
                  <div className="hud-label">⚠️ HAZARD</div>
                  <div className="hud-value danger">{currentSector.hazard_level}/10</div>
                  <div className="hud-bar">
                    <div className="hud-bar-fill danger" style={{ width: `${currentSector.hazard_level * 10}%` }}></div>
                  </div>
                </HudChip>
              )}

              {currentSector.radiation_level > 0 && (
                <HudChip
                  id="radiation"
                  className="bottom-right radiation"
                  pill={<>☢ {(currentSector.radiation_level * 100).toFixed(1)}%</>}
                >
                  <div className="hud-label">☢️ RADIATION</div>
                  <div className="hud-value warning">{(currentSector.radiation_level * 100).toFixed(1)}%</div>
                  <div className="hud-bar">
                    <div className="hud-bar-fill warning" style={{ width: `${currentSector.radiation_level * 100}%` }}></div>
                  </div>
                </HudChip>
              )}

              {currentSector.special_formations && currentSector.special_formations.length > 0 && (
                <HudChip
                  id="formations"
                  className="bottom-left formations"
                  pill={<>🌀 {currentSector.special_formations.length}</>}
                >
                  <div className="hud-label">🌀 FORMATIONS</div>
                  {/* WO-UI-ANOMALY: a discovered formation carries an Investigate
                      control (one-time reward). Undiscovered → label only, no
                      control. Already-investigated this session → disabled.
                      Shared with the SOLAR SYSTEM monitor's HAZARDS page
                      (renderFormationList, WO-UI2-DECK-MONITORS). */}
                  {renderFormationList(currentSector.special_formations)}
                </HudChip>
              )}

              {currentSector.special_features && currentSector.special_features.length > 0 && (
                <div className="hud-overlay bottom-left features" style={{ display: 'none' }}>
                  <div className="hud-label">ANOMALIES</div>
                  <div className="hud-features">
                    {currentSector.special_features.map(feature => (
                      <span key={feature} className="hud-badge">
                        {feature.replace(/_/g, ' ').toUpperCase()}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {currentSector.description && (
                <div className="hud-overlay bottom-center description" style={{ display: 'none' }}>
                  <div className="hud-description-text">{currentSector.description}</div>
                </div>
              )}
            </>
          )}
        </div>
        );
        return bandEl ? createPortal(windshieldNode, bandEl) : windshieldNode;
        })()}

        {/* CONSOLE - Metal panel with embedded monitors.
            WO-UI0-SHELL-TRANSPLANT: portaled into GameLayout's `.deck` slot
            (falls back to inline if deckEl isn't published yet — see the
            bandEl/deckEl doc-comment above). */}
        {(() => {
        const consoleNode = (
        <div className="cockpit-console">
          {/* DOCKED STATE: the station-face venue workspace (WO-UI3-STATION-
              MODE) — replaces the flight-monitor bezel wrapper
              (.console-monitor.trading-monitor.full-width + .monitor-bezel
              rivets, cockpit.css) with `.station-face-workspace`
              (game-layout.css, scoped under `.game-container.mode-station`).
              Everything below the bezel (QuantumRefineryStrip, monitor-
              screen, tabs, TradingInterface/SpaceDockInterface/venues) is
              the shipped venue workspace, salvaged verbatim. */}
          {playerState?.is_docked ? (
            <div className="station-face-workspace">
              {isWarpJumper && (
                <QuantumRefineryStrip status={quantumStatus} onRefine={refineQuantumCharge} />
              )}
              <div className="monitor-screen">
                <div
                  className="screen-hud-header station-venue-header"
                  role="region"
                  aria-label="Station operations"
                >
                  {isDockedAtSpaceDock ? (
                    <span>SPACEDOCK TERMINAL</span>
                  ) : (
                    /* Station venues as tabs — the places you can visit while
                       docked. Buy/sell (TRADE) is the default. */
                    <div className="station-venue-tabs" role="tablist">
                      <button
                        type="button"
                        role="tab"
                        aria-selected={stationTerminal === 'trade'}
                        className={`venue-tab${stationTerminal === 'trade' ? ' active' : ''}`}
                        onClick={() => setStationTerminal('trade')}
                      >
                        🛒 TRADE
                      </button>
                      <button
                        type="button"
                        role="tab"
                        aria-selected={stationTerminal === 'portoffice'}
                        className={`venue-tab${stationTerminal === 'portoffice' ? ' active' : ''}`}
                        onClick={() => setStationTerminal('portoffice')}
                      >
                        🏛️ PORT OFFICE
                      </button>
                      <button
                        type="button"
                        role="tab"
                        aria-selected={stationTerminal === 'contracts'}
                        className={`venue-tab${stationTerminal === 'contracts' ? ' active' : ''}`}
                        onClick={() => setStationTerminal('contracts')}
                      >
                        📋 CONTRACTS
                      </button>
                    </div>
                  )}
                  {/* UNDOCK & LAUNCH — for regular (non-SpaceDock) stations.
                      SpaceDock has this button in its own hub-header via the
                      SpaceDockInterface onUndock prop. */}
                  {!isDockedAtSpaceDock && (
                    <button
                      className="station-undock-btn station-undock-action"
                      onClick={handleUndock}
                      disabled={helmBusy}
                      aria-disabled={helmBusy}
                      aria-label={helmBusy ? 'Undock unavailable — helm is busy' : 'Undock and launch into space'}
                      title="Undock and launch into space"
                    >
                      {helmBusy ? '🚀 LAUNCHING…' : '🚀 UNDOCK & LAUNCH'}
                    </button>
                  )}
                </div>
                <div className="screen-hud-content trading-content">
                  {isDockedAtSpaceDock ? (
                    <SpaceDockInterface onUndock={handleUndock} helmBusy={helmBusy} />
                  ) : stationTerminal === 'portoffice' && playerState?.current_port_id ? (
                    <PortOfficeVenue
                      stationId={playerState.current_port_id}
                      stationName={
                        stationsInSector?.find((s: any) => s.id === playerState?.current_port_id)?.name ||
                        'Trading Station'
                      }
                      credits={playerState?.credits ?? 0}
                      onCreditsSet={() => { refreshPlayerState(); }}
                      onBack={() => setStationTerminal('trade')}
                    />
                  ) : stationTerminal === 'contracts' && playerState?.current_port_id ? (
                    <ContractBoardVenue
                      stationId={playerState.current_port_id}
                      stationName={
                        stationsInSector?.find((s: any) => s.id === playerState?.current_port_id)?.name ||
                        'Trading Station'
                      }
                      credits={playerState?.credits ?? 0}
                      onCreditsSet={() => { refreshPlayerState(); }}
                      onBack={() => setStationTerminal('trade')}
                    />
                  ) : (
                    <TradingInterface onClose={() => {}} />
                  )}
                </div>
              </div>
            </div>
          ) : playerState?.is_landed && landedPlanet?.is_population_hub ? (
            /* LANDED ON A POPULATION HUB: the Capital Sector welcome +
               Pioneer Office, not the generic owned-colony console.
               `.surface-face-workspace` (WO-UI4-SURFACE-MODE, game-layout.css)
               places it the same way the owned-colony branch below is placed;
               PopulationCenterInterface owns its own `.console-monitor
               .population-center-monitor.full-width` + `.monitor-bezel`
               markup internally (out of this WO's file scope) — game-
               layout.css neutralizes the bezel and stretches the console-
               monitor to fill the workspace, purely in CSS, so this wrapper
               is the only change needed here. */
            <div className="surface-face-workspace">
              <PopulationCenterInterface planet={landedPlanet} />
            </div>
          ) : playerState?.is_landed ? (
            /* LANDED STATE: Show Comprehensive Planetary Operations Terminal.
               `.surface-face-workspace` (WO-UI4-SURFACE-MODE) replaces the
               flight-monitor bezel wrapper (`.console-monitor.planetary-ops-
               monitor.full-width` + `.monitor-bezel` rivets, cockpit.css) —
               the same mechanical swap DOCKED already got (WO-UI3-STATION-
               MODE's `.station-face-workspace`). `.monitor-screen` and
               everything inside it (CockpitColonyManagement included) is
               salvaged verbatim, unchanged. */
            <div className="surface-face-workspace">
              <div className="monitor-screen">
                <div className="screen-hud-header" role="region" aria-label="Planetary Operations">
                  <span role="heading" aria-level={2}>PLANETARY OPERATIONS COMMAND</span>
                </div>
                <div className="screen-hud-content planetary-ops-content">
                  {(() => {
                    const currentPlanet = planetsInSector?.find((p: any) => p.id === playerState?.current_planet_id);

                    // Real telemetry, no fabrication:
                    //   citadelInfo  — GET /planets/{id}/citadel (owner-only)
                    //   defenseInfo  — GET /planets/{id}/defenses (public)
                    const population = currentPlanet?.population || 0;
                    // Vitals telemetry relocated from the old landed band
                    // (GLASS LAW: flow content lives in the console, not the glass)
                    const habitability = Math.max(0, Math.min(100, currentPlanet?.habitability_score ?? 0));
                    const shieldGen = defenseInfo?.shieldGenerator || null;
                    // The Planet model has no drone column — deployed fighters
                    // fill that role (see PlanetaryService.update_defenses note)
                    const droneCount: number | null = typeof defenseInfo?.fighters === 'number' ? defenseInfo.fighters : null;
                    const turretCount: number | null = typeof defenseInfo?.turrets === 'number' ? defenseInfo.turrets : null;
                    // ADR-0086: shield upgrades are time-based (target level x 6h).
                    const fmtRemain = (secs: number) => {
                      const s = Math.max(0, Math.floor(secs || 0));
                      const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
                      if (d > 0) return `${d}d ${h}h`;
                      if (h > 0) return `${h}h ${m}m`;
                      return `${m}m`;
                    };
                    const shieldUpgrading: boolean = !!shieldGen?.isUpgrading;
                    const shieldRemain: string = shieldGen?.upgrade ? fmtRemain(shieldGen.upgrade.remainingSeconds ?? 0) : '';

                    const planetIcon = getPlanetIcon(currentPlanet?.type);

                    return (
                      <div className="planet-ui">
                        {/* COMPACT VITALS STRIP — ONE slim always-visible row. Folds
                            in the old tall header, the standalone habitability/
                            population readout, AND the colonist-transfer panel
                            (Disembark/Embark) so the page never stacks them as
                            separate sections. Planet name (+ rename) · type ·
                            Habitability · Population/cap · Credits · Defense, then
                            the labeled Colonist Transfer group (counts + Disembark /
                            Embark). Lift Off lives on the green landed bar. */}
                        <div className="planet-vitals-strip">
                          <span className="pvs-icon" aria-hidden="true">{planetIcon}</span>
                          {renamingPlanet && currentPlanet ? (() => {
                            const trimmed = renameValue.trim();
                            // ✓ is meaningless when the name is empty or unchanged.
                            const canConfirm = !!trimmed && trimmed !== currentPlanet.name;
                            const confirmRename = () => {
                              if (canConfirm) handleRenamePlanet(currentPlanet.id, trimmed);
                              setRenamingPlanet(false);
                            };
                            return (
                              <span className="planet-rename-form" role="form" aria-label="Rename planet">
                                <input
                                  className="planet-rename-input"
                                  value={renameValue}
                                  maxLength={60}
                                  autoFocus
                                  onChange={(e) => setRenameValue(e.target.value)}
                                  onKeyDown={(e) => {
                                    if (e.key === 'Enter') { e.preventDefault(); confirmRename(); }
                                    else if (e.key === 'Escape') { e.preventDefault(); setRenamingPlanet(false); }
                                  }}
                                  aria-label="New planet name"
                                />
                                <button className="rename-planet-btn" onClick={confirmRename} disabled={!canConfirm} title="Confirm rename">✓</button>
                                <button className="rename-planet-btn" onClick={() => setRenamingPlanet(false)} title="Cancel rename">✕</button>
                              </span>
                            );
                          })() : (
                            <span className="pvs-name">
                              {currentPlanet?.name || 'Unknown Planet'}
                              {isLandedPlanetMine && currentPlanet && (
                                <button
                                  className="rename-planet-btn"
                                  onClick={() => { setRenameValue(currentPlanet.name || ''); setRenamingPlanet(true); }}
                                  title="Rename your planet"
                                >
                                  ✏️
                                </button>
                              )}
                            </span>
                          )}

                          <span className="pvs-stat type">{currentPlanet?.type?.toUpperCase().replace('_', ' ') || 'UNKNOWN'}</span>
                          <span className="pvs-stat" title="Planet habitability"><span className="pvs-label">Habitability</span><span className="pvs-val">{habitability}%</span></span>
                          <span className="pvs-stat" title="Total residents living on this planet"><span className="pvs-label">Population</span><span className="pvs-val green">{population.toLocaleString()}</span></span>
                          {isLandedPlanetMine && citadelInfo && (
                            <span className="pvs-stat" title="Protected credits in this colony's citadel safe"><span className="pvs-label">Safe</span><span className="pvs-val">{safeCredits.toLocaleString()} cr{safeCapacity > 0 ? ` / ${safeCapacity.toLocaleString()}` : ''}</span></span>
                          )}
                          <span className="pvs-stat" title="Planetary defense damage reduction"><span className="pvs-label">Defense</span><span className="pvs-val">{defenseInfo?.damageReduction ?? '—'}</span></span>

                          {/* COLONIST TRANSFER (WO 130-B) — counts + the two move
                              actions on ONE labeled row so the relationship reads
                              plainly: where colonists are now, and how to move them.
                              Ownership/empty-count gating preserved from b7a77f8. */}
                          <span className="pvs-transfer" title="Move colonists between your ship and the colony">
                            <span className="pvs-transfer-label">Colonist Transfer</span>
                            <span className="pvs-stat" title="Colonists living on this planet"><span className="pvs-label">On planet</span><span className="pvs-val green">{landedPlanetColonists.toLocaleString()}</span></span>
                            <span className="pvs-stat" title="Colonists aboard your ship"><span className="pvs-label">Aboard your ship</span><span className="pvs-val">{shipColonists.toLocaleString()}</span></span>
                            {isLandedPlanetMine && (
                              <span className="pvs-stat" title="Colonists you can still unload before this colony's cap — the lower of citadel & habitability limits"><span className="pvs-label">Room to add</span><span className="pvs-val">{transferHeadroom !== null ? transferHeadroom.toLocaleString() : '—'}</span></span>
                            )}
                            <span className="pvs-transfer-actions">
                              <button
                                className="pvs-btn disembark"
                                disabled={!isLandedPlanetMine || shipColonists === 0}
                                title={
                                  !isLandedPlanetMine ? 'Unloading colonists requires landing on a planet you own'
                                    : shipColonists === 0 ? 'No colonists aboard your ship' : 'Move colonists from your ship down to the colony'
                                }
                                onClick={() => openTransferModal('disembark')}
                              >
                                ⬇ Unload colonists → colony
                              </button>
                              <button
                                className="pvs-btn embark"
                                disabled={!isLandedPlanetMine || landedPlanetColonists === 0}
                                title={
                                  !isLandedPlanetMine ? 'Loading colonists requires landing on a planet you own'
                                    : landedPlanetColonists === 0 ? 'No colonists on this planet to load' : 'Move colonists from the colony up to your ship'
                                }
                                onClick={() => openTransferModal('embark')}
                              >
                                ⬆ Load colonists → ship
                              </button>
                            </span>
                          </span>
                        </div>

                        {/* Notices (rename / upgrade / vault / transfer outcomes) —
                            slim line directly under the strip, where the player acted. */}
                        {opsNotice && (
                          <div className={`transfer-notice ${opsNotice.type}`} role="status">{opsNotice.message}</div>
                        )}
                        {transferNotice && (
                          <div className={`transfer-notice ${transferNotice.type}`} role="status">{transferNotice.message}</div>
                        )}

                        {/* Main content: the tabbed management console fills the rest. */}
                        <div className="planet-content">
                          {/* COLONY MANAGEMENT — Screen 2 of the cockpit redesign.
                              SCROLL LAW (WO-COCKPIT-SCROLLLAW): hoisted to the TOP of
                              the content area so a landed owner sees the management HUD
                              above the fold at 1440×900 — the vitals dial / vault /
                              transfer / production chrome now render BELOW it. Only a
                              landed colony you OWN surfaces the full management depth
                              (citadel ladder / grid / terraform / research / live
                              production + the 6 action modals) as cockpit-native HUD
                              panels. The PRODUCTION panel ticks live off the realtime
                              poll (landedPlanetDetail) via the projected lines — no
                              extra fetch. */}
                          {isLandedPlanetMine && currentPlanet && (() => {
                            // SET stays fixed (the production stockpile is the same
                            // 3-column planet contract as SAFE_COMMODITIES above);
                            // icon/name now come from the shared resource catalog.
                            const prodLines: ProductionLine[] = ([
                              { key: 'fuel' as const, icon: getResourceIcon('fuel'), name: getResourceLabel('fuel') },
                              { key: 'organics' as const, icon: getResourceIcon('organics'), name: getResourceLabel('organics') },
                              { key: 'equipment' as const, icon: getResourceIcon('equipment'), name: getResourceLabel('equipment') },
                            ]).map(({ key, icon, name }) => {
                              const ss = storageStatus(key);
                              // Store-to-safe affordance: same computation the Safe
                              // tab uses (room left in the cr-equiv vault / unit value).
                              const safeKey = SAFE_COMMODITIES.find((c) => c.stock === key)?.safe;
                              const onPlanet = Math.floor(projectedStock(key));
                              const unitVal = Number(citadelInfo?.commodity_values?.[safeKey ?? ''] ?? 0);
                              const room = Math.max(0, safeCapacity - safeTotalValue);
                              const canStore = unitVal > 0 ? Math.min(onPlanet, Math.floor(room / unitVal)) : 0;
                              const storeBusy = !!safeKey && commodityBusy === safeKey;
                              const rate = Number(landedPlanetDetail?.productionRates?.[key] ?? 0);
                              const allocation = Number(landedPlanetDetail?.allocations?.[key] ?? 0);
                              const storeDisabledTitle =
                                !citadelInfo || citadelInfo.citadel_level < 1
                                  ? 'No citadel safe — establish an Outpost (Citadel Level 1)'
                                  : onPlanet >= 1 && room < unitVal
                                    ? `The vault is full — it holds your credits AND stored goods together, valued in credits, up to ${safeCapacity.toLocaleString()}. Withdraw credits or goods to make room.`
                                    : allocation > 0 && rate <= 0
                                      ? `This world produces no ${name}`
                                      : rate <= 0
                                        ? `No production — assign workforce to ${name}`
                                        : 'Under 1 unit produced so far';
                              return {
                                key,
                                icon,
                                name,
                                stock: projectedStock(key),
                                rate,
                                ratio: ss.ratio,
                                capped: ss.capped,
                                nearCap: ss.nearCap,
                                atCap: ss.atCap,
                                cap: storageCap,
                                canStore,
                                storeBusy,
                                storeDisabledTitle,
                              };
                            });
                            // DEFENSE-OPS tab body — the slim controls the cockpit
                            // HUD panels do NOT cover: the in-progress citadel CANCEL,
                            // the shield-generator upgrade (distinct from raw defense
                            // counts), and the server-authoritative defense-building
                            // catalog. Rendered here in GameDashboard's closure so the
                            // existing handlers + live telemetry keep working, then
                            // handed to the tabbed console as the Defense tab.
                            const defenseTabBody = (
                            <div className="planet-section defense">
                              <h4>🛡️ Defense Ops</h4>
                              {citadelInfo?.is_upgrading && (() => {
                                const startMs = citadelInfo.upgrade_started_at ? new Date(citadelInfo.upgrade_started_at).getTime() : null;
                                const endMs = citadelInfo.upgrade_complete_at ? new Date(citadelInfo.upgrade_complete_at).getTime() : null;
                                const remainMs = endMs ? Math.max(0, endMs - nowMs) : (citadelInfo.upgrade_remaining_seconds || 0) * 1000;
                                const pct = (startMs && endMs && endMs > startMs)
                                  ? Math.min(100, Math.max(0, ((nowMs - startMs) / (endMs - startMs)) * 100))
                                  : 0;
                                const targetName = citadelInfo.next_level?.citadel_name
                                  || citadelInfo.next_level?.name
                                  || `Level ${(citadelInfo.citadel_level || 0) + 1}`;
                                return (
                                  <div className="citadel-construction" role="status"
                                       aria-label={`Citadel construction ${Math.round(pct)} percent complete`}>
                                    <div className="cc-head">
                                      <span className="cc-title">🏗️ CONSTRUCTION ACTIVE</span>
                                      <span className="cc-target">→ {targetName}</span>
                                      <button
                                        type="button"
                                        className="cc-cancel"
                                        disabled={cancelBusy}
                                        title="Cancel the upgrade (50% credit refund; resources are not returned)"
                                        onClick={() => {
                                          if (cancelArmed) { void handleCancelCitadel(); }
                                          else { setCancelArmed(true); window.setTimeout(() => setCancelArmed(false), 4000); }
                                        }}
                                      >
                                        {cancelBusy ? '…' : cancelArmed ? 'Confirm? · 50% refund' : '✕ Cancel'}
                                      </button>
                                    </div>
                                    <div className="cc-bar"><div className="cc-fill" style={{ width: `${pct}%` }} /></div>
                                    <div className="cc-meta">
                                      <span className="cc-pct">{Math.round(pct)}%</span>
                                      <span className="cc-remain">{fmtBuildCountdown(remainMs)} remaining</span>
                                      {endMs && (
                                        <span className="cc-eta">ETA {new Date(endMs).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</span>
                                      )}
                                    </div>
                                  </div>
                                );
                              })()}
                              {/* Shield-generator readout pairs with the unique
                                  shield-upgrade control below (drone/turret/citadel
                                  telemetry now lives in the cockpit Citadel panel). */}
                              <div className="defense-grid">
                                <div className="defense-item">
                                  <span>🛡️</span>
                                  <span>
                                    {shieldGen
                                      ? `${Number(shieldGen.currentShields ?? 0).toLocaleString()} / ${Number(shieldGen.strength ?? 0).toLocaleString()}`
                                      : '—'}
                                  </span>
                                  <span className="sublabel">
                                    {shieldUpgrading
                                      ? `L${shieldGen?.upgrade?.fromLevel ?? shieldGen?.level} → L${shieldGen?.upgrade?.toLevel} · ${shieldRemain} left`
                                      : `Shields L${shieldGen?.level ?? '—'}`}
                                  </span>
                                </div>
                              </div>
                              <div className="section-actions">
                                <button
                                  className="section-btn"
                                  disabled={!isLandedPlanetMine || shieldUpgrading || !shieldGen?.nextUpgrade || upgradeBusy}
                                  title={
                                    !isLandedPlanetMine
                                      ? 'Shield upgrades require planetary ownership'
                                      : !shieldGen
                                        ? 'Defense telemetry unavailable'
                                        : shieldUpgrading
                                          ? `Shield generator upgrading to L${shieldGen.upgrade?.toLevel} — ${shieldRemain} remaining`
                                          : !shieldGen.nextUpgrade
                                            ? `Shield generator at maximum level (${shieldGen.maxLevel})`
                                            : `Upgrade to L${shieldGen.nextUpgrade.level} ${shieldGen.nextUpgrade.name} — ${Number(shieldGen.nextUpgrade.cost).toLocaleString()} credits, ${shieldGen.nextUpgrade.buildHours}h`
                                  }
                                  onClick={() => setConfirmUpgrade('shields')}
                                >
                                  {shieldUpgrading ? '🛡️ Upgrading Shields…' : '🛡️ Upgrade Shields'}
                                  {shieldUpgrading ? (
                                    <span className="btn-sublabel">→ L{shieldGen?.upgrade?.toLevel} · {shieldRemain} left</span>
                                  ) : shieldGen?.nextUpgrade && (
                                    <span className="btn-sublabel">→ L{shieldGen.nextUpgrade.level} · {Number(shieldGen.nextUpgrade.cost).toLocaleString()} cr · {shieldGen.nextUpgrade.buildHours}h</span>
                                  )}
                                </button>
                                {/* Citadel upgrade lives in the cockpit Citadel panel
                                    (CitadelManager) — not duplicated here. The only
                                    candidate "Deploy Drones" endpoint sets raw counts
                                    with no cost semantics, so it stays out too. */}
                              </div>
                              {/* Inline confirm — native confirm()/alert() are forbidden */}
                              {confirmUpgrade === 'shields' && shieldGen?.nextUpgrade && (
                                <div className="transfer-notice" role="alertdialog" aria-label="Confirm shield upgrade">
                                  Upgrade shields to L{shieldGen.nextUpgrade.level} {shieldGen.nextUpgrade.name} for{' '}
                                  {Number(shieldGen.nextUpgrade.cost).toLocaleString()} credits? Build time {shieldGen.nextUpgrade.buildHours}h — credits charged now, shields strengthen on completion.
                                  <div className="section-actions" style={{ marginTop: '4px' }}>
                                    <button className="section-btn upgrade" onClick={handleUpgradeShields} disabled={upgradeBusy}>
                                      {upgradeBusy ? 'UPGRADING…' : '✓ Confirm'}
                                    </button>
                                    <button className="section-btn" onClick={() => setConfirmUpgrade(null)} disabled={upgradeBusy}>
                                      ✕ Cancel
                                    </button>
                                  </div>
                                </div>
                              )}
                              {isLandedPlanetMine && defenseBuildings.length > 0 && (
                                <div className="defense-buildings">
                                  <div className="db-head">🏗️ Defense Buildings</div>
                                  {defenseBuildings.map((b: any) => {
                                    const affordable = (playerState?.credits ?? 0) >= Number(b.cost || 0);
                                    const queued = Number(b.queued_count || 0);
                                    const soonest = (b.in_progress || [])[0];
                                    const endMs = soonest?.complete_at ? new Date(soonest.complete_at).getTime() : 0;
                                    const remainMs = endMs ? Math.max(0, endMs - nowMs) : 0;
                                    const totalMs = Number(b.build_hours || 0) * 3600 * 1000;
                                    const pct = totalMs > 0 && endMs
                                      ? Math.min(100, Math.max(0, ((totalMs - remainMs) / totalMs) * 100))
                                      : 0;
                                    return (
                                      <div className="db-row-wrap" key={b.type}>
                                        <div className="db-row">
                                          <span className="db-name" title={b.effects}>{b.name}</span>
                                          <span className="db-count">
                                            {b.current_count}/{b.max_count}
                                            {queued > 0 && <em className="db-queued" title={`${queued} under construction`}> +{queued}🏗️</em>}
                                          </span>
                                          <span className="db-cost">{Number(b.cost).toLocaleString()} cr</span>
                                          {b.can_build ? (
                                            <button
                                              className="db-build"
                                              disabled={buildingBusy === b.type || !affordable}
                                              title={affordable ? `Build ${b.name} · ${b.build_hours}h` : 'Insufficient credits'}
                                              onClick={() => handleBuildBuilding(b.type)}
                                            >
                                              {buildingBusy === b.type ? '…' : 'Build'}
                                            </button>
                                          ) : queued > 0 ? (
                                            <span className="db-building" title="Under construction">{fmtBuildCountdown(remainMs)}</span>
                                          ) : (
                                            <span className="db-max">Max</span>
                                          )}
                                        </div>
                                        {queued > 0 && (
                                          <div className="db-bar"><div className="db-bar-fill" style={{ width: `${pct}%` }} /></div>
                                        )}
                                      </div>
                                    );
                                  })}
                                </div>
                              )}
                            </div>
                            );

                            // SAFE tab body — the UNIFIED citadel-safe vault: the
                            // single cr-equivalent cap bar, CREDIT deposit/withdraw
                            // (with 25/50/75/Max presets), per-commodity store/take,
                            // and the auto-deposit sweep toggle. One vault, one cap
                            // (credits + commodities). Requires citadel level >= 1.
                            const safeTabBody = (
                              <SafeVaultPanel
                                isOwned={isLandedPlanetMine}
                                citadelInfo={citadelInfo}
                                landedPlanetDetail={landedPlanetDetail}
                                playerCredits={playerState?.credits ?? 0}
                                safeCredits={safeCredits}
                                safeCapacity={safeCapacity}
                                safeTotalValue={safeTotalValue}
                                onDepositCredits={handleDepositCredits}
                                onWithdrawCredits={handleWithdrawCredits}
                                creditBusy={safeBusy}
                                commodities={SAFE_COMMODITIES}
                                projectedStock={projectedStock}
                                onMoveCommodity={moveCommoditySafe}
                                commodityBusy={commodityBusy}
                                onToggleAutoDeposit={handleToggleAutoDeposit}
                                autoDepositBusy={autoDepositBusy}
                              />
                            );

                            return (
                              <CockpitColonyManagement
                                planetId={String(landedPlanet.id)}
                                planetType={currentPlanet?.type}
                                playerCredits={playerState?.credits ?? 0}
                                citadelInfo={citadelInfo}
                                landedPlanetDetail={landedPlanetDetail}
                                habitabilityScore={currentPlanet?.habitability_score ?? habitability}
                                underSiege={!!(landedPlanet as any)?.under_siege || !!(landedPlanetDetail as any)?.underSiege}
                                productionLines={landedPlanetDetail ? prodLines : []}
                                overflowResources={overflowResources}
                                allocations={allocations}
                                productionRates={allocRates ?? landedPlanetDetail?.productionRates}
                                perColonistRates={perColonistRates}
                                allocBudget={allocBudget}
                                totalColonists={landedPlanetColonists}
                                onSetAllocations={handleSetAllocations}
                                allocSyncing={allocSyncing}
                                allocError={allocError}
                                onStoreToSafe={storeStockToSafe}
                                onOpsChange={() => setOpsRefresh(n => n + 1)}
                                defenseTab={defenseTabBody}
                                safeTab={safeTabBody}
                              />
                            );
                          })()}
                        </div>

                      </div>
                    );
                  })()}
                </div>
              </div>
            </div>
          ) : (
            <>
              {/* LEFT MONITOR: Navigation */}
              <div className="mon nav-monitor">
                {/* NAV header (WO-UI2-DECK-RECONCILE, §05: [COURSE · CHART ·
                    DRIVE]) — unified for every hull, no more per-page
                    plot-row crammed into the shared header (moved into
                    COURSE's own content below). DRIVE is WJ-gated;
                    non-Warp-Jumper hulls still see 2 pages (COURSE/CHART),
                    so the rail always renders regardless of hull type.
                    WO-UI0-SHELL-TRANSPLANT (Leaf L3): the page-switch
                    softkeys moved out of this header to a bottom `.skrow`
                    (artifact `monNav()`) — the header now shows only the
                    monitor title + a live "N CHARTED EXIT(S)" sub-status,
                    reusing the same `adjacentExits` COURSE already reads. */}
                <div className="mhead">
                  <span className="mtitle">NAV</span>
                  <span className="hsub">{adjacentExits.length} CHARTED EXIT{adjacentExits.length === 1 ? '' : 'S'}</span>
                </div>
                <div
                  className="mbody"
                  role="tabpanel"
                  id={`nav-panel-${navMode}`}
                  aria-labelledby={`nav-tab-${navMode}`}
                >
                {navMode === 'drive' ? (
                    <QuantumDriveConsole onOpenGatewright={() => setShowGatewright(true)} />
                  ) : !currentSector ? (
                    <div className="nav-scan-state">
                      {!navScanTimedOut ? (
                        <>
                          <div className="nav-scan-spinner" aria-hidden="true"></div>
                          <span className="nav-scan-text">SCANNING SECTOR...</span>
                        </>
                      ) : (
                        <>
                          <span className="nav-scan-text warning">SECTOR SCAN TIMED OUT — NO TELEMETRY</span>
                          <button className="nav-scan-retry" onClick={handleRetryScan}>
                            ⟳ RETRY SCAN
                          </button>
                        </>
                      )}
                    </div>
                  ) : navMode === 'course' ? (
                    <>
                      {/* Adjacent exits — 1 click = 1 hop (§05 COURSE). */}
                      <div className="nav-course-section">
                        <div className="nav-course-section-title">ADJACENT EXITS</div>
                        {adjacentExits.length === 0 ? (
                          <div className="empty-state">No charted exits</div>
                        ) : (
                          adjacentExits.map((exit) => (
                            <div key={exit.sector_id} className="nav-exit-row">
                              <span className="nav-exit-name">→ {exit.name}</span>
                              <span className="nav-exit-cost"><TurnsIcon /> {exit.turn_cost}</span>
                              <button
                                type="button"
                                className="nav-exit-move-btn"
                                onClick={() => handleMove(exit.sector_id)}
                                disabled={!exit.can_afford}
                                title={exit.can_afford ? `Move to ${exit.name}` : 'Insufficient turns'}
                              >
                                MOVE ▸
                              </button>
                            </div>
                          ))
                        )}
                      </div>

                      {/* Plotted multi-hop course + PLOT/ENGAGE — relocated
                          out of the shared header (§05 COURSE: "plotted
                          course + 🧭 ENGAGE"). */}
                      <div className="nav-course-section">
                        <div className="nav-course-section-title">COURSE</div>
                        <div className="nav-course-plot-row">
                          <input
                            type="number"
                            className="nav-plot-input"
                            placeholder="SECTOR #"
                            value={plotTarget}
                            onChange={(e) => setPlotTarget(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') {
                                const id = parseInt(plotTarget, 10);
                                if (!isNaN(id) && id > 0) autopilot.plotCourse(id);
                              }
                            }}
                            aria-label="Destination sector number"
                            min={1}
                          />
                          <button
                            className="nav-plot-btn"
                            disabled={autopilot.status === 'plotting' || !plotTarget || isNaN(parseInt(plotTarget, 10))}
                            onClick={() => {
                              const id = parseInt(plotTarget, 10);
                              if (!isNaN(id) && id > 0) autopilot.plotCourse(id);
                            }}
                            title="Plot course to destination sector"
                          >
                            {autopilot.status === 'plotting' ? '…' : 'PLOT'}
                          </button>
                          {/* Autopilot engage / abort — shown when a course is plotted */}
                          {autopilot.course && autopilot.status !== 'arrived' && (
                            autopilot.status === 'engaged' ? (
                              <button
                                className="nav-autopilot-abort"
                                onClick={() => autopilot.abort('manual abort')}
                                disabled={helmBusy}
                                aria-disabled={helmBusy}
                                aria-label={helmBusy ? 'Abort unavailable — helm is busy' : 'Abort autopilot'}
                                title="Abort autopilot"
                              >
                                🛑 ABORT · HOP {autopilot.currentHopIndex + 1}/{autopilot.course.hops.length}{helmBusy ? ' (busy)' : ''}
                              </button>
                            ) : (
                              <button
                                className="nav-autopilot-engage"
                                onClick={() => autopilot.engage()}
                                disabled={helmBusy}
                                aria-disabled={helmBusy}
                                aria-label={helmBusy ? 'Autopilot unavailable — helm is busy' : `Engage autopilot — ${autopilot.course.hops.length} hops, ${autopilot.course.total_turns} turns`}
                                title={`Engage autopilot — ${autopilot.course.hops.length} hops, ${autopilot.course.total_turns} turns`}
                              >
                                🧭 ENGAGE · {autopilot.course.hops.length} HOP{autopilot.course.hops.length !== 1 ? 'S' : ''}{helmBusy ? ' (busy)' : ''}
                              </button>
                            )
                          )}
                        </div>
                        {/* Course summary — total turns + progress; the old
                            ≤6-chip breadcrumb is retired in favor of the
                            polyline overlay drawn directly on the CHART page,
                            which shows the COMPLETE route regardless of hop
                            count (WO-NAV-COURSE-OVERLAY). */}
                        {(() => {
                          const apCourse = autopilot.course;
                          const apLastPlot = autopilot.lastPlot;
                          const hopIdx = autopilot.currentHopIndex;
                          // Unreachable refusal — show inline warning
                          if (apLastPlot !== null && apLastPlot.reachable === false) {
                            const unreach = apLastPlot as import('../../contexts/AutopilotContext').CourseUnreachable;
                            const nearest = unreach.nearest_known;
                            return (
                              <div
                                className="nav-course-strip nav-course-unreachable"
                                role="alert"
                              >
                                BEYOND CHARTED SPACE — NEAREST KNOWN APPROACH:{' '}
                                {nearest ? `SECTOR ${nearest.sector_id}` : 'UNKNOWN'}
                              </div>
                            );
                          }
                          // Reachable course summary — CHART draws the route itself
                          if (apCourse && apCourse.hops.length > 0) {
                            const legNumber = Math.min(hopIdx + 1, apCourse.hops.length);
                            return (
                              <div className="nav-course-strip">
                                <div className="nav-course-meta" role="status" aria-live="polite">
                                  <TurnsIcon /> {apCourse.total_turns} · LEG {legNumber}/{apCourse.hops.length}
                                </div>
                              </div>
                            );
                          }
                          return null;
                        })()}
                      </div>
                    </>
                  ) : (
                    <>
                      {/* CHART — the astrogation chart (§05: "window onto the
                          LIVING ASTROGATION CHART"). 2D/3D toggle relocated
                          here from the shared header (WO-UI2-CHART-MONITOR's
                          toggle was already chart-only in behavior, just
                          header-shared in markup). */}
                      <div className="nav-chart-toolbar">
                        <button
                          type="button"
                          className="nav-plot-btn"
                          style={{ opacity: navChartMode === '2d' ? 1 : 0.45 }}
                          aria-pressed={navChartMode === '2d'}
                          aria-label="2D star chart view"
                          onClick={() => setNavChartMode('2d')}
                          title="2D star chart"
                        >
                          2D
                        </button>
                        <button
                          type="button"
                          className="nav-plot-btn"
                          style={{ opacity: navChartMode === '3d' ? 1 : 0.45 }}
                          aria-pressed={navChartMode === '3d'}
                          aria-label="3D galaxy view"
                          onClick={() => setNavChartMode('3d')}
                          title="3D galaxy view"
                        >
                          3D
                        </button>
                      </div>
                      {navChartMode === '3d' ? (
                        // Galaxy3DRenderer sources currentSector/availableMoves
                        // itself via useGame() -- the rendered node set is
                        // already {current} ∪ {warps} ∪ {tunnels}, the exact
                        // same reachable domain as 2D's availableMoves, so
                        // any non-current click is always a valid hop
                        // (mirrors GalaxyMap.tsx's own onSectorSelect reuse).
                        // .galaxy-3d-container fills 100% of this flex cell,
                        // same height:100% chain NavigationMap's own wrapper
                        // already relies on here -- no extra sizing wrapper.
                        <Galaxy3DRenderer
                          className="nav-3d-view"
                          onSectorSelect={(sector) => handleMove(sector.sector_id)}
                        />
                      ) : (
                        <NavigationMap
                          currentSectorId={currentSector.sector_id}
                          sectors={mergedNavSectors}
                          availableMoves={affordableMoveIds}
                          moveCosts={moveCosts}
                          onNavigate={handleMove}
                          width={440}
                          height={300}
                          course={autopilot.course?.hops ?? null}
                          currentHopIndex={autopilot.currentHopIndex}
                          oneWayEdges={oneWayEdges}
                        />
                      )}
                    </>
                  )}
                </div>
                <div className="skrow">
                  <DeckPageTabs
                    pages={[
                      { id: 'course', label: 'COURSE' },
                      { id: 'chart', label: 'CHART' },
                      { id: 'drive', label: 'DRIVE', available: isWarpJumper },
                    ]}
                    activeId={navMode}
                    onSelect={(id) => setNavMode(id as 'course' | 'chart' | 'drive')}
                    ariaLabel="NAV display mode"
                    accent="#00d9ff"
                    idBase="nav"
                  />
                </div>
              </div>

              {/* CENTER MONITOR: Solar System (formerly "Planetary Systems",
                  §05 [SYSTEM · SALVAGE · SIGNALS], WO-UI2-DECK-RECONCILE) */}
              <div className="mon system-monitor">
                {/* WO-UI0-SHELL-TRANSPLANT (Leaf L3): header now shows only
                    the title + the live sector name as its sub-status; the
                    SYSTEM/SALVAGE/SIGNALS softkeys moved to a bottom
                    `.skrow` (artifact `monSys()`). */}
                <div className="mhead">
                  <span className="mtitle">SOLAR SYSTEM</span>
                  <span className="hsub">{currentSector?.name ?? '—'}</span>
                </div>
                <div
                  className="mbody"
                  role="tabpanel"
                  id={`system-panel-${systemPage}`}
                  aria-labelledby={`system-tab-${systemPage}`}
                >
                {systemPage === 'system' ? (
                    <>
                      {/* Show planets paired with stations (by index) */}
                      {planetsInSector.map((planet, index) => (
                        <PlanetPortPair
                          key={planet.id}
                          planet={planet}
                          station={stationsInSector?.[index] || null}
                          onLandOnPlanet={handleLand}
                          onClaimPlanet={handleClaim}
                          onDockAtStation={handleDock}
                          isLanded={playerState?.is_landed || false}
                          isDocked={playerState?.is_docked || false}
                        />
                      ))}
                      {/* Show any extra stations not paired with planets */}
                      {stationsInSector.slice(planetsInSector.length).map((station) => (
                        <PlanetPortPair
                          key={station.id}
                          planet={null}
                          station={station}
                          onLandOnPlanet={handleLand}
                          onDockAtStation={handleDock}
                          isLanded={playerState?.is_landed || false}
                          isDocked={playerState?.is_docked || false}
                        />
                      ))}
                      {/* Empty state when neither planets nor stations.
                          Asteroid fields get a HARVEST trigger; all other empty
                          sectors get the generic "nothing detected" label. */}
                      {planetsInSector.length === 0 && stationsInSector.length === 0 && (
                        currentSector?.type?.toUpperCase() === 'ASTEROID_FIELD' ? (
                          <div className="planetary-asteroid-state">
                            <div className="planetary-asteroid-label">⚫ ASTEROID FIELD</div>
                            <button
                              className="planetary-harvest-btn"
                              onClick={handleHarvest}
                              disabled={helmBusy || harvestBusy}
                              aria-disabled={helmBusy || harvestBusy}
                              aria-label={helmBusy ? 'Harvest unavailable — helm is busy' : 'Deploy the mining laser to harvest ore from the asteroid field'}
                              title="Deploy the mining laser to harvest ore from the asteroid field"
                            >
                              {harvestBusy ? '⛏️ MINING…' : helmBusy ? '⛏️ HARVEST (busy)' : '⛏️ HARVEST'}
                            </button>
                          </div>
                        ) : (
                          <div className="empty-state">No planetary bodies or stations detected</div>
                        )
                      )}
                      {/* Hazards FOLDED IN — NOT their own page (§05 SYSTEM:
                          "bodies/stations rows... WITH hazards FOLDED IN").
                          Same currentSector fields the windshield's HudChips
                          already surface (hazard/radiation/features/
                          description); special_formations is deliberately
                          NOT repeated here — it moved to its own SIGNALS
                          page below. Always shows the hazard/radiation
                          readouts (even at 0) so this reads as a live
                          sensor sweep, not a conditional chip. */}
                      {currentSector && (
                        <div className="system-hazard-fold">
                          <div className="system-hazard-fold-title">SECTOR HAZARDS</div>
                          <div className="system-hazard-metric">
                            <div className="hud-label">⚠️ HAZARD</div>
                            <div className={`hud-value${currentSector.hazard_level > 0 ? ' danger' : ''}`}>
                              {currentSector.hazard_level}/10
                            </div>
                            <div className="hud-bar">
                              <div className="hud-bar-fill danger" style={{ width: `${currentSector.hazard_level * 10}%` }}></div>
                            </div>
                          </div>
                          <div className="system-hazard-metric">
                            <div className="hud-label">☢️ RADIATION</div>
                            <div className={`hud-value${currentSector.radiation_level > 0 ? ' warning' : ''}`}>
                              {(currentSector.radiation_level * 100).toFixed(1)}%
                            </div>
                            <div className="hud-bar">
                              <div className="hud-bar-fill warning" style={{ width: `${currentSector.radiation_level * 100}%` }}></div>
                            </div>
                          </div>
                          {currentSector.special_features && currentSector.special_features.length > 0 && (
                            <div className="system-hazard-metric">
                              <div className="hud-label">NO-TRANSIT NOTES</div>
                              <div className="hud-features">
                                {currentSector.special_features.map(feature => (
                                  <span key={feature} className="hud-badge">
                                    {feature.replace(/_/g, ' ').toUpperCase()}
                                  </span>
                                ))}
                              </div>
                            </div>
                          )}
                          {currentSector.description && (
                            <div className="hud-description-text">{currentSector.description}</div>
                          )}
                        </div>
                      )}
                    </>
                  ) : systemPage === 'salvage' ? (
                    <SolarSalvagePage wrecks={sectorWrecks} onSalvaged={refetchSectorWrecks} />
                  ) : (
                    /* SIGNALS — discovered formations → INVESTIGATE (§05).
                       Shares renderFormationList with the windshield's
                       FORMATIONS HudChip (same currentSector.
                       special_formations data + Investigate control). */
                    !currentSector ? (
                      <div className="empty-state">No sector telemetry</div>
                    ) : currentSector.special_formations && currentSector.special_formations.length > 0 ? (
                      renderFormationList(currentSector.special_formations)
                    ) : (
                      <div className="empty-state">No signals or formations charted in this sector</div>
                    )
                  )}
                </div>
                <div className="skrow">
                  <DeckPageTabs
                    pages={[
                      { id: 'system', label: 'SYSTEM' },
                      { id: 'salvage', label: 'SALVAGE' },
                      { id: 'signals', label: 'SIGNALS' },
                    ]}
                    activeId={systemPage}
                    onSelect={(id) => setSystemPage(id as 'system' | 'salvage' | 'signals')}
                    ariaLabel="SOLAR SYSTEM display mode"
                    accent="#9333ea"
                    idBase="system"
                  />
                </div>
              </div>

              {/* TACTICAL — [TARGET · THREAT] (§05, WO-UI2-DECK-RECONCILE).
                  The former COMMS monitor's CONTACTS list now lives at
                  TACTICAL[TARGET] (`sectorContacts`, unchanged merge/feed);
                  the deck no longer has a standalone COMMS monitor — HAILS/
                  composer moved to the MFD-B COMM lane. TacticalMonitor owns
                  its own header + content, same self-contained pattern
                  CommsMailbox used for COMMS. WO-UI0-SHELL-TRANSPLANT (Leaf
                  L3): TacticalMonitor now renders its OWN full `.mon` block
                  (artifact `monTac()`) — no wrapper divs needed here, same
                  as NAV/SOLAR SYSTEM rendering their own `.mon` above. */}
              <TacticalMonitor
                contacts={sectorContacts}
                selectedShipId={selectedShipId}
                onSelectContact={(c) =>
                  setSelectedShipId(c?.ship_id ? String(c.ship_id) : null)
                }
              />
            </>
          )}
        </div>
        );
        return deckEl ? createPortal(consoleNode, deckEl) : consoleNode;
        })()}

        {/* Colonist transfer quantity modal — portal escapes the cockpit stacking context */}
        {transferModal && landedPlanet && createPortal(
          <div
            className="colonist-modal-overlay"
            onClick={() => { if (!isTransferring) setTransferModal(null); }}
          >
            <div className="colonist-modal" onClick={(e) => e.stopPropagation()}>
              <div className="colonist-modal-header">
                <h3>{transferModal === 'disembark' ? '📥 UNLOAD COLONISTS TO PLANET' : '📤 LOAD COLONISTS TO SHIP'}</h3>
                <button
                  className="colonist-modal-close"
                  onClick={() => setTransferModal(null)}
                  aria-label="Close colonist transfer"
                >
                  ×
                </button>
              </div>
              <div className="colonist-modal-route">
                {transferModal === 'disembark'
                  ? <>🚀 {currentShip?.name || 'Your ship'} → 🪐 {landedPlanet.name}</>
                  : <>🪐 {landedPlanet.name} → 🚀 {currentShip?.name || 'Your ship'}</>}
              </div>

              <div className="colonist-qty-section">
                <label className="colonist-qty-label" htmlFor="colonist-qty-input">Colonists</label>
                <input
                  type="range"
                  min="1"
                  max={Math.max(1, transferMax)}
                  value={transferQuantity}
                  onChange={(e) => setTransferQuantity(clampTransferQuantity(parseInt(e.target.value) || 1))}
                  className="colonist-qty-slider"
                  disabled={transferMax < 1}
                />
                <div className="colonist-qty-input-group">
                  <button
                    className="qty-step"
                    onClick={() => setTransferQuantity(clampTransferQuantity(transferQuantity - 1))}
                    disabled={transferQuantity <= 1}
                  >
                    −
                  </button>
                  <input
                    id="colonist-qty-input"
                    type="number"
                    min="1"
                    max={Math.max(1, transferMax)}
                    value={transferQuantity}
                    onChange={(e) => setTransferQuantity(clampTransferQuantity(parseInt(e.target.value) || 1))}
                    className="colonist-qty-input"
                  />
                  <button
                    className="qty-step"
                    onClick={() => setTransferQuantity(clampTransferQuantity(transferQuantity + 1))}
                    disabled={transferQuantity >= transferMax}
                  >
                    +
                  </button>
                </div>
                <div className="colonist-qty-presets">
                  {[0.25, 0.5, 0.75].map((fraction) => (
                    <button
                      key={fraction}
                      onClick={() => setTransferQuantity(clampTransferQuantity(Math.floor(transferMax * fraction)))}
                      disabled={transferMax < 1}
                    >
                      {fraction * 100}% ({Math.max(1, Math.floor(transferMax * fraction)).toLocaleString()})
                    </button>
                  ))}
                  <button
                    onClick={() => setTransferQuantity(Math.max(1, transferMax))}
                    disabled={transferMax < 1}
                  >
                    Max ({transferMax.toLocaleString()})
                  </button>
                </div>
              </div>

              <div className="colonist-transfer-summary">
                <div className="summary-line">
                  <span>Aboard ship</span>
                  <span className="value">
                    {shipColonists.toLocaleString()} → {(transferModal === 'disembark'
                      ? Math.max(0, shipColonists - transferQuantity)
                      : shipColonists + transferQuantity).toLocaleString()}
                  </span>
                </div>
                <div className="summary-line">
                  <span>On planet</span>
                  <span className="value">
                    {landedPlanetColonists.toLocaleString()} → {(transferModal === 'disembark'
                      ? landedPlanetColonists + transferQuantity
                      : Math.max(0, landedPlanetColonists - transferQuantity)).toLocaleString()}
                  </span>
                </div>
              </div>

              <div className="colonist-modal-actions">
                <button
                  className="colonist-modal-cancel"
                  onClick={() => setTransferModal(null)}
                  disabled={isTransferring}
                >
                  Cancel
                </button>
                <button
                  className="colonist-modal-confirm"
                  onClick={handleTransferConfirm}
                  disabled={isTransferring || transferMax < 1 || transferQuantity < 1 || transferQuantity > transferMax}
                >
                  {isTransferring
                    ? 'TRANSFERRING…'
                    : transferModal === 'disembark' ? 'CONFIRM UNLOAD' : 'CONFIRM LOAD'}
                </button>
              </div>
            </div>
          </div>,
          document.body
        )}

        {/* Gatewright project panel — portal escapes the cockpit stacking
            context, same pattern as the colonist transfer modal above */}
        {showGatewright && isWarpJumper && createPortal(
          <div
            className="quantum-gatewright-overlay"
            onClick={() => setShowGatewright(false)}
          >
            <div className="quantum-gatewright-shell" onClick={(e) => e.stopPropagation()}>
              <GatewrightPanel onClose={() => setShowGatewright(false)} />
            </div>
          </div>,
          document.body
        )}

        {/* Region-owner invite/tradedock portal modals RELOCATED to
            components/governance/RegionOwnerControls.tsx (WO-UI0-STATUSBAR
            integration) — it carries its own state + both portals now,
            mounted inside StatusBar's LocationDropdown. */}

        {/* ARIA assistant is mounted globally in GameLayout for all /game routes */}
      </div>
  );
};

export default GameDashboard;