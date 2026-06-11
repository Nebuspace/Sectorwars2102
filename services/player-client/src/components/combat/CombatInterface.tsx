/**
 * CombatInterface Component
 *
 * Main combat engagement interface for ship-to-ship and ship-to-planet
 * combat. Combat resolves synchronously on the backend (a single engage
 * call resolves the whole fight), so this interface shows the resolved
 * outcome with a full round-by-round combat log replay.
 */

import React, { useState, useCallback } from 'react';
import { useGame } from '../../contexts/GameContext';
import { gameAPI } from '../../services/api';
import { InputValidator, SecurityAudit } from '../../utils/security/inputValidation';
import GameLayout from '../layouts/GameLayout';
import './combat-interface.css';

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
  // Wrap in GameLayout when standalone (no onClose prop = used as a route).
  // When embedded as a modal (onClose provided), render bare so the parent's
  // shell isn't duplicated inside the modal.
  const isStandalone = !onClose;
  const Wrapper = isStandalone ? GameLayout : React.Fragment;

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
  const [error, setError] = useState<string | null>(null);

  // Target selected from the in-sector target list (when no target prop is given,
  // e.g. when rendered as the /game/combat route)
  const [selectedTarget, setSelectedTarget] = useState<CombatTarget | null>(null);
  const combatTarget = target ?? selectedTarget;

  // UI state
  const [showCombatLog, setShowCombatLog] = useState(true);

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
      // apiRequest surfaces the server's `detail` message — show it inline
      setError(err instanceof Error ? err.message : 'Combat system error. Please try again.');
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
  type TargetOption = CombatTarget & { subtype: string };

  const shipTargets: TargetOption[] = (currentSector?.players_present ?? [])
    .filter((p: any) => p && p.player_id && p.player_id !== playerState?.id && p.ship_id)
    .map((p: any) => ({
      id: p.ship_id as string,
      name: p.ship_name && p.ship_name !== 'None'
        ? `${p.username} — ${p.ship_name}`
        : p.username || 'Unknown pilot',
      type: 'ship' as const,
      isNpc: !!p.is_npc,
      subtype: p.ship_type && p.ship_type !== 'None'
        ? String(p.ship_type).replace(/_/g, ' ').toLowerCase()
        : 'ship'
    }));

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

  const stationTargets: TargetOption[] = stationsInSector.map(station => ({
    id: station.id,
    name: station.name,
    type: 'port' as const,
    subtype: station.type
  }));

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
              </span>
              <span className="target-type-label">{t.subtype}</span>
            </div>
            <button
              className="cockpit-btn danger engage-target-btn"
              onClick={() => handleEngageTarget({ id: t.id, name: t.name, type: t.type, isNpc: t.isNpc })}
              disabled={isEngaging}
            >
              {isEngaging ? '...' : 'ENGAGE'}
            </button>
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
            <div className="combat-stats">
              <div>Attack: {currentShip.combat?.attack_rating || 0}</div>
              <div>Defense: {currentShip.combat?.defense_rating || 0}</div>
              <div>Drones: {playerState?.defense_drones ?? 0}</div>
            </div>
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
              <button
                className="cockpit-btn danger engage-btn"
                onClick={() => initiateCombat(combatTarget)}
                disabled={isEngaging}
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
    </div>
    </Wrapper>
  );
};
