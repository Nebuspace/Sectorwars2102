/**
 * CombatInterface Component
 * 
 * Main combat engagement interface for ship-to-ship, ship-to-planet, 
 * and ship-to-port combat. Provides real-time combat visualization
 * and player controls during combat encounters.
 */

import React, { useState, useEffect, useCallback } from 'react';
import { useGame } from '../../contexts/GameContext';
import { gameAPI } from '../../services/api';
import { InputValidator, SecurityAudit } from '../../utils/security/inputValidation';
import GameLayout from '../layouts/GameLayout';
import './combat-interface.css';

// Define types locally since we're removing mocks
interface CombatStatus {
  combatId: string;
  status: 'initiated' | 'ongoing' | 'completed' | 'error';
  rounds: CombatRound[];
  winner?: string;
  message?: string;
  loot?: {
    credits: number;
    items: string[];
  };
}

interface CombatRound {
  round: number;
  roundNumber?: number;
  actions: Array<{
    attacker: string;
    target: string;
    damage: number;
    critical: boolean;
    message: string;
  }>;
  playerHealth: number;
  playerShields: number;
  targetHealth: number;
  targetShields: number;
  attackerHealth?: number;
  defenderHealth?: number;
  attackerAction?: {
    type: string;
    damage?: number;
  };
  defenderAction?: {
    type: string;
    damage?: number;
  };
}

interface CombatTarget {
  id: string;
  name: string;
  type: 'ship' | 'planet' | 'port';
  health?: number;
  shields?: number;
  drones?: number;
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
  const [selectedAction, setSelectedAction] = useState<'fire' | 'drones' | 'retreat'>('fire');
  const [retreatMessage, setRetreatMessage] = useState<{ success: boolean; message: string } | null>(null);

  // Target selected from the in-sector target list (when no target prop is given,
  // e.g. when rendered as the /game/combat route)
  const [selectedTarget, setSelectedTarget] = useState<CombatTarget | null>(null);
  const combatTarget = target ?? selectedTarget;

  // UI state
  const [showCombatLog, setShowCombatLog] = useState(true);
  const [animationState, setAnimationState] = useState<'idle' | 'attacking' | 'defending'>('idle');
  
  // Auto-refresh combat status
  useEffect(() => {
    if (!combatId || combatStatus?.status === 'completed') return;
    
    const interval = setInterval(async () => {
      try {
        const status = await gameAPI.combat.getStatus(combatId);
        if (status) {
          setCombatStatus(status);
          
          // Trigger animations based on latest round
          if (status.rounds.length > 0) {
            const latestRound = status.rounds[status.rounds.length - 1];
            if (latestRound.actions && latestRound.actions.length > 0) {
              setAnimationState('attacking');
              setTimeout(() => setAnimationState('idle'), 500);
            }
          }
          
          // Handle combat end
          if (status.status === 'completed') {
            handleCombatEnd(status);
          }
        }
      } catch (err) {
        console.error('Failed to fetch combat status:', err);
      }
    }, 1000);
    
    return () => clearInterval(interval);
  }, [combatId, combatStatus?.status]);
  
  // Initiate combat against an explicit target (from prop or target selection)
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
    setRetreatMessage(null);

