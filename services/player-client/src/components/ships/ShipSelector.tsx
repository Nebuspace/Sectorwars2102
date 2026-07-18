/**
 * ShipSelector Component
 * 
 * Allows players to view and switch between their owned ships.
 * Displays ship stats, location, and condition for easy comparison.
 */

import React, { useState } from 'react';
import { useGame } from '../../contexts/GameContext';
import { Ship } from '../../types/game';
import { InputValidator, SecurityAudit } from '../../utils/security/inputValidation';
import { formatShipType } from '../../utils/formatters';
import CockpitInstrument from '../cockpit/CockpitInstrument';
import { useEmbedded } from '../cockpit/EmbeddedContext';
import './ship-selector.css';

interface ShipSelectorProps {
  onShipSelected?: (ship: Ship) => void;
  onClose?: () => void;
}

/* HANGAR console shell (Law 3) — module-level so React never remounts the
   frame (or the children) when the component re-renders between states.
   Renders just the framed instrument — no GameLayout wrapper (removed
   WO-UI0-PERSISTENT-SHELL lane C1; the persistent shell already wraps
   every /game/* route, so a second cockpit shell here would nest). */
const HangarShell: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const embedded = useEmbedded();
  const instrument = (
    <CockpitInstrument title="HANGAR" accent="#9EC5FF" subtitle="FLEET REGISTRY">
      {children}
    </CockpitInstrument>
  );
  return embedded ? instrument : instrument;
};

export const ShipSelector: React.FC<ShipSelectorProps> = ({
  onShipSelected,
  onClose
}) => {
  const { ships: gameShips, currentShip, setCurrentShip, playerState } = useGame();

  const [selectedShipId, setSelectedShipId] = useState<string | null>(currentShip?.id || null);
  const [isChangingShip, setIsChangingShip] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<'all' | 'active' | 'docked'>('all');
  const [sortBy, setSortBy] = useState<'name' | 'type' | 'location' | 'condition'>('name');

  // Show empty state if player has no ships — inside the frame so the
  // monitor chrome never unmounts between states.
  if (gameShips.length === 0) {
    return (
      <HangarShell>
        <div className="ship-selector-empty">
          <p>No ships available. Visit a shipyard to purchase your first ship.</p>
        </div>
      </HangarShell>
    );
  }

  // Filter ships based on selected filter
  const filteredShips = gameShips.filter(ship => {
    if (filter === 'all') return true;
    if (filter === 'active') return ship.id === currentShip?.id;
    if (filter === 'docked') return ship.id !== currentShip?.id;
    return true;
  });
  
  // Sort ships
  const sortedShips = [...filteredShips].sort((a, b) => {
    switch (sortBy) {
      case 'name':
        return a.name.localeCompare(b.name);
      case 'type':
        return a.type.localeCompare(b.type);
      case 'location':
        return a.sector_id - b.sector_id;
      case 'condition':
        return (b.maintenance?.current_rating || 100) - (a.maintenance?.current_rating || 100);
      default:
        return 0;
    }
  });
  
  // Handle ship selection
  const handleShipSelect = (ship: Ship) => {
    setSelectedShipId(ship.id);
    setError(null);
  };
  
  // WO-UI5-DOSSIER FLEET location-gate: mirrors the server's OWN gate on
  // POST /api/v1/ships/{id}/set-active (ship_upgrades.py set_active_ship) --
  // "the target ship must be in the player's current sector" and "lift off
  // before switching ships" (locked_player.is_landed). Both server checks
  // are re-derived client-side here so the button disables BEFORE a doomed
  // request round-trips, rather than only surfacing the 400 after the fact.
  // (The other two server checks -- ship.is_destroyed / ShipStatus.
  // HARMONIZING -- aren't in the client's Ship type; a destroyed/
  // harmonizing ship never appears in gameShips in the first place, so
  // there is nothing to gate on client-side for those.)
  const selectedShip = selectedShipId ? gameShips.find(s => s.id === selectedShipId) ?? null : null;
  const targetOutOfSector =
    !!selectedShip &&
    playerState?.current_sector_id != null &&
    selectedShip.sector_id !== playerState.current_sector_id;
  const blockedByLanding = !!playerState?.is_landed;

  // Pixel a11y fix (WO-UI5-DOSSIER gate review) -- single source of truth
  // for the disable reason, consumed by BOTH `title` (hover) and
  // `aria-label` (screen reader) below. `title` alone isn't reliably
  // announced by screen readers, so the reason must also live in the
  // accessible name.
  const switchDisabledReason = blockedByLanding
    ? 'Lift off before switching ships'
    : targetOutOfSector && selectedShip
      ? `${selectedShip.name} is in sector ${selectedShip.sector_id}; travel there to board it`
      : null;

  // Change active ship
  const handleChangeShip = async () => {
    if (!selectedShipId || selectedShipId === currentShip?.id || !playerState) return;
    if (blockedByLanding) {
      setError('Lift off before switching ships.');
      return;
    }
    if (targetOutOfSector && selectedShip) {
      setError(`${selectedShip.name} is in sector ${selectedShip.sector_id}; travel there to board it.`);
      return;
    }

    // Rate limiting
    if (!InputValidator.checkRateLimit(`ship_change_${playerState.id}`, 5, 300000)) {
      setError('Too many ship changes. Please wait before switching again.');
      SecurityAudit.log({
        type: 'rate_limit_exceeded',
        details: { action: 'ship_change' },
        userId: playerState.id
      });
      return;
    }
    
    setIsChangingShip(true);
    setError(null);
    
    try {
      await setCurrentShip(selectedShipId);
      
      const selectedShip = gameShips.find(s => s.id === selectedShipId);
      if (selectedShip && onShipSelected) {
        onShipSelected(selectedShip);
      }
      
      // Clear rate limit on successful change
      InputValidator.clearRateLimit(`ship_change_${playerState.id}`);
      
      if (onClose) {
        setTimeout(onClose, 500); // Brief delay to show success
      }
    } catch (err) {
      setError('Failed to change ship. Please try again.');
      console.error('Ship change failed:', err);
    } finally {
      setIsChangingShip(false);
    }
  };
  
  // Calculate cargo usage percentage
  const getCargoUsage = (ship: Ship): number => {
    const cargo = ship.cargo || {};
    // The API cargo format is {"capacity": N, "used": N, "contents": {...}}
    // If "used" field exists, use it directly; otherwise sum numeric values
    // excluding metadata fields like "capacity"
    let used: number;
    if (typeof cargo.used === 'number') {
      used = cargo.used;
    } else {
      // Fallback for alternative cargo formats: sum commodity values
      // but exclude metadata keys like "capacity", "used", "contents"
      const metadataKeys = ['capacity', 'used', 'contents'];
      used = Object.entries(cargo)
        .filter(([key, val]) => !metadataKeys.includes(key) && typeof val === 'number')
        .reduce((sum, [, val]) => sum + (val as number), 0);
    }
    const capacity = ship.cargo_capacity > 0 ? ship.cargo_capacity : (typeof cargo.capacity === 'number' ? cargo.capacity : 0);
    return capacity > 0 ? (used / capacity * 100) : 0;
  };
  
  // Get ship condition color
  const getConditionColor = (rating: number): string => {
    if (rating >= 80) return 'excellent';
    if (rating >= 60) return 'good';
    if (rating >= 40) return 'fair';
    if (rating >= 20) return 'poor';
    return 'critical';
  };

  // Format shields for display, handling both {current, max} and numeric formats
  const getShieldsDisplay = (ship: Ship): string => {
    const shields = ship.combat?.shields;
    if (shields == null) return 'N/A';
    if (typeof shields === 'object') {
      return `${shields.current}/${shields.max}`;
    }
    return ship.combat?.max_shields ? `${shields}/${ship.combat.max_shields}` : `${shields}`;
  };
  
  return (
    <HangarShell>
      <div className="ship-selector">
      {/* The instrument LED header carries HANGAR on the route; the old
          page header renders only in (future) modal usage with onClose. */}
      {onClose && (
        <div className="selector-header">
          <h2>SHIP HANGAR</h2>
          <button className="close-btn" onClick={onClose}>×</button>
        </div>
      )}

      {error && (
        <div className="selector-error">
          <span className="error-icon">⚠️</span>
          {error}
        </div>
      )}
      
      <div className="selector-controls">
        <div className="filter-group">
          <button
            className={`filter-btn ${filter === 'all' ? 'active' : ''}`}
            onClick={() => setFilter('all')}
          >
            All Ships ({gameShips.length})
          </button>
          <button
            className={`filter-btn ${filter === 'active' ? 'active' : ''}`}
            onClick={() => setFilter('active')}
          >
            Active
          </button>
          <button
            className={`filter-btn ${filter === 'docked' ? 'active' : ''}`}
            onClick={() => setFilter('docked')}
          >
            Docked
          </button>
        </div>
        
        <div className="sort-group">
          <label>Sort by:</label>
          <select 
            value={sortBy} 
            onChange={(e) => setSortBy(e.target.value as any)}
            className="sort-select"
          >
            <option value="name">Name</option>
            <option value="type">Type</option>
            <option value="location">Location</option>
            <option value="condition">Condition</option>
          </select>
        </div>
      </div>
      
      <div className="ships-grid">
        {sortedShips.map(ship => (
          <div
            key={ship.id}
            className={`ship-card ${selectedShipId === ship.id ? 'selected' : ''} ${ship.is_flagship ? 'flagship' : ''}`}
            onClick={() => handleShipSelect(ship)}
            role="button"
            tabIndex={0}
            aria-pressed={selectedShipId === ship.id}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                handleShipSelect(ship);
              }
            }}
          >
            <div className="ship-header">
              <h3>{ship.name}</h3>
              <div className="ship-badges">
                {ship.is_flagship && <span className="flagship-badge">FLAGSHIP</span>}
                {ship.id === currentShip?.id && <span className="active-badge">ACTIVE</span>}
              </div>
            </div>

            <div className="ship-subheader">
              <span className="subheader-type">{formatShipType(ship.type)}</span>
              <span className="subheader-location">Sector {ship.sector_id}</span>
            </div>

            <div className="ship-stats">
              <div className="stat-chips-grid">
                <div className="stat-chip">
                  <span className="label">Speed</span>
                  <span className="value">{ship.current_speed}/{ship.base_speed}</span>
                </div>
                <div className="stat-chip">
                  <span className="label">Attack</span>
                  <span className="value">{ship.combat?.attack_rating ?? ship.combat?.weapons ?? 'N/A'}</span>
                </div>
                <div className="stat-chip">
                  <span className="label">Defense</span>
                  <span className="value">{ship.combat?.defense_rating ?? 'N/A'}</span>
                </div>
                <div className="stat-chip">
                  <span className="label">Drones</span>
                  <span className="value">
                    {(ship.combat?.attack_drones || 0) + (ship.combat?.defense_drones || 0)}/{ship.combat?.max_drones || 0}
                  </span>
                </div>
                {ship.combat?.shields != null && (
                  <div className="stat-chip">
                    <span className="label">Shields</span>
                    <span className="value">{getShieldsDisplay(ship)}</span>
                  </div>
                )}
                <div className="stat-chip">
                  <span className="label">Value</span>
                  <span className="value value-credits">{ship.current_value.toLocaleString()}</span>
                </div>
              </div>

              <div className="condition-section">
                <div className="condition-label">Condition</div>
                <div className="condition-bar">
                  <div
                    className={`condition-fill ${getConditionColor(ship.maintenance?.current_rating || 100)}`}
                    style={{ width: `${ship.maintenance?.current_rating || 100}%` }}
                  />
                  <span className="condition-text">
                    {ship.maintenance?.current_rating || 100}%
                  </span>
                </div>
                {ship.maintenance?.failure_status && ship.maintenance.failure_status !== 'NONE' && (
                  <div className="failure-warning">
                    ⚠️ {ship.maintenance.failure_status} failure detected
                  </div>
                )}
              </div>

              <div className="cargo-section">
                <div className="cargo-label">Cargo Hold</div>
                <div className="cargo-bar">
                  <div
                    className="cargo-fill"
                    style={{ width: `${getCargoUsage(ship)}%` }}
                  />
                  <span className="cargo-text">
                    {Math.round(getCargoUsage(ship))}% full
                  </span>
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>
      
      <div className="selector-actions">
        <button
          className="cockpit-btn primary"
          onClick={handleChangeShip}
          disabled={
            !selectedShipId ||
            selectedShipId === currentShip?.id ||
            isChangingShip ||
            blockedByLanding ||
            targetOutOfSector
          }
          title={switchDisabledReason ?? undefined}
          aria-label={switchDisabledReason ? `Make Active Ship – ${switchDisabledReason}` : undefined}
        >
          {isChangingShip ? 'Changing Ship...' : 'Make Active Ship'}
        </button>
        {onClose && (
          <button
            className="cockpit-btn secondary"
            onClick={onClose}
          >
            Cancel
          </button>
        )}
      </div>
    </div>
    </HangarShell>
  );
};