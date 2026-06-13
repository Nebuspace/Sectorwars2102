import React, { useEffect, useRef, useState, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { useGame, type MoveOption } from '../../contexts/GameContext';
import { useAutopilot } from '../../contexts/AutopilotContext';
import { useFirstLogin } from '../../contexts/FirstLoginContext';
import { useWebSocket } from '../../contexts/WebSocketContext';
// import { useTheme } from '../../themes/ThemeProvider'; // Available for future use
import GameLayout from '../layouts/GameLayout';
import TradingInterface from '../trading/TradingInterface';
import SpaceDockInterface from '../spacedock/SpaceDockInterface';
import PortOfficeVenue from '../spacedock/PortOfficeVenue';
import PopulationCenterInterface from '../planetary/PopulationCenterInterface';
import TacticalCard from '../tactical/TacticalCard';
import SolarSystemViewscreen from '../tactical/SolarSystemViewscreen';
import PlanetPortPair from '../tactical/PlanetPortPair';
import NavigationMap from '../tactical/NavigationMap';
import QuantumDriveConsole from '../quantum/QuantumDriveConsole';
import GatewrightPanel from '../gatewright/GatewrightPanel';
import CommsMailbox from '../comms/CommsMailbox';
import './game-dashboard.css';
import './cockpit.css';
import '../tactical/tactical-layout.css';
import '../quantum/quantum-drive.css';

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
}> = ({ planetId, isOwned, habitability }) => {
  // Read-only: only refreshPlayerState is used, to re-pull credits after a
  // terraforming START debits the ladder cost server-side.
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
                  disabled={busy}
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
                  title={selectedInfo
                    ? `+${selectedInfo.habitabilityBoost} habitability over ${selectedInfo.durationHours}h — also consumes ${selectedInfo.organicsCost.toLocaleString()} organics + ${selectedInfo.equipmentCost.toLocaleString()} equipment from planet stock`
                    : undefined}
                >
                  START
                </button>
              </div>
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
    depositToSafe,
    withdrawFromSafe,
    getPlanetDefenseInfo,
    upgradeShields,
    exploreCurrentLocation,
    getAvailableMoves,
    refreshPlayerState,
    quantumStatus,
    refineQuantumCharge,
    error
  } = useGame();
  
  const autopilot = useAutopilot();

  const { requiresFirstLogin } = useFirstLogin();
  const { sectorPlayers, isConnected } = useWebSocket();

  // Autopilot plot input state (NAV monitor destination field)
  const [plotTarget, setPlotTarget] = useState('');

  const [movementResult, setMovementResult] = useState<any>(null);
  const [dockingResult, setDockingResult] = useState<any>(null);
  const [landingResult, setLandingResult] = useState<any>(null);

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
  const [stationTerminal, setStationTerminal] = useState<'trade' | 'portoffice'>('trade');
  useEffect(() => {
    setStationTerminal('trade');
  }, [playerState?.current_port_id]);

  // Docked chrome minimize: a few seconds after docking, collapse the
  // station-bay windshield (low-value scenery) so the station console — the
  // buy/sell desk + venues, the reason you docked — gets ~85% of the band and
  // fits without scrolling. Auto-fires once per dock; the player can expand/
  // re-minimize manually. Resets on undock.
  const [dockedChromeMin, setDockedChromeMin] = useState(false);
  useEffect(() => {
    if (!playerState?.is_docked) { setDockedChromeMin(false); return; }
    setDockedChromeMin(false); // start expanded so the dock "lands" visibly
    const t = window.setTimeout(() => setDockedChromeMin(true), 3500);
    return () => window.clearTimeout(t);
  }, [playerState?.is_docked, playerState?.current_port_id]);

  // NAV monitor mode: Warp Jumpers get a second mode — the Quantum Drive
  // console — behind a two-position switch in the NAV header. Every other
  // ship type sees exactly the classic warp graph, no switch.
  const isWarpJumper = currentShip?.type === 'WARP_JUMPER';
  const [navMode, setNavMode] = useState<'graph' | 'quantum'>('graph');
  const [showGatewright, setShowGatewright] = useState(false);

  // Swapping off the Warp Jumper drops back to the warp graph and closes
  // the Gatewright panel — neither exists without the quantum drive.
  useEffect(() => {
    if (!isWarpJumper) {
      setNavMode('graph');
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

    const byId = new Map<number, { id: number; name: string; type?: string; connected_sectors: number[] }>();

    // Current sector (connections deduped — a dual warp+tunnel destination
    // would otherwise draw the same edge twice)
    byId.set(currentSector.sector_id, {
      id: currentSector.sector_id,
      name: `Sector ${currentSector.sector_number || currentSector.sector_id}`,
      type: currentSector.type,
      connected_sectors: [...new Set([
        ...availableMoves.warps.map(w => w.sector_id),
        ...availableMoves.tunnels.map(t => t.sector_id)
      ])]
    });

    // Available warp destinations
    availableMoves.warps.forEach(warp => {
      if (byId.has(warp.sector_id)) return;
      byId.set(warp.sector_id, {
        id: warp.sector_id,
        name: destinationName(warp),
        type: warp.type,
        connected_sectors: [currentSector.sector_id]
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
        connected_sectors: [currentSector.sector_id]
      });
    });

    return Array.from(byId.values());
  }, [currentSector, availableMoves]);

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

  // Detailed planet data (colonists / maxColonists) — the detail endpoint only
  // answers for planets the player owns, so render gracefully when absent
  const [landedPlanetDetail, setLandedPlanetDetail] = useState<any>(null);
  useEffect(() => {
    let cancelled = false;
    if (!landedPlanet || !isLandedPlanetMine) {
      setLandedPlanetDetail(null);
      return;
    }
    getPlanetDetails(landedPlanet.id)
      .then((detail: any) => { if (!cancelled) setLandedPlanetDetail(detail); })
      .catch(() => { if (!cancelled) setLandedPlanetDetail(null); });
    return () => { cancelled = true; };
  }, [landedPlanet?.id, isLandedPlanetMine]);

  // Colonists on the landed planet (detail when owned, sector snapshot otherwise)
  const landedPlanetColonists: number =
    typeof landedPlanetDetail?.colonists === 'number'
      ? landedPlanetDetail.colonists
      : landedPlanet?.population || 0;

  // --- Citadel + defense telemetry for the landed console ---
  // GET /planets/{id}/citadel answers only for the owner (400 otherwise);
  // GET /planets/{id}/defenses answers for anyone (scouting is allowed).
  // opsRefresh bumps re-fetch both after upgrades change them server-side.
  const [citadelInfo, setCitadelInfo] = useState<any>(null);
  const [defenseInfo, setDefenseInfo] = useState<any>(null);
  const [opsRefresh, setOpsRefresh] = useState(0);

  useEffect(() => {
    let cancelled = false;
    if (!landedPlanet) {
      setCitadelInfo(null);
      setDefenseInfo(null);
      return;
    }
    getPlanetDefenseInfo(landedPlanet.id)
      .then((info: any) => { if (!cancelled) setDefenseInfo(info); })
      .catch(() => { if (!cancelled) setDefenseInfo(null); });
    if (isLandedPlanetMine) {
      getCitadelInfo(landedPlanet.id)
        .then((info: any) => { if (!cancelled) setCitadelInfo(info); })
        .catch(() => { if (!cancelled) setCitadelInfo(null); });
    } else {
      setCitadelInfo(null);
    }
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [landedPlanet?.id, isLandedPlanetMine, opsRefresh]);

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
      setOpsNotice({
        type: 'success',
        message: `Shield generator upgraded to L${gen?.level ?? '?'}${gen?.name ? ` (${gen.name})` : ''} — ${Number(result?.creditsCost || 0).toLocaleString()} credits spent.`
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

  // --- Citadel safe (credits only — CitadelService stores nothing else) ---
  const [safeAction, setSafeAction] = useState<'deposit' | 'withdraw' | null>(null);
  const [safeAmount, setSafeAmount] = useState(1);
  const [safeBusy, setSafeBusy] = useState(false);

  const safeCredits: number = Number(citadelInfo?.safe_credits ?? 0);
  const safeCapacity: number = Number(citadelInfo?.safe_storage ?? 0);
  // Deposit is capped by both wallet and remaining vault headroom
  // (CitadelService.deposit_to_safe rejects beyond-capacity deposits)
  const safeMax = safeAction === 'deposit'
    ? Math.max(0, Math.min(playerState?.credits ?? 0, safeCapacity - safeCredits))
    : safeCredits;

  const openSafeAction = (action: 'deposit' | 'withdraw') => {
    const max = action === 'deposit'
      ? Math.max(0, Math.min(playerState?.credits ?? 0, safeCapacity - safeCredits))
      : safeCredits;
    setSafeAmount(Math.max(1, max));
    setSafeAction(action);
  };

  const handleSafeConfirm = async () => {
    if (!landedPlanet || !safeAction || safeBusy || safeAmount < 1) return;
    setSafeBusy(true);
    try {
      const result = safeAction === 'deposit'
        ? await depositToSafe(landedPlanet.id, safeAmount)
        : await withdrawFromSafe(landedPlanet.id, safeAmount);
      // safe_balance in the response is authoritative — no refetch needed
      setCitadelInfo((prev: any) => prev ? {
        ...prev,
        safe_credits: typeof result?.safe_balance === 'number' ? result.safe_balance : prev.safe_credits
      } : prev);
      setOpsNotice({ type: 'success', message: result?.message || 'Vault transaction complete.' });
      setSafeAction(null);
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

  // --- Colonist transfer modal (quantity pattern mirrors the trading modal) ---
  const [transferModal, setTransferModal] = useState<'disembark' | 'embark' | null>(null);
  const [transferQuantity, setTransferQuantity] = useState(1);
  const [isTransferring, setIsTransferring] = useState(false);
  const [transferNotice, setTransferNotice] = useState<{ type: 'success' | 'error'; message: string } | null>(null);

  const transferMax = useMemo(() => {
    if (transferModal === 'disembark') {
      let max = shipColonists;
      if (typeof landedPlanetDetail?.maxColonists === 'number' && typeof landedPlanetDetail?.colonists === 'number') {
        max = Math.min(max, Math.max(0, landedPlanetDetail.maxColonists - landedPlanetDetail.colonists));
      }
      return max;
    }
    if (transferModal === 'embark') {
      return landedPlanetColonists;
    }
    return 0;
  }, [transferModal, shipColonists, landedPlanetDetail, landedPlanetColonists]);

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
          ? `${transferQuantity.toLocaleString()} colonists disembarked to ${landedPlanet.name}`
          : `${transferQuantity.toLocaleString()} colonists embarked from ${landedPlanet.name}`)
      });
      // Sync the detail panel from the authoritative server response
      setLandedPlanetDetail((prev: any) => prev ? {
        ...prev,
        colonists: typeof result?.planet_colonists === 'number' ? result.planet_colonists : prev.colonists,
        maxColonists: typeof result?.max_colonists === 'number' ? result.max_colonists : prev.maxColonists
      } : prev);
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
    const seeded = {
      fuel: Number(landedPlanetDetail?.allocations?.fuel ?? 0),
      organics: Number(landedPlanetDetail?.allocations?.organics ?? 0),
      equipment: Number(landedPlanetDetail?.allocations?.equipment ?? 0)
    };
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

  const handleAllocationChange = (resource: 'fuel' | 'organics' | 'equipment', newValue: number) => {
    if (!landedPlanet || !isLandedPlanetMine) return;
    // Clamp so the three allocations never exceed the colonist workforce
    const othersTotal = (['fuel', 'organics', 'equipment'] as const)
      .filter(r => r !== resource)
      .reduce((sum, r) => sum + allocations[r], 0);
    const clamped = Math.max(0, Math.min(newValue, Math.max(0, landedPlanetColonists - othersTotal)));
    const next = { ...allocations, [resource]: clamped };
    setAllocations(next);
    persistAllocations(landedPlanet.id, next);
  };

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
  
  // If the player needs to complete the first login experience, the FirstLoginContainer
  // component will be shown by the App component, so we don't need to render the dashboard
  if (requiresFirstLogin) {
    return null;
  }

  return (
    <GameLayout>
      <div className={`game-dashboard cockpit-mode${dockedChromeMin && playerState?.is_docked ? ' docked-min' : ''}`}>
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

        {/* WINDSHIELD - Full immersive viewport with HUD overlays */}
        <div className="cockpit-windshield" ref={windshieldRef}>
          {/* LANDED STATE — planet-surface vista scene. GLASS LAW: the band
              hosts canvas scenery + absolutely-anchored HUD chips ONLY. The
              vitals/status/rename console moved to PLANETARY OPERATIONS
              COMMAND below; LIFT OFF lives on the helm rail. */}
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
              />

              {/* Cockpit frame vignette */}
              <div className="windshield-frame">
                <div className="frame-corner top-left"></div>
                <div className="frame-corner top-right"></div>
                <div className="frame-corner bottom-left"></div>
                <div className="frame-corner bottom-right"></div>
              </div>

              {/* HUD chips — fixed anchors, never flow layout */}
              <HudChip id="landed" className="top-left" pill={<>🪐 {landedPlanet?.name || 'Planet'}</>}>
                <div className="hud-label">LANDED — PLANETARY SURFACE</div>
                <div className="hud-value hud-chip-name" title={landedPlanet?.name || undefined}>
                  {getPlanetIcon(landedPlanet?.type)} {landedPlanet?.name || 'Unknown Planet'}
                </div>
                <div className="hud-value-secondary">
                  {landedPlanet?.type?.replace(/_/g, ' ').toUpperCase() || 'UNKNOWN TYPE'}
                </div>
              </HudChip>

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
            </>
            );
          })()}

          {/* DOCKED STATE — station bay scene. GLASS LAW: canvas scenery +
              absolutely-anchored HUD chips ONLY; UNDOCK lives on the helm
              rail, the trade/SpaceDock instruments stay in the console. */}
          {playerState?.is_docked && (
            <>
              <SolarSystemViewscreen
                sectorId={sceneSectorId}
                scene="docked"
                isSpaceDock={isDockedAtSpaceDock}
              />

              {/* Cockpit frame vignette */}
              <div className="windshield-frame">
                <div className="frame-corner top-left"></div>
                <div className="frame-corner top-right"></div>
                <div className="frame-corner bottom-left"></div>
                <div className="frame-corner bottom-right"></div>
              </div>

              {/* HUD chips — fixed anchors, never flow layout */}
              <HudChip
                id="station"
                className="top-left"
                pill={<>{isDockedAtSpaceDock ? '🚀' : '🏪'} {dockedStation?.name || (isDockedAtSpaceDock ? 'SpaceDock' : 'Trading Station')}</>}
              >
                <div className="hud-label">
                  {isDockedAtSpaceDock ? '🚀 DOCKED — SPACEDOCK' : '🏪 DOCKED — STATION'}
                </div>
                <div className="hud-value hud-chip-name" title={dockedStation?.name || undefined}>
                  {dockedStation?.name || (isDockedAtSpaceDock ? 'SpaceDock' : 'Trading Station')}
                </div>
                <div className="hud-value-secondary">
                  {dockedStation?.type?.replace(/_/g, ' ').toUpperCase() ||
                    (isDockedAtSpaceDock ? 'SPACEDOCK' : 'TRADING STATION')}
                </div>
              </HudChip>

              <HudChip id="baystatus" className="top-right" pill={<>⚓ CLAMPED</>}>
                <div className="hud-label">BAY STATUS</div>
                <div className="hud-value hud-chip-name hud-chip-ok">CLAMPS ENGAGED</div>
              </HudChip>

              {/* Manual minimize — collapse the bay scenery to give the console
                  the band (only shown while the bay is expanded). */}
              <button
                type="button"
                className="bay-minimize-btn"
                onClick={() => setDockedChromeMin(true)}
                title="Minimize the bay view — expand the station console"
              >
                ▴ MINIMIZE BAY
              </button>

              {/* Collapsed strip (shown only when minimized via CSS): station
                  identity + expand affordance. */}
              <div className="docked-min-bar">
                <span className="docked-min-name">
                  {isDockedAtSpaceDock ? '🚀' : '🏪'} DOCKED — {(dockedStation?.name || (isDockedAtSpaceDock ? 'SpaceDock' : 'Trading Station')).toUpperCase()}
                </span>
                <button
                  type="button"
                  className="docked-min-expand"
                  onClick={() => setDockedChromeMin(false)}
                  title="Expand the docking-bay view"
                >
                  ⤢ EXPAND BAY
                </button>
              </div>
            </>
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
              />

              {/* Cockpit frame vignette */}
              <div className="windshield-frame">
                <div className="frame-corner top-left"></div>
                <div className="frame-corner top-right"></div>
                <div className="frame-corner bottom-left"></div>
                <div className="frame-corner bottom-right"></div>
              </div>

              {/* HUD Overlays */}
              <HudChip
                id="location"
                className="top-left"
                pill={<>◈ SECTOR {currentSector.sector_number || currentSector.sector_id}</>}
              >
                <div className="hud-label">LOCATION</div>
                <div className="hud-value">
                  SECTOR {currentSector.sector_number || currentSector.sector_id}
                </div>
                {currentSector.region_name && (
                  <div className="hud-region-name" title={currentSector.region_name}>
                    {currentSector.region_name}
                  </div>
                )}
                <div className="hud-value-secondary">
                  {currentSector.type ? currentSector.type.replace(/_/g, ' ').toUpperCase() : 'STANDARD'}
                </div>
                {playerState && (
                  <div className="hud-pilot">
                    <span className="status-indicator online"></span>
                    <span style={{ color: playerState.name_color || '#FFFFFF' }}>
                      {playerState.military_rank.toUpperCase()} {playerState.username.toUpperCase()}
                    </span>
                    <div className="hud-reputation-tier" style={{ fontSize: '0.7em', opacity: 0.8 }}>
                      {playerState.reputation_tier} ({playerState.personal_reputation >= 0 ? '+' : ''}{playerState.personal_reputation})
                    </div>
                  </div>
                )}
              </HudChip>

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

        {/* HELM ACTION RAIL — the always-visible home of the primary state
            actions (SCROLL LAW): DOCK / LAND in flight, UNDOCK while docked,
            LIFT OFF while landed. Scenery click-throughs and the PLANETARY
            monitor affordances remain as alternate triggers. */}
        <div className="helm-rail">
          <div className="helm-state">
            {playerState?.is_landed && !playerState?.is_docked ? (
              <>LANDED ON {(landedPlanet?.name || 'PLANET').toUpperCase()}</>
            ) : playerState?.is_docked ? (
              <>DOCKED AT {(dockedStation?.name || (isDockedAtSpaceDock ? 'SPACEDOCK' : 'STATION')).toUpperCase()}</>
            ) : (
              <>IN FLIGHT{currentSector ? ` — SECTOR ${currentSector.sector_number || currentSector.sector_id}` : ''}</>
            )}
          </div>
          <div className={`helm-actions${helmBusy ? ' busy' : ''}`}>
            {playerState?.is_landed && !playerState?.is_docked ? (
              <button className="helm-btn liftoff" onClick={handleLeavePlanet} disabled={helmBusy}>
                {helmBusy ? '🚀 DEPARTING…' : '🚀 LIFT OFF & DEPART'}
              </button>
            ) : playerState?.is_docked ? (
              <button className="helm-btn undock" onClick={handleUndock} disabled={helmBusy}>
                {helmBusy ? '🚀 LAUNCHING…' : '🚀 UNDOCK & LAUNCH'}
              </button>
            ) : (
              <>
                {/* Autopilot course controls — visible in IN FLIGHT only */}
                {autopilot.course && autopilot.status !== 'arrived' && (
                  autopilot.status === 'engaged' ? (
                    <button
                      className="helm-btn autopilot-abort"
                      onClick={() => autopilot.abort('manual abort')}
                      disabled={helmBusy}
                      title="Abort autopilot"
                    >
                      🛑 ABORT · HOP {autopilot.currentHopIndex + 1}/{autopilot.course.hops.length}
                    </button>
                  ) : (
                    <button
                      className="helm-btn autopilot-engage"
                      onClick={() => autopilot.engage()}
                      disabled={helmBusy}
                      title={`Engage autopilot — ${autopilot.course.hops.length} hops, ${autopilot.course.total_turns} turns`}
                    >
                      🧭 ENGAGE AUTOPILOT · {autopilot.course.hops.length} HOPS · {autopilot.course.total_turns} TURNS
                    </button>
                  )
                )}
                {(stationsInSector || []).map((station: any) => (
                  <button
                    key={station.id}
                    className="helm-btn dock"
                    onClick={() => handleDock(station.id)}
                    disabled={helmBusy}
                    title={`Dock at ${station.name}`}
                  >
                    ⚓ DOCK · <span className="helm-btn-target">{station.name}</span>
                  </button>
                ))}
                {(planetsInSector || []).map((planet: any) => (
                  <button
                    key={planet.id}
                    className="helm-btn land"
                    onClick={() => handleLand(planet.id)}
                    disabled={helmBusy}
                    title={`Land on ${planet.name}`}
                  >
                    🛬 LAND · <span className="helm-btn-target">{planet.name}</span>
                  </button>
                ))}
                {/* SCANNING until the sector telemetry resolves, so we never
                    flash a false "NO TARGETS" during the in-flight load. */}
                {!currentSector || !stationsInSector || !planetsInSector ? (
                  <span className="helm-empty scanning">SCANNING SECTOR…</span>
                ) : (stationsInSector.length === 0 && planetsInSector.length === 0) && (
                  <span className="helm-empty">NO DOCK / LANDING TARGETS IN SECTOR</span>
                )}
              </>
            )}
          </div>
        </div>

        {/* CONSOLE - Metal panel with embedded monitors */}
        <div className="cockpit-console">
          {/* DOCKED STATE: Show SpaceDock or Trading Interface */}
          {playerState?.is_docked ? (
            <div className="console-monitor trading-monitor full-width">
              {isWarpJumper && (
                <QuantumRefineryStrip status={quantumStatus} onRefine={refineQuantumCharge} />
              )}
              <div className="monitor-bezel">
                <div className="bezel-corner tl"></div>
                <div className="bezel-corner tr"></div>
                <div className="bezel-corner bl"></div>
                <div className="bezel-corner br"></div>
              </div>
              <div className="monitor-screen">
                <div className="screen-hud-header station-venue-header">
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
                    </div>
                  )}
                </div>
                <div className="screen-hud-content trading-content">
                  {isDockedAtSpaceDock ? (
                    <SpaceDockInterface />
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
                  ) : (
                    <TradingInterface onClose={() => {}} />
                  )}
                </div>
              </div>
            </div>
          ) : playerState?.is_landed && landedPlanet?.is_population_hub ? (
            /* LANDED ON A POPULATION HUB: the Capital Sector welcome +
               Pioneer Office, not the generic owned-colony console. */
            <PopulationCenterInterface planet={landedPlanet} />
          ) : playerState?.is_landed ? (
            /* LANDED STATE: Show Comprehensive Planetary Operations Terminal */
            <div className="console-monitor planetary-ops-monitor full-width">
              <div className="monitor-bezel">
                <div className="bezel-corner tl"></div>
                <div className="bezel-corner tr"></div>
                <div className="bezel-corner bl"></div>
                <div className="bezel-corner br"></div>
              </div>
              <div className="monitor-screen">
                <div className="screen-hud-header">PLANETARY OPERATIONS COMMAND</div>
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
                    const maxPopulation = currentPlanet?.max_population ?? 0;
                    const detailColonists = typeof landedPlanetDetail?.colonists === 'number' ? landedPlanetDetail.colonists : null;
                    const detailMaxColonists = typeof landedPlanetDetail?.maxColonists === 'number' ? landedPlanetDetail.maxColonists : null;
                    const shieldGen = defenseInfo?.shieldGenerator || null;
                    // The Planet model has no drone column — deployed fighters
                    // fill that role (see PlanetaryService.update_defenses note)
                    const droneCount: number | null = typeof defenseInfo?.fighters === 'number' ? defenseInfo.fighters : null;
                    const turretCount: number | null = typeof defenseInfo?.turrets === 'number' ? defenseInfo.turrets : null;

                    const planetIcon = getPlanetIcon(currentPlanet?.type);

                    return (
                      <div className="planet-ui">
                        {/* Header with planet name, terraform, and key stats */}
                        <div className="planet-header">
                          <div className="planet-title">
                            <span className="planet-icon-lg">{planetIcon}</span>
                            <div className="planet-name-block">
                              <span className="planet-name">
                                {currentPlanet?.name || 'Unknown Planet'}
                                {isLandedPlanetMine && currentPlanet && !renamingPlanet && (
                                  <button
                                    className="rename-planet-btn"
                                    onClick={() => {
                                      setRenameValue(currentPlanet.name || '');
                                      setRenamingPlanet(true);
                                    }}
                                    title="Rename your planet"
                                  >
                                    ✏️
                                  </button>
                                )}
                              </span>
                              {renamingPlanet && currentPlanet && (() => {
                                const trimmed = renameValue.trim();
                                // ✓ is meaningless when the name is empty or
                                // unchanged — disable it so the only outcomes
                                // are a real rename or an explicit cancel.
                                const canConfirm = !!trimmed && trimmed !== currentPlanet.name;
                                const confirmRename = () => {
                                  if (canConfirm) {
                                    handleRenamePlanet(currentPlanet.id, trimmed);
                                  }
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
                                      if (e.key === 'Enter') {
                                        e.preventDefault();
                                        confirmRename();
                                      } else if (e.key === 'Escape') {
                                        e.preventDefault();
                                        setRenamingPlanet(false);
                                      }
                                    }}
                                    aria-label="New planet name"
                                  />
                                  <button
                                    className="rename-planet-btn"
                                    onClick={confirmRename}
                                    disabled={!canConfirm}
                                    title="Confirm rename"
                                  >
                                    ✓
                                  </button>
                                  <button
                                    className="rename-planet-btn"
                                    onClick={() => setRenamingPlanet(false)}
                                    title="Cancel rename"
                                  >
                                    ✕
                                  </button>
                                </span>
                                );
                              })()}
                              <span className="planet-meta">{currentPlanet?.type?.toUpperCase().replace('_', ' ') || 'UNKNOWN'} • Hab: {currentPlanet?.habitability_score || 0}%</span>
                            </div>
                          </div>
                          {/* Planetary-ops notice (rename / upgrade / vault outcomes)
                              sits adjacent to the planet title so a rename
                              confirmation lands where the player just acted —
                              not buried down in the citadel section. */}
                          {opsNotice && (
                            <div className={`transfer-notice ${opsNotice.type}`} role="status">
                              {opsNotice.message}
                            </div>
                          )}
                          <TerraformHeaderPanel
                            planetId={currentPlanet?.id}
                            isOwned={!!currentPlanet && isLandedPlanetMine}
                            habitability={currentPlanet?.habitability_score || 0}
                          />
                          <div className="planet-stats">
                            <div className="stat"><span className="label">Population</span><span className="value green">{population.toLocaleString()}</span></div>
                            {/* Server-computed damage reduction (defense_level × per-level factor) */}
                            <div className="stat"><span className="label">Defense</span><span className="value">{defenseInfo?.damageReduction ?? '—'}</span></div>
                          </div>
                        </div>

                        {/* Main content: 2 columns on top, full-width safe at bottom */}
                        <div className="planet-content">
                          {/* VITALS — relocated from the old landed band (it used to
                              clip inside the glass); habitability dial + population /
                              colonist bars + colony status, tinted per planet type */}
                          <div className={`planet-section vitals ${getPlanetTintClass(currentPlanet?.type)}`}>
                            <div className="planet-vitals">
                              <div
                                className="vital-dial"
                                style={{ '--dial-pct': habitability } as React.CSSProperties}
                                title={`Habitability: ${habitability}%`}
                              >
                                <div className="dial-face">
                                  <span className="dial-value">{habitability}%</span>
                                </div>
                                <span className="dial-label">Habitability</span>
                              </div>
                              <div className="vital-bars">
                                <div className="vital-bar-row" title="Planetary population">
                                  <span className="vital-bar-label">Population</span>
                                  <div className="vital-bar-track">
                                    <div
                                      className="vital-bar-fill population"
                                      style={{ width: `${maxPopulation > 0 ? Math.min(100, (population / maxPopulation) * 100) : (population > 0 ? 100 : 0)}%` }}
                                    ></div>
                                  </div>
                                  <span className="vital-bar-value">
                                    {population.toLocaleString()}{maxPopulation > 0 ? ` / ${maxPopulation.toLocaleString()}` : ''}
                                  </span>
                                </div>
                                {detailColonists !== null && (
                                  <div className="vital-bar-row" title="Colonist workforce">
                                    <span className="vital-bar-label">Colonists</span>
                                    <div className="vital-bar-track">
                                      <div
                                        className="vital-bar-fill colonists"
                                        style={{ width: `${detailMaxColonists && detailMaxColonists > 0 ? Math.min(100, (detailColonists / detailMaxColonists) * 100) : (detailColonists > 0 ? 100 : 0)}%` }}
                                      ></div>
                                    </div>
                                    <span className="vital-bar-value">
                                      {detailColonists.toLocaleString()}{detailMaxColonists && detailMaxColonists > 0 ? ` / ${detailMaxColonists.toLocaleString()}` : ''}
                                    </span>
                                  </div>
                                )}
                                <div className="vital-bar-row status">
                                  <span className="vital-bar-label">Status</span>
                                  <span className="vital-bar-value">{currentPlanet?.status?.toUpperCase() || 'UNKNOWN'}</span>
                                </div>
                              </div>
                            </div>
                          </div>

                          {/* Top Row: Defense & Production side by side */}
                          <div className="planet-top-row">
                            <div className="planet-section defense">
                              <h4>🛡️ Citadel Defense Systems</h4>
                              <div className="citadel-banner">
                                <span className="citadel-icon">🏰</span>
                                {citadelInfo ? (
                                  <>
                                    <span className="citadel-name">{citadelInfo.citadel_name}</span>
                                    <span className="citadel-level">Level {citadelInfo.citadel_level}</span>
                                  </>
                                ) : (
                                  <span className="citadel-name">
                                    {isLandedPlanetMine ? 'Citadel telemetry unavailable' : 'Citadel status — owner access only'}
                                  </span>
                                )}
                              </div>
                              {citadelInfo?.is_upgrading && (
                                <div className="transfer-notice success" role="status">
                                  ⏳ Citadel upgrade in progress — ~{Math.max(1, Math.ceil((citadelInfo.upgrade_remaining_seconds || 0) / 3600))}h remaining
                                </div>
                              )}
                              <div className="defense-grid">
                                <div className="defense-item">
                                  <span>🛡️</span>
                                  <span>
                                    {shieldGen
                                      ? `${Number(shieldGen.currentShields ?? 0).toLocaleString()} / ${Number(shieldGen.strength ?? 0).toLocaleString()}`
                                      : '—'}
                                  </span>
                                  <span className="sublabel">Shields L{shieldGen?.level ?? '—'}</span>
                                </div>
                                <div className="defense-item">
                                  <span>🤖</span>
                                  <span>
                                    {droneCount !== null ? droneCount : '—'}
                                    {citadelInfo ? ` / ${Number(citadelInfo.drone_capacity ?? 0)}` : ''}
                                  </span>
                                  <span className="sublabel">Drones</span>
                                </div>
                                <div className="defense-item">
                                  <span>🔫</span>
                                  <span>{turretCount !== null ? turretCount : '—'}</span>
                                  <span className="sublabel">Turrets</span>
                                </div>
                              </div>
                              <div className="section-actions">
                                <button
                                  className="section-btn"
                                  disabled={!isLandedPlanetMine || !shieldGen?.nextUpgrade || upgradeBusy}
                                  title={
                                    !isLandedPlanetMine
                                      ? 'Shield upgrades require planetary ownership'
                                      : !shieldGen
                                        ? 'Defense telemetry unavailable'
                                        : !shieldGen.nextUpgrade
                                          ? `Shield generator at maximum level (${shieldGen.maxLevel})`
                                          : `Upgrade to L${shieldGen.nextUpgrade.level} ${shieldGen.nextUpgrade.name} — ${Number(shieldGen.nextUpgrade.cost).toLocaleString()} credits`
                                  }
                                  onClick={() => setConfirmUpgrade('shields')}
                                >
                                  🛡️ Upgrade Shields
                                </button>
                                {/* No "Deploy Drones" control: the only candidate endpoint
                                    (PUT /planets/{id}/defenses → update_defenses) overwrites
                                    raw counts with no inventory or cost semantics — wiring a
                                    one-click free-set would be an economy cheat, not a
                                    deployment. Removed rather than left dead. */}
                                <button
                                  className="section-btn upgrade"
                                  disabled={!isLandedPlanetMine || !citadelInfo || citadelInfo.is_upgrading || !citadelInfo.next_level || upgradeBusy}
                                  title={
                                    !isLandedPlanetMine
                                      ? 'Citadel upgrades require planetary ownership'
                                      : !citadelInfo
                                        ? 'Citadel telemetry unavailable'
                                        : citadelInfo.is_upgrading
                                          ? 'An upgrade is already in progress'
                                          : !citadelInfo.next_level
                                            ? 'Citadel is at maximum level'
                                            : citadelInfo.citadel_level === 0
                                              ? 'Establish an Outpost (Level 1) — free'
                                              : `Upgrade to L${citadelInfo.next_level.level} ${citadelInfo.next_level.name} — ${Number(citadelInfo.next_level.upgrade_cost).toLocaleString()} credits + resources, ${citadelInfo.next_level.upgrade_hours}h`
                                  }
                                  onClick={() => setConfirmUpgrade('citadel')}
                                >
                                  🏗️ Upgrade Citadel
                                </button>
                              </div>
                              {/* Inline confirm — native confirm()/alert() are forbidden */}
                              {confirmUpgrade === 'shields' && shieldGen?.nextUpgrade && (
                                <div className="transfer-notice" role="alertdialog" aria-label="Confirm shield upgrade">
                                  Upgrade shields to L{shieldGen.nextUpgrade.level} {shieldGen.nextUpgrade.name} for{' '}
                                  {Number(shieldGen.nextUpgrade.cost).toLocaleString()} credits?
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
                              {confirmUpgrade === 'citadel' && citadelInfo?.next_level && (
                                <div className="transfer-notice" role="alertdialog" aria-label="Confirm citadel upgrade">
                                  {citadelInfo.citadel_level === 0 ? (
                                    <>Establish an Outpost (Level 1)? This is free and immediate.</>
                                  ) : (
                                    <>
                                      Upgrade citadel to L{citadelInfo.next_level.level} {citadelInfo.next_level.name} for{' '}
                                      {Number(citadelInfo.next_level.upgrade_cost).toLocaleString()} credits
                                      {citadelInfo.next_level.resource_cost && (
                                        <>
                                          {' '}+ {Object.entries(citadelInfo.next_level.resource_cost as Record<string, number>)
                                            .map(([res, amt]) => `${Number(amt).toLocaleString()} ${res.replace(/_/g, ' ')}`)
                                            .join(', ')}
                                        </>
                                      )}
                                      ? Takes {citadelInfo.next_level.upgrade_hours}h.
                                    </>
                                  )}
                                  <div className="section-actions" style={{ marginTop: '4px' }}>
                                    <button className="section-btn upgrade" onClick={handleUpgradeCitadel} disabled={upgradeBusy}>
                                      {upgradeBusy ? 'WORKING…' : '✓ Confirm'}
                                    </button>
                                    <button className="section-btn" onClick={() => setConfirmUpgrade(null)} disabled={upgradeBusy}>
                                      ✕ Cancel
                                    </button>
                                  </div>
                                </div>
                              )}
                            </div>
                            <div className="planet-section production">
                              <h4>👥 Population & Production</h4>

                              {/* Colonist Transfer */}
                              <div className="colonist-transfer">
                                <div className="transfer-info">
                                  <span className="pop-count">{Math.floor(population / 1000)}K</span>
                                  <span className="pop-label">Population</span>
                                </div>
                                <div className="transfer-actions">
                                  <button
                                    className="transfer-btn disembark"
                                    disabled={!isLandedPlanetMine || shipColonists === 0}
                                    title={
                                      !isLandedPlanetMine
                                        ? 'Disembark requires landing on a planet you own'
                                        : shipColonists === 0
                                          ? 'No colonists aboard your ship'
                                          : 'Ship → Planet'
                                    }
                                    onClick={() => openTransferModal('disembark')}
                                  >
                                    <span>📥</span> Disembark
                                  </button>
                                  <button
                                    className="transfer-btn embark"
                                    disabled={!isLandedPlanetMine || landedPlanetColonists === 0}
                                    title={
                                      !isLandedPlanetMine
                                        ? 'You can only embark colonists from a planet you own'
                                        : landedPlanetColonists === 0
                                          ? 'No colonists on this planet to embark'
                                          : 'Planet → Ship'
                                    }
                                    onClick={() => openTransferModal('embark')}
                                  >
                                    <span>📤</span> Embark
                                  </button>
                                </div>
                              </div>
                              {shipColonists > 0 && (
                                <div className="colonists-aboard">
                                  👥 {shipColonists.toLocaleString()} colonists aboard
                                </div>
                              )}
                              {transferNotice && (
                                <div className={`transfer-notice ${transferNotice.type}`} role="status">
                                  {transferNotice.message}
                                </div>
                              )}

                              {/* Production Allocation — colonist headcounts assigned to
                                  fuel/organics/equipment (the only allocations the backend
                                  stores; ore/terraform sliders were UI fiction). Rates are
                                  the server's productionRates (per day). */}
                              <div className="allocation-section">
                                <div className="allocation-header">
                                  Production Allocation
                                  {allocSyncing && (
                                    <span style={{ marginLeft: '8px', opacity: 0.7, fontSize: '0.85em' }}>syncing…</span>
                                  )}
                                </div>
                                {!isLandedPlanetMine ? (
                                  <div className="empty-state">Allocation control requires planetary ownership</div>
                                ) : !landedPlanetDetail ? (
                                  <div className="empty-state">Colony ledger unavailable</div>
                                ) : (
                                  <>
                                    <div className="allocation-sliders">
                                      {([
                                        { key: 'fuel' as const, icon: '⛽', name: 'Fuel' },
                                        { key: 'organics' as const, icon: '🌿', name: 'Organics' },
                                        { key: 'equipment' as const, icon: '⚙️', name: 'Equipment' }
                                      ]).map(({ key, icon, name }) => (
                                        <div className="alloc-row" key={key}>
                                          <span className="alloc-icon">{icon}</span>
                                          <span className="alloc-name">{name}</span>
                                          <input
                                            type="range"
                                            min="0"
                                            max={Math.max(0, landedPlanetColonists)}
                                            value={allocations[key]}
                                            onChange={(e) => handleAllocationChange(key, parseInt(e.target.value) || 0)}
                                            className={`alloc-slider ${key}`}
                                            disabled={landedPlanetColonists === 0}
                                            title={`Colonists assigned to ${name.toLowerCase()} production`}
                                          />
                                          <span className="alloc-pct">{allocations[key].toLocaleString()}</span>
                                          <span className="alloc-rate">+{Number(allocRates?.[key] ?? 0).toLocaleString()}/day</span>
                                        </div>
                                      ))}
                                    </div>
                                    <div className="allocation-header" style={{ opacity: 0.8 }}>
                                      Unassigned: {Math.max(0, landedPlanetColonists - allocations.fuel - allocations.organics - allocations.equipment).toLocaleString()} colonists
                                    </div>
                                    {allocError && (
                                      <div className="transfer-notice error" role="alert">{allocError}</div>
                                    )}
                                  </>
                                )}
                              </div>
                            </div>
                          </div>

                          {/* Bottom Row: Full-width Citadel Safe.
                              The safe holds CREDITS ONLY (planet.citadel_safe_credits,
                              capacity CITADEL_LEVELS[level].safe_storage) — the old
                              16-commodity grid was pure fiction and is gone. Deposits
                              require citadel level >= 1 (CitadelService.deposit_to_safe:
                              "Planet does not have a citadel"). */}
                          <div className="planet-section storage full-width">
                            <div className="safe-header">
                              <h4>🔐 Citadel Safe</h4>
                              {citadelInfo && citadelInfo.citadel_level >= 1 ? (
                                <>
                                  <div className="safe-credits">
                                    <span className="credits-label">💰</span>
                                    <span className="credits-value">{safeCredits.toLocaleString()}</span>
                                    <span className="credits-text">credits</span>
                                  </div>
                                  <span className="safe-cap">capacity {safeCapacity.toLocaleString()} credits</span>
                                  <div className="safe-header-actions">
                                    <button
                                      className="safe-btn deposit"
                                      disabled={safeBusy || Math.min(playerState?.credits ?? 0, safeCapacity - safeCredits) < 1}
                                      title={
                                        safeCapacity - safeCredits < 1
                                          ? 'Safe is at capacity — upgrade the citadel to expand storage'
                                          : (playerState?.credits ?? 0) < 1
                                            ? 'No credits to deposit'
                                            : 'Deposit credits into the vault'
                                      }
                                      onClick={() => openSafeAction('deposit')}
                                    >
                                      📥 Deposit
                                    </button>
                                    <button
                                      className="safe-btn withdraw"
                                      disabled={safeBusy || safeCredits < 1}
                                      title={safeCredits < 1 ? 'Vault is empty' : 'Withdraw credits from the vault'}
                                      onClick={() => openSafeAction('withdraw')}
                                    >
                                      📤 Withdraw
                                    </button>
                                  </div>
                                </>
                              ) : (
                                <div className="safe-credits">
                                  <span className="credits-text">
                                    {!isLandedPlanetMine
                                      ? 'Vault access requires planetary ownership'
                                      : citadelInfo
                                        ? 'No citadel safe — establish an Outpost (Citadel Level 1) to unlock credit storage'
                                        : 'Vault telemetry unavailable'}
                                  </span>
                                </div>
                              )}
                            </div>
                            {safeAction && citadelInfo && (
                              <div
                                className="safe-inline-form"
                                role="form"
                                aria-label={safeAction === 'deposit' ? 'Deposit credits' : 'Withdraw credits'}
                                style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap', padding: '6px 0' }}
                              >
                                <span>{safeAction === 'deposit' ? '📥 Deposit' : '📤 Withdraw'}</span>
                                <input
                                  type="number"
                                  min={1}
                                  max={Math.max(1, safeMax)}
                                  value={safeAmount}
                                  onChange={(e) => setSafeAmount(Math.max(1, Math.min(safeMax, parseInt(e.target.value) || 1)))}
                                  className="colonist-qty-input"
                                  style={{ width: '120px' }}
                                  disabled={safeBusy}
                                />
                                <button
                                  className="safe-btn"
                                  onClick={() => setSafeAmount(Math.max(1, safeMax))}
                                  disabled={safeBusy || safeMax < 1}
                                >
                                  Max ({safeMax.toLocaleString()})
                                </button>
                                <button
                                  className={`safe-btn ${safeAction}`}
                                  onClick={handleSafeConfirm}
                                  disabled={safeBusy || safeAmount < 1 || safeAmount > safeMax}
                                >
                                  {safeBusy ? 'PROCESSING…' : '✓ Confirm'}
                                </button>
                                <button className="safe-btn" onClick={() => setSafeAction(null)} disabled={safeBusy}>
                                  ✕ Cancel
                                </button>
                              </div>
                            )}
                          </div>
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
              <div className="console-monitor nav-monitor">
                <div className="monitor-bezel">
                  <div className="bezel-corner tl"></div>
                  <div className="bezel-corner tr"></div>
                  <div className="bezel-corner bl"></div>
                  <div className="bezel-corner br"></div>
                </div>
                <div className="monitor-screen">
                  {isWarpJumper ? (
                    <div className="screen-hud-header nav-header-with-modes">
                      <span>NAV</span>
                      <div className="nav-mode-switch" role="tablist" aria-label="NAV display mode">
                        <button
                          className={`nav-mode-btn ${navMode === 'graph' ? 'active' : ''}`}
                          role="tab"
                          aria-selected={navMode === 'graph'}
                          onClick={() => setNavMode('graph')}
                        >
                          WARP GRAPH
                        </button>
                        <button
                          className={`nav-mode-btn quantum ${navMode === 'quantum' ? 'active' : ''}`}
                          role="tab"
                          aria-selected={navMode === 'quantum'}
                          onClick={() => setNavMode('quantum')}
                        >
                          QUANTUM DRIVE
                        </button>
                      </div>
                      {/* Destination plot row — sits in the NAV header for all ship types */}
                      <div className="nav-plot-row">
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
                      </div>
                    </div>
                  ) : (
                    <div className="screen-hud-header nav-header-with-plot">
                      NAV
                      {/* Destination plot row — non-Warp-Jumper variant */}
                      <div className="nav-plot-row">
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
                      </div>
                    </div>
                  )}
                  <div className="screen-hud-content">
                  {isWarpJumper && navMode === 'quantum' ? (
                    <QuantumDriveConsole onOpenGatewright={() => setShowGatewright(true)} />
                  ) : (
                    <>
                      {!currentSector && (
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
                      )}
                      {currentSector && (
                        <NavigationMap
                          currentSectorId={currentSector.sector_id}
                          sectors={navSectors}
                          availableMoves={affordableMoveIds}
                          moveCosts={moveCosts}
                          onNavigate={handleMove}
                          width={440}
                          height={300}
                        />
                      )}
                      {/* Course strip — rendered below the warp graph whenever a course exists */}
                      {(() => {
                        const apCourse = autopilot.course;
                        const apLastPlot = autopilot.lastPlot;
                        const hopIdx = autopilot.currentHopIndex;
                        // Unreachable refusal — show inline warning
                        if (apLastPlot !== null && apLastPlot.reachable === false) {
                          const unreach = apLastPlot as import('../../contexts/AutopilotContext').CourseUnreachable;
                          const nearest = unreach.nearest_known;
                          return (
                            <div className="nav-course-strip nav-course-unreachable">
                              BEYOND CHARTED SPACE — NEAREST KNOWN APPROACH:{' '}
                              {nearest ? `SECTOR ${nearest.sector_id}` : 'UNKNOWN'}
                            </div>
                          );
                        }
                        // Reachable course breadcrumb strip
                        if (apCourse && apCourse.hops.length > 0) {
                          const hops = apCourse.hops;
                          const MAX_VISIBLE = 6;
                          const showEllipsis = hops.length > MAX_VISIBLE + 1;
                          const visibleHops = showEllipsis ? hops.slice(0, MAX_VISIBLE) : hops;
                          return (
                            <div className="nav-course-strip">
                              <div className="nav-course-breadcrumb">
                                {visibleHops.map((hop, i) => (
                                  <span
                                    key={hop.sector_id}
                                    className={`nav-course-hop${i < hopIdx ? ' nav-course-hop-done' : i === hopIdx ? ' nav-course-hop-current' : ''}`}
                                    title={hop.name}
                                  >
                                    {hop.sector_id}
                                  </span>
                                ))}
                                {showEllipsis && (
                                  <>
                                    <span className="nav-course-ellipsis">…</span>
                                    <span
                                      className={`nav-course-hop${hops.length - 1 < hopIdx ? ' nav-course-hop-done' : hops.length - 1 === hopIdx ? ' nav-course-hop-current' : ''}`}
                                      title={hops[hops.length - 1].name}
                                    >
                                      {hops[hops.length - 1].sector_id}
                                    </span>
                                  </>
                                )}
                              </div>
                              <div className="nav-course-meta">
                                {apCourse.total_turns} TURNS · {hops.length} HOPS
                              </div>
                            </div>
                          );
                        }
                        return null;
                      })()}
                    </>
                  )}
                  </div>
                </div>
              </div>

              {/* CENTER MONITOR: Planetary Systems */}
              <div className="console-monitor planetary-monitor">
                <div className="monitor-bezel">
                  <div className="bezel-corner tl"></div>
                  <div className="bezel-corner tr"></div>
                  <div className="bezel-corner bl"></div>
                  <div className="bezel-corner br"></div>
                </div>
                <div className="monitor-screen">
                  <div className="screen-hud-header">PLANETARY</div>
                  <div className="screen-hud-content">
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
                  {/* Empty state when neither planets nor stations */}
                  {planetsInSector.length === 0 && stationsInSector.length === 0 && (
                    <div className="empty-state">No planetary bodies or stations detected</div>
                  )}
                  </div>
                </div>
              </div>

              {/* RIGHT MONITOR: COMMS — CONTACTS (sector presence) / HAILS
                  (player-to-player mailbox). CommsMailbox owns the header
                  (mode switch + unread badge) and content. */}
              <div className="console-monitor comms-monitor">
                <div className="monitor-bezel">
                  <div className="bezel-corner tl"></div>
                  <div className="bezel-corner tr"></div>
                  <div className="bezel-corner bl"></div>
                  <div className="bezel-corner br"></div>
                </div>
                <div className="monitor-screen">
                  <CommsMailbox
                    contacts={sectorContacts}
                    selectedShipId={selectedShipId}
                    onSelectContact={(c) =>
                      setSelectedShipId(c?.ship_id ? String(c.ship_id) : null)
                    }
                  />
                </div>
              </div>
            </>
          )}
        </div>

        {/* Colonist transfer quantity modal — portal escapes the cockpit stacking context */}
        {transferModal && landedPlanet && createPortal(
          <div
            className="colonist-modal-overlay"
            onClick={() => { if (!isTransferring) setTransferModal(null); }}
          >
            <div className="colonist-modal" onClick={(e) => e.stopPropagation()}>
              <div className="colonist-modal-header">
                <h3>{transferModal === 'disembark' ? '📥 DISEMBARK COLONISTS' : '📤 EMBARK COLONISTS'}</h3>
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
                    : transferModal === 'disembark' ? 'CONFIRM DISEMBARK' : 'CONFIRM EMBARK'}
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

        {/* ARIA assistant is mounted globally in GameLayout for all /game routes */}
      </div>
    </GameLayout>
  );
};

export default GameDashboard;