/**
 * CombatInterface Component
 *
 * Main combat engagement interface for ship-to-ship, ship-to-planet, and
 * ship-to-port combat. Combat resolves synchronously on the backend (a
 * single engage call resolves the whole fight), so this interface shows
 * the resolved outcome with a full round-by-round combat log replay.
 */

import React, { useState, useCallback } from 'react';
import { useGame } from '../../contexts/GameContext';
import { gameAPI } from '../../services/api';
import { InputValidator, SecurityAudit } from '../../utils/security/inputValidation';
import CockpitInstrument from '../cockpit/CockpitInstrument';
import { CombatHistoryPanel } from './CombatHistoryPanel';
import CombatAdvicePanel from './CombatAdvicePanel';
import './combat-interface.css';

/* WEAPONS CONSOLE shell (Law 3) — module-level so the frame keeps its
   identity across target-selection/engagement renders. Used only when the
   component is a standalone route; modal usage stays bare. */
const WeaponsConsoleShell: React.FC<{ children?: React.ReactNode }> = ({ children }) => (
  <CockpitInstrument title="WEAPONS CONSOLE" accent="#FF4D6D" subtitle="COMBAT OPERATIONS">
    {children}
  </CockpitInstrument>
);

function httpStatus(err: unknown): number | undefined {
  if (err && typeof err === 'object') {
    const direct = (err as { status?: number }).status;
    if (typeof direct === 'number') return direct;
    const resp = (err as { response?: { status?: number } }).response;
    if (typeof resp?.status === 'number') return resp.status;
  }
  return undefined;
}

/** Transport collapse copy is not gameserver detail (network-collapse densify). */
const isNetworkCollapseMessage = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed) ||
    /^networkerror$/i.test(trimmed)
  );
};

/** Surface GS engage detail on combat initiation failure (LEG-2932 Soft-ORDER). */
export function formatCombatInitiateError(err: unknown): string {
  const status = httpStatus(err);
  const message = err instanceof Error ? err.message : undefined;
  // Network collapse (fetch TypeError / axios transport) is not gameserver copy — use the fallback.
  const hasServerDetail =
    !(err instanceof TypeError) &&
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim()) &&
    !isNetworkCollapseMessage(message);

  if (status === 403) {
    if (hasServerDetail) return message!;
    return 'You do not have permission to engage this target.';
  }

  if (status === 429) {
    return 'Combat action rate limit exceeded — wait a moment and try again.';
  }

  if (hasServerDetail) return message!;
  return 'Combat system error. Please try again.';
}

// Shapes returned by the player_combat API (see gameserver player_combat.py)
interface CombatRoundEvent {
  round: number;
  actor?: string | null;
  action?: string | null;
  message: string;
}

interface CombatStatus {
  status: 'completed';
  outcome?: 'attacker_win' | 'defender_win' | 'draw' | 'escaped' | null;
  rounds: CombatRoundEvent[];
  winner?: string | null;
  combatDuration?: number | null;
  creditsLooted?: number | null;
  cargoLooted?: string[];
}

interface CombatTarget {
  id: string;
  name: string;
  type: 'ship' | 'planet' | 'port';
  isNpc?: boolean;
  shipType?: string;
  /** Defender ship turn-cost from players_present (LEG-391; default handled client-side). */
  attack_turn_cost?: number | null;
  /** Optional combat snapshot when target is another ship (hull/shields for HUD bars). */
  combat?: {
    hull?: number | null;
    max_hull?: number | null;
    shields?: number | null;
    max_shields?: number | null;
  } | null;
  /** Optional cargo snapshot when target is another ship (used for cargo HUD bar). */
  cargo?: {
    used?: number | null;
    capacity?: number | null;
  } | null;
}

interface CombatInterfaceProps {
  target?: CombatTarget;
  onCombatEnd?: (result: CombatStatus) => void;
  onClose?: () => void;
}

export const CombatInterface: React.FC<CombatInterfaceProps> = ({
  target,
  onCombatEnd,
  onClose
}) => {
  // Wrap in the cockpit shell + WEAPONS CONSOLE instrument when standalone
  // (no onClose prop = used as a route). When embedded as a modal (onClose
  // provided), render bare so the parent's shell isn't duplicated.
  const isStandalone = !onClose;
  const Wrapper = isStandalone ? WeaponsConsoleShell : React.Fragment;

  const {
    playerState,
    currentShip,
    currentSector,
    planetsInSector,
    stationsInSector,
    refreshPlayerState
  } = useGame();

  // Combat state
  const [combatId, setCombatId] = useState<string | null>(null);
  const [combatStatus, setCombatStatus] = useState<CombatStatus | null>(null);
  const [isEngaging, setIsEngaging] = useState(false);
  const [roundAction, setRoundAction] = useState<'attack' | 'defend' | 'evade' | 'flee'>(
    'attack',
  );
  const [retreatBusy, setRetreatBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Target selected from the in-sector target list (when no target prop is given,
  // e.g. when rendered as the /game/combat route)
  const [selectedTarget, setSelectedTarget] = useState<CombatTarget | null>(null);
  const [ariaAdviceShipType, setAriaAdviceShipType] = useState<string | null>(null);
  const combatTarget = target ?? selectedTarget;

  // UI state
  const [showCombatLog, setShowCombatLog] = useState(true);

  // Helpers for HUD bars.
  const numOrNull = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
  const pctOrNull = (current: number | null, max: number | null): number | null => {
    if (current == null || max == null || max <= 0) return null;
    return Math.max(0, Math.min(100, (current / max) * 100));
  };
  const cargoUsedAndCap = (cargo: any): { used: number | null; capacity: number | null } => {
    if (!cargo || typeof cargo !== 'object') return { used: null, capacity: null };
    const used =
      typeof cargo.used === 'number'
        ? cargo.used
        : typeof cargo.used === 'string'
          ? Number(cargo.used)
          : null;
    const capacity =
      typeof cargo.capacity === 'number'
        ? cargo.capacity
        : typeof cargo.capacity === 'string'
          ? Number(cargo.capacity)
          : null;
    return {
      used: typeof used === 'number' && Number.isFinite(used) ? used : null,
      capacity: typeof capacity === 'number' && Number.isFinite(capacity) ? capacity : null,
    };
  };

  const playerCombat = currentShip?.combat ?? null;
  const oppCombat = combatTarget?.combat ?? null;

  const playerCargo = currentShip?.cargo ?? null;
  const playerCargoCap =
    typeof currentShip?.cargo_capacity === 'number' ? currentShip?.cargo_capacity : null;

  const playerCargoUsed = cargoUsedAndCap(playerCargo).used;
  const playerCargoCapacity = playerCargoCap ?? cargoUsedAndCap(playerCargo).capacity;

  const oppCargoUsed = cargoUsedAndCap(combatTarget?.cargo).used;
  const oppCargoCapacity = cargoUsedAndCap(combatTarget?.cargo).capacity;

  const turnCost = typeof combatTarget?.attack_turn_cost === 'number' ? combatTarget.attack_turn_cost : 2;

  // Handle combat end
  const handleCombatEnd = useCallback((status: CombatStatus) => {
    // Clear rate limit on combat end
    if (playerState) {
      InputValidator.clearRateLimit(`combat_${playerState.id}`);
    }

    // Refresh player state to update turns, drones, cargo, etc.
    refreshPlayerState();

    // Notify parent component
    if (onCombatEnd) {
      onCombatEnd(status);
    }
  }, [playerState, refreshPlayerState, onCombatEnd]);

  // Initiate combat against an explicit target (from prop or target selection).
  // The backend resolves the whole fight in this call — the follow-up status
  // fetch returns the completed result with every round for replay.
  const initiateCombat = useCallback(async (engageTarget: CombatTarget) => {
    if (!playerState || isEngaging) return;

    // Validate inputs
    const validation = InputValidator.validateCombatParams({
      targetType: engageTarget.type,
      targetId: engageTarget.id
    });

    if (!validation.valid) {
      setError(validation.errors.join(', '));
      SecurityAudit.log({
        type: 'validation_failure',
        details: { errors: validation.errors, target: engageTarget },
        userId: playerState.id
      });
      return;
    }

    // Rate limiting check
    if (!InputValidator.checkRateLimit(`combat_${playerState.id}`, 5, 60000)) {
      setError('Too many combat attempts. Please wait before engaging again.');
      SecurityAudit.log({
        type: 'rate_limit_exceeded',
        details: { action: 'combat_initiation' },
        userId: playerState.id
      });
      return;
    }

    setIsEngaging(true);
    setError(null);

    try {
      const response = await gameAPI.combat.engage(engageTarget.type, engageTarget.id);

      if (response.status === 'initiated' && response.combatId) {
        setCombatId(response.combatId);

        const status = await gameAPI.combat.getStatus(response.combatId) as CombatStatus;
        if (status) {
          setCombatStatus(status);
          if (status.status === 'completed') {
            handleCombatEnd(status);
          }
        }
      } else {
        setError(response.message || 'Failed to initiate combat');
      }
    } catch (err) {
      setError(formatCombatInitiateError(err));
      console.error('Combat initiation failed:', err);
    } finally {
      setIsEngaging(false);
    }
  }, [playerState, isEngaging, handleCombatEnd]);

  // Select a target from the in-sector list and engage immediately
  const handleEngageTarget = useCallback((engageTarget: CombatTarget) => {
    setSelectedTarget(engageTarget);
    setError(null);
    initiateCombat(engageTarget);
  }, [initiateCombat]);

  // Reset to target selection for another engagement
  const resetCombat = useCallback(() => {
    setCombatId(null);
    setCombatStatus(null);
    setSelectedTarget(null);
    setError(null);
  }, []);

  // Resolve the headline result text from the player's perspective
  const getResultHeadline = (status: CombatStatus): string => {
    if (status.outcome === 'escaped') return 'DISENGAGED';
    if (status.winner === 'draw') {
      // The backend collapses MUTUAL_DESTRUCTION into the 'draw' outcome
      // (the combat_logs outcome column has no dedicated value) — tell
      // them apart by checking whether both sides' ships were destroyed
      // in the round log ('attacker' destroying = defender ship died,
      // 'defender' destroying = the player's attacking ship died).
      const destroyers = new Set(
        status.rounds
          .filter(e => e.action === 'ship_destroyed' && e.actor)
          .map(e => e.actor as string)
      );
      if (destroyers.has('attacker') && destroyers.has('defender')) {
        return 'MUTUAL DESTRUCTION';
      }
      return 'STALEMATE';
    }
    if (status.winner && playerState && status.winner === playerState.id) return 'VICTORY!';
    return 'DEFEATED';
  };

  // Build target lists from the current sector (GameContext)
  type TargetOption = CombatTarget & {
    subtype: string;
    /** false → not attackable (renders a disabled note instead of ENGAGE) */
    engageable?: boolean;
    note?: string;
    /** NPC moral standing: true = a lawful target a paladin can engage freely */
    lawful?: boolean;
    shipType?: string;
  };

  // NPC standing read for the target list (mirrors the server notoriety tiers).
  const npcStanding = (p: any): { label: string; lawful: boolean } | null => {
    if (!p.is_npc) return null;
    const arch = String(p.archetype || '').toUpperCase();
    if (arch === 'LAW_ENFORCEMENT') return { label: 'law enforcement', lawful: false };
    if (arch === 'HOSTILE_RAIDER') return { label: 'hostile', lawful: true };
    const n = typeof p.notoriety === 'number' ? p.notoriety : 0;
    if (n >= 75) return { label: 'notorious trader — fair game', lawful: true };
    if (n >= 50) return { label: 'unscrupulous trader — fair game', lawful: true };
    if (n >= 25) return { label: 'merchant — protected', lawful: false };
    return { label: 'reputable merchant — protected', lawful: false };
  };

  const shipTargets: TargetOption[] = (currentSector?.players_present ?? [])
    .filter((p: any) => p && p.player_id && p.player_id !== playerState?.id && p.ship_id)
    .map((p: any) => {
      const standing = npcStanding(p);
      const attackTurnCost =
        typeof p.attack_turn_cost === 'number' && Number.isFinite(p.attack_turn_cost)
          ? p.attack_turn_cost
          : null;
      const combatSnapshot =
        p.combat && typeof p.combat === 'object' ? (p.combat as CombatTarget['combat']) : null;
      const cargoSnapshot =
        p.cargo && typeof p.cargo === 'object' ? (p.cargo as CombatTarget['cargo']) : null;
      const hull = p.ship_type && p.ship_type !== 'None'
        ? String(p.ship_type).replace(/_/g, ' ').toLowerCase()
        : 'ship';
      return {
        id: p.ship_id as string,
        name: p.ship_name && p.ship_name !== 'None'
          ? `${p.username} — ${p.ship_name}`
          : p.username || 'Unknown pilot',
        type: 'ship' as const,
        isNpc: !!p.is_npc,
        shipType: p.ship_type && p.ship_type !== 'None' ? String(p.ship_type) : undefined,
        subtype: standing ? `${hull} · ${standing.label}` : hull,
        lawful: standing?.lawful,
        attack_turn_cost: attackTurnCost,
        combat: combatSnapshot,
        cargo: cargoSnapshot,
      };
    });

  const planetTargets: TargetOption[] = planetsInSector
    .filter(planet => !planet.owner_id || planet.owner_id !== playerState?.id)
    .map(planet => ({
      id: planet.id,
      name: planet.name,
      type: 'planet' as const,
      subtype: planet.owner_name
        ? `${planet.type} — owned by ${planet.owner_name}`
        : `${planet.type} — unclaimed`
    }));

  // Port assault is LIVE: player_combat engage targetType=="port" →
  // combat_service.attack_port (WO attack-port-build). Capture remains
  // kernel-unreachable; the server still rejects unowned / own /
  // teammate / docked-or-landed cases with a clear message. Mirror that
  // honesty in the target list so ENGAGE reaches the live route instead
  // of a stale "NOT AUTHORIZED" hard-block.
  const stationTargets: TargetOption[] = stationsInSector.map(station => {
    const directOwnerId = (station as { owner_id?: string | null }).owner_id;
    let ownerId: string | null =
      typeof directOwnerId === 'string' && directOwnerId.length > 0 ? directOwnerId : null;
    if (!ownerId && Array.isArray(station.owner) && station.owner[0]) {
      const nested = String((station.owner[0] as { id?: string }).id ?? '');
      ownerId = nested || null;
    } else if (!ownerId && station.owner && typeof station.owner === 'object' && !Array.isArray(station.owner)) {
      const nested = String((station.owner as { id?: string }).id ?? '');
      ownerId = nested || null;
    }
    const isOwn =
      !!ownerId && !!playerState?.id && String(ownerId) === String(playerState.id);
    if (isOwn) {
      return {
        id: station.id,
        name: station.name,
        type: 'port' as const,
        subtype: `${station.type} — yours`,
        engageable: false,
        note: 'YOUR PORT',
      };
    }
    if (!ownerId) {
      return {
        id: station.id,
        name: station.name,
        type: 'port' as const,
        subtype: `${station.type} — unowned`,
        engageable: false,
        note: 'UNOWNED — NO ASSAULT',
      };
    }
    return {
      id: station.id,
      name: station.name,
      type: 'port' as const,
      subtype: station.type,
      engageable: true,
    };
  });

  const renderTargetGroup = (
    title: string,
    targets: TargetOption[],
    emptyText: string
  ) => (
    <div className="target-group">
      <h3>{title}</h3>
      {targets.length === 0 ? (
        <div className="target-empty">{emptyText}</div>
      ) : (
        targets.map(t => (
          <div key={`${t.type}-${t.id}`} className="target-row">
            <div className="target-info">
              <span className="target-name">
                {t.name}
                {t.isNpc && <span className="npc-badge"> NPC</span>}
                {t.isNpc && t.lawful === false && (
                  <span className="npc-badge" style={{ color: '#00ff41', borderColor: 'rgba(0,255,65,0.5)' }}> PROTECTED</span>
                )}
                {t.isNpc && t.lawful === true && (
                  <span className="npc-badge" style={{ color: '#ffb000', borderColor: 'rgba(255,176,0,0.5)' }}> FAIR GAME</span>
                )}
              </span>
              <span className="target-type-label">{t.subtype}</span>
            </div>
            {t.engageable === false ? (
              <button className="cockpit-btn engage-target-btn" disabled title="Port assault is not yet authorized">
                {t.note || 'N/A'}
              </button>
            ) : (
              <div className="target-actions">
                {t.type === 'ship' && t.shipType && (
                  <button
                    type="button"
                    className="cockpit-btn aria-advice-btn"
                    onClick={() => setAriaAdviceShipType(
                      ariaAdviceShipType === t.shipType ? null : t.shipType!,
                    )}
                    aria-pressed={ariaAdviceShipType === t.shipType}
                  >
                    ARIA
                  </button>
                )}
                <button
                  className="cockpit-btn danger engage-target-btn"
                  onClick={() => handleEngageTarget({
                    id: t.id,
                    name: t.name,
                    type: t.type,
                    isNpc: t.isNpc,
                    shipType: t.shipType,
                    attack_turn_cost: t.attack_turn_cost ?? null,
                    combat: t.combat ?? null,
                    cargo: t.cargo ?? null,
                  })}
                  disabled={isEngaging}
                >
                  {isEngaging ? '...' : 'ENGAGE'}
                </button>
              </div>
            )}
          </div>
        ))
      )}
    </div>
  );

  if (!combatTarget) {
    return (
      <Wrapper>
        <div className="combat-interface target-selection">
          <div className="combat-header">
            <h2>SELECT COMBAT TARGET</h2>
            {onClose && (
              <button className="close-btn" onClick={onClose}>×</button>
            )}
          </div>

          {error && (
            <div className="combat-error">
              <span className="error-icon">⚠️</span>
              {error}
            </div>
          )}

          <p className="target-selection-hint">
            {currentSector
              ? `Targets in sector ${currentSector.sector_number ?? currentSector.sector_id} — ${currentSector.name}`
              : 'Scanning sector for targets...'}
          </p>

          {ariaAdviceShipType && (
            <CombatAdvicePanel opponentShipType={ariaAdviceShipType} />
          )}

          <div className="target-groups">
            {renderTargetGroup('Ships', shipTargets, 'No ships in sector')}
            {renderTargetGroup('Planets', planetTargets, 'No planets in sector')}
            {renderTargetGroup('Stations', stationTargets, 'No stations in sector')}
          </div>
        </div>
      </Wrapper>
    );
  }

  return (
    <Wrapper>
    <div className="combat-interface">
      <div className="combat-header">
        <h2>COMBAT ENGAGEMENT</h2>
        {onClose && (
          <button className="close-btn" onClick={onClose}>×</button>
        )}
      </div>

      {error && (
        <div className="combat-error">
          <span className="error-icon">⚠️</span>
          {error}
        </div>
      )}

      <div className="combat-main">
        {/* Player Status */}
        <div className="combatant player">
          <h3>{currentShip?.name || 'Your Ship'}</h3>
          <div className="ship-type">{currentShip?.type || 'Unknown'}</div>

          {currentShip && (
            <>
              {combatId && (
                <>
                  <div
                    className="health-bar"
                    data-testid="combat-hull-bar-player"
                    aria-label="Player hull"
                  >
                    <div
                      className="health-fill"
                      style={{
                        width: `${pctOrNull(numOrNull(playerCombat?.hull), numOrNull(playerCombat?.max_hull)) ?? 0}%`,
                      }}
                    />
                    <div className="health-text">
                      {(() => {
                        const h = numOrNull(playerCombat?.hull);
                        const mh = numOrNull(playerCombat?.max_hull);
                        return h != null && mh != null ? `${h} / ${mh}` : '—';
                      })()}
                    </div>
                  </div>
                  <div
                    className="health-bar"
                    data-testid="combat-shield-bar-player"
                    aria-label="Player shields"
                  >
                    <div
                      className="health-fill"
                      style={{
                        width: `${pctOrNull(numOrNull(playerCombat?.shields), numOrNull(playerCombat?.max_shields)) ?? 0}%`,
                        background: 'linear-gradient(90deg, #00ccff, #0077ff)',
                      }}
                    />
                    <div className="health-text">
                      {(() => {
                        const s = numOrNull(playerCombat?.shields);
                        const ms = numOrNull(playerCombat?.max_shields);
                        return s != null && ms != null ? `${s} / ${ms}` : '—';
                      })()}
                    </div>
                  </div>
                  <div
                    className="health-bar"
                    data-testid="combat-cargo-bar-player"
                    aria-label="Player cargo"
                  >
                    <div
                      className="health-fill"
                      style={{
                        width: `${pctOrNull(playerCargoUsed, playerCargoCapacity) ?? 0}%`,
                        background: 'linear-gradient(90deg, #a78bfa, #6d28d9)',
                      }}
                    />
                    <div className="health-text">
                      {(() => {
                        return playerCargoUsed != null && playerCargoCapacity != null
                          ? `${playerCargoUsed} / ${playerCargoCapacity}`
                          : '—';
                      })()}
                    </div>
                  </div>
                </>
              )}

              <div className="combat-stats">
                <div>Attack: {currentShip.combat?.attack_rating || 0}</div>
                <div>Defense: {currentShip.combat?.defense_rating || 0}</div>
                <div>Drones: {playerState?.defense_drones ?? 0}</div>
              </div>
            </>
          )}
        </div>

        {/* Combat Arena */}
        <div className="combat-arena">
          {!combatId ? (
            <div className="pre-combat">
              <p>
                Prepare for combat against {combatTarget.name}
                {combatTarget.isNpc && <span className="npc-badge"> NPC</span>}
              </p>
              <div className="turn-cost-preview" role="status" data-testid="combat-turn-cost-preview">
                Costs {turnCost} turn{turnCost === 1 ? '' : 's'}
              </div>
              <button
                className="cockpit-btn danger engage-btn"
                onClick={() => initiateCombat(combatTarget)}
                disabled={isEngaging}
                title={`Costs ${turnCost} turn${turnCost === 1 ? '' : 's'}`}
              >
                {isEngaging ? 'Engaging...' : 'ENGAGE COMBAT'}
              </button>
              {!target && (
                <button
                  className="cockpit-btn secondary change-target-btn"
                  onClick={resetCombat}
                  disabled={isEngaging}
                >
                  ← CHANGE TARGET
                </button>
              )}
            </div>
          ) : (
            <div className="combat-active">
              <div className="combat-status">
                {!combatStatus ? (
                  <div className="round-indicator">Resolving combat...</div>
                ) : (
                  <div className="combat-result">
                    <h3>COMBAT COMPLETE</h3>
                    <div className="winner">{getResultHeadline(combatStatus)}</div>
                    <div className="combat-rounds-summary">
                      Resolved in {combatStatus.rounds.length > 0
                        ? Math.max(...combatStatus.rounds.map(r => r.round))
                        : 0} rounds
                    </div>
                    {((combatStatus.creditsLooted ?? 0) > 0 ||
                      (combatStatus.cargoLooted?.length ?? 0) > 0) && (
                      <div className="loot-display">
                        <h4>Salvage Recovered:</h4>
                        {(combatStatus.creditsLooted ?? 0) > 0 && (
                          <div>Credits: {combatStatus.creditsLooted}</div>
                        )}
                        {combatStatus.cargoLooted && combatStatus.cargoLooted.length > 0 && (
                          <div>Cargo: {combatStatus.cargoLooted.join(', ')}</div>
                        )}
                      </div>
                    )}
                    {!target && (
                      <button
                        className="cockpit-btn secondary change-target-btn"
                        onClick={resetCombat}
                      >
                        ← NEW TARGET
                      </button>
                    )}
                  </div>
                )}

                {/* Round-action controls (per-fight round HUD). */}
                <div className="combat-actions" data-testid="combat-round-actions" aria-label="Round actions">
                  {(
                    [
                      { key: 'attack' as const, label: 'Attack' },
                      { key: 'defend' as const, label: 'Defend' },
                      { key: 'evade' as const, label: 'Evade' },
                    ] as const
                  ).map((a) => (
                    <button
                      key={a.key}
                      type="button"
                      className={`action-btn ${roundAction === a.key ? 'active' : ''}`}
                      onClick={() => {
                        setRoundAction(a.key);
                        setError(null);
                      }}
                      disabled={retreatBusy}
                      aria-pressed={roundAction === a.key}
                      title="Tactical round action (UI intent only)"
                    >
                      {a.label}
                    </button>
                  ))}
                  <button
                    type="button"
                    className={`action-btn retreat ${roundAction === 'flee' ? 'active' : ''}`}
                    data-testid="combat-round-action-flee"
                    onClick={async () => {
                      if (!combatId || retreatBusy) return;
                      setRetreatBusy(true);
                      setError(null);
                      setRoundAction('flee');
                      try {
                        const result = await gameAPI.combat.retreat(combatId);
                        if (result?.success) {
                          await refreshPlayerState();
                        } else {
                          setError(result?.message || 'Retreat failed.');
                        }
                      } catch (e: unknown) {
                        setError(formatCombatInitiateError(e));
                      } finally {
                        setRetreatBusy(false);
                      }
                    }}
                    disabled={retreatBusy}
                    aria-pressed={roundAction === 'flee'}
                    title="Flee (calls combatAPI.retreat)"
                  >
                    {retreatBusy ? 'Fleeing…' : 'Flee'}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Target Status */}
        <div className="combatant target">
          <h3>
            {combatTarget.name}
            {combatTarget.isNpc && <span className="npc-badge"> NPC</span>}
          </h3>
          <div className="ship-type">{combatTarget.type}</div>

          {combatId && (
            <>
              <div
                className="health-bar"
                data-testid="combat-hull-bar-opponent"
                aria-label="Opponent hull"
              >
                <div
                  className="health-fill enemy"
                  style={{
                    width: `${pctOrNull(numOrNull(oppCombat?.hull), numOrNull(oppCombat?.max_hull)) ?? 0}%`,
                  }}
                />
                <div className="health-text">
                  {(() => {
                    const h = numOrNull(oppCombat?.hull);
                    const mh = numOrNull(oppCombat?.max_hull);
                    return h != null && mh != null ? `${h} / ${mh}` : '—';
                  })()}
                </div>
              </div>
              <div
                className="health-bar"
                data-testid="combat-shield-bar-opponent"
                aria-label="Opponent shields"
              >
                <div
                  className="health-fill enemy"
                  style={{
                    width: `${pctOrNull(numOrNull(oppCombat?.shields), numOrNull(oppCombat?.max_shields)) ?? 0}%`,
                    background: 'linear-gradient(90deg, #ff0000, #cc0000)',
                  }}
                />
                <div className="health-text">
                  {(() => {
                    const s = numOrNull(oppCombat?.shields);
                    const ms = numOrNull(oppCombat?.max_shields);
                    return s != null && ms != null ? `${s} / ${ms}` : '—';
                  })()}
                </div>
              </div>
              <div
                className="health-bar"
                data-testid="combat-cargo-bar-opponent"
                aria-label="Opponent cargo"
              >
                <div
                  className="health-fill enemy"
                  style={{
                    width: `${pctOrNull(oppCargoUsed, oppCargoCapacity) ?? 0}%`,
                    background: 'linear-gradient(90deg, #6d28d9, #2e1065)',
                  }}
                />
                <div className="health-text">
                  {(() => {
                    return oppCargoUsed != null && oppCargoCapacity != null
                      ? `${oppCargoUsed} / ${oppCargoCapacity}`
                      : '—';
                  })()}
                </div>
              </div>
            </>
          )}

          <div className="combat-stats">
            <div>Type: {combatTarget.type}</div>
          </div>
        </div>
      </div>

      {/* Combat Log — full round-by-round replay of the resolved fight */}
      {showCombatLog && combatStatus && combatStatus.rounds.length > 0 && (
        <div className="combat-log">
          <div className="log-header">
            <h4>COMBAT LOG</h4>
            <button
              className="toggle-log"
              onClick={() => setShowCombatLog(!showCombatLog)}
            >
              {showCombatLog ? '−' : '+'}
            </button>
          </div>
          <div className="log-entries">
            {combatStatus.rounds.map((event, index) => (
              <div key={index} className={`log-entry ${event.actor ?? ''}`}>
                <span className="round-num">R{event.round}:</span>
                <span className="log-message">{event.message}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* LEG-372 — paginated own-history browse (standalone Weapons Console only) */}
      {isStandalone && <CombatHistoryPanel />}
    </div>
    </Wrapper>
  );
};