    try {
      const response = await gameAPI.combat.engage(engageTarget.type, engageTarget.id);

      if (response.status === 'initiated' && response.combatId) {
        setCombatId(response.combatId);

        // Fetch initial status
        const initialStatus = await gameAPI.combat.getStatus(response.combatId);
        if (initialStatus) {
          setCombatStatus(initialStatus);
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
  }, [playerState, isEngaging]);

  // Select a target from the in-sector list and engage immediately
  const handleEngageTarget = useCallback((engageTarget: CombatTarget) => {
    setSelectedTarget(engageTarget);
    setError(null);
    initiateCombat(engageTarget);
  }, [initiateCombat]);
  
  // Handle combat end
  const handleCombatEnd = useCallback((status: CombatStatus) => {
    // Clear rate limit on combat end
    if (playerState) {
      InputValidator.clearRateLimit(`combat_${playerState.id}`);
    }
    
    // Refresh player state to update resources, health, etc.
    refreshPlayerState();
    
    // Notify parent component
    if (onCombatEnd) {
      onCombatEnd(status);
    }
  }, [playerState, refreshPlayerState, onCombatEnd]);
  
  // Attempt retreat
  const attemptRetreat = useCallback(async () => {
    if (!combatId || !playerState || combatStatus?.status === 'completed') return;

    setSelectedAction('retreat');
    setError(null);

    try {
      const result = await gameAPI.combat.retreat(combatId);
      setRetreatMessage({
        success: !!result.success,
        message: result.message || (result.success ? 'Retreat successful!' : 'Retreat failed!')
      });

      // On a successful retreat the combat ends — refresh status immediately
      if (result.success) {
        const status = await gameAPI.combat.getStatus(combatId);
        if (status) {
          setCombatStatus(status);
          if (status.status === 'completed') {
            handleCombatEnd(status);
          }
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Retreat attempt failed.');
      console.error('Retreat attempt failed:', err);
    }
  }, [combatId, playerState, combatStatus, handleCombatEnd]);
  
  // Calculate health percentages
  const getHealthPercentage = (current: number, max: number = 100): number => {
    return Math.max(0, Math.min(100, (current / max) * 100));
  };
  
  // Get latest round data
  const latestRound = combatStatus?.rounds[combatStatus.rounds.length - 1];
  const playerHealth = latestRound?.playerHealth ?? 100;
  const targetHealth = latestRound?.targetHealth ?? 100;

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
              <span className="target-name">{t.name}</span>
              <span className="target-type-label">{t.subtype}</span>
            </div>
            <button
              className="cockpit-btn danger engage-target-btn"
              onClick={() => handleEngageTarget({ id: t.id, name: t.name, type: t.type })}
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
    <div className={`combat-interface ${animationState}`}>
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
          
          <div className="health-bar">
            <div 
              className="health-fill"
              style={{ width: `${getHealthPercentage(playerHealth)}%` }}
            />
            <span className="health-text">{playerHealth}/100</span>
          </div>
          
          {currentShip && (
            <div className="combat-stats">
              <div>Attack: {currentShip.combat?.attack_rating || 0}</div>
              <div>Defense: {currentShip.combat?.defense_rating || 0}</div>
              <div>Drones: {currentShip.combat?.attack_drones || 0}</div>
            </div>
          )}
        </div>
        
        {/* Combat Arena */}
        <div className="combat-arena">
          {!combatId ? (
            <div className="pre-combat">
              <p>Prepare for combat against {combatTarget.name}</p>
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
                  onClick={() => {
                    setSelectedTarget(null);
                    setError(null);
                  }}
                  disabled={isEngaging}
                >
                  ← CHANGE TARGET
                </button>
              )}
            </div>
          ) : (
            <div className="combat-active">
              <div className="combat-status">
                {combatStatus?.status === 'ongoing' ? (
                  <>
                    <div className="round-indicator">
                      Round {combatStatus.rounds.length}
                    </div>
                    <div className="combat-actions">
                      <button 
                        className={`action-btn ${selectedAction === 'fire' ? 'active' : ''}`}
                        onClick={() => setSelectedAction('fire')}
                      >
                        FIRE WEAPONS
                      </button>
                      <button 
                        className={`action-btn ${selectedAction === 'drones' ? 'active' : ''}`}
                        onClick={() => setSelectedAction('drones')}
                        disabled={!currentShip?.combat?.attack_drones}
                      >
                        DEPLOY DRONES
                      </button>
                      <button
                        className={`action-btn retreat ${selectedAction === 'retreat' ? 'active' : ''}`}
                        onClick={attemptRetreat}
                      >
                        ATTEMPT RETREAT
                      </button>
                    </div>
                    {retreatMessage && (
                      <div className={`combat-retreat-message ${retreatMessage.success ? 'success' : 'failure'}`}>
                        <span className="retreat-icon">{retreatMessage.success ? '✓' : '✗'}</span>
                        {retreatMessage.message}
                      </div>
                    )}
                  </>
                ) : (
                  <div className="combat-result">
                    <h3>COMBAT COMPLETE</h3>
                    <div className="winner">
                      {combatStatus?.winner === 'attacker' ? 'VICTORY!' : 'DEFEATED'}
                    </div>
                    {combatStatus?.loot && (
                      <div className="loot-display">
                        <h4>Salvage Recovered:</h4>
                        <div>Credits: {combatStatus.loot.credits}</div>
                        {combatStatus.loot.items && combatStatus.loot.items.length > 0 && (
                          <div>
                            Items: {combatStatus.loot.items.join(', ')}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
        
        {/* Target Status */}
        <div className="combatant target">
          <h3>{combatTarget.name}</h3>
          <div className="ship-type">{combatTarget.type}</div>

          <div className="health-bar">
            <div
              className="health-fill enemy"
              style={{ width: `${getHealthPercentage(targetHealth)}%` }}
            />
            <span className="health-text">{targetHealth}/100</span>
          </div>

          <div className="combat-stats">
            <div>Type: {combatTarget.type}</div>
            {combatTarget.shields && <div>Shields: {combatTarget.shields}</div>}
            {combatTarget.drones && <div>Drones: {combatTarget.drones}</div>}
          </div>
        </div>
      </div>
      
      {/* Combat Log */}
      {showCombatLog && combatStatus && (
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
            {combatStatus.rounds.map((round, index) => (
              <div key={index} className="log-entry">
                <span className="round-num">R{round.roundNumber}:</span>
                <span className="attacker-action">
                  You {round.attackerAction.type === 'fire' ? 'fired weapons' : round.attackerAction.type}
                  {round.attackerAction.damage && ` (${round.attackerAction.damage} damage)`}
                </span>
                <span className="defender-action">
                  {combatTarget.name} {round.defenderAction.type === 'fire' ? 'returned fire' : round.defenderAction.type}
                  {round.defenderAction.damage && ` (${round.defenderAction.damage} damage)`}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
    </Wrapper>
  );
};