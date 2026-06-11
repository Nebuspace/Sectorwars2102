import React, { useState } from 'react';
import ConfirmDialog, { type PendingConfirm } from './ConfirmDialog';
import './planet-card.css';

interface PlanetCardProps {
  planet: {
    id: string;
    name: string;
    type: string;
    status: string;
    sector_id: number;
    owner_id?: string | null;
    owner_name?: string | null;
    resources?: {
      [key: string]: any;
    };
    population?: number;
    max_population?: number;
    habitability_score?: number;
  };
  onLand?: (planetId: string) => void;
  onClaim?: (planetId: string) => void;
  isLanded?: boolean;
}

const PlanetCard: React.FC<PlanetCardProps> = ({ planet, onLand, onClaim, isLanded = false }) => {
  // Determine if planet is unclaimed
  const isUnclaimed = !planet.owner_id && !planet.owner_name && planet.name !== 'New Earth';

  // In-fiction confirmation dialog state (replaces native confirm())
  const [pendingConfirm, setPendingConfirm] = useState<PendingConfirm | null>(null);

  const handleClick = () => {
    if (isLanded) return;

    if (isUnclaimed) {
      // Planet is unclaimed - need to claim it
      if (!onClaim) return;
      // Capture narrowed callback for the deferred onConfirm closure
      const claim = onClaim;
      setPendingConfirm({
        title: 'Claim Planet',
        message: `Claim ${planet.name}?\n\nThis planet is unclaimed. Claiming it will make you the owner and automatically land your ship.`,
        confirmLabel: 'Claim',
        onConfirm: () => claim(planet.id)
      });
    } else {
      // Planet is owned - just land
      if (!onLand) return;
      // Capture narrowed callback for the deferred onConfirm closure
      const land = onLand;
      setPendingConfirm({
        title: 'Landing Request',
        message: `Land on ${planet.name}?`,
        confirmLabel: 'Land',
        onConfirm: () => land(planet.id)
      });
    }
  };
  // Planet type configurations
  const planetTypeInfo: {
    [key: string]: { icon: string; color: string; climate: string };
  } = {
    'terran': { icon: '🌍', color: '#00ff41', climate: 'Earth-like' },
    'ice': { icon: '🧊', color: '#00d9ff', climate: 'Frozen' },
    'volcanic': { icon: '🌋', color: '#ff6b00', climate: 'Volcanic' },
    'gas_giant': { icon: '🪐', color: '#c961de', climate: 'Gas Giant' },
    'barren': { icon: '🌑', color: '#8b4513', climate: 'Barren' },
    'oceanic': { icon: '🌊', color: '#0088ff', climate: 'Oceanic' },
    'desert': { icon: '🏜️', color: '#ffb000', climate: 'Desert' },
    'jungle': { icon: '🌴', color: '#22c55e', climate: 'Jungle' }
  };

  // Resource icons
  const resourceIcons: { [key: string]: string } = {
    'ore': '⛏️',
    'fuel': '⛽',
    'organics': '🌱',
    'equipment': '⚙️',
    'water': '💧',
    'minerals': '💎',
    'energy': '⚡',
    'exotic': '✨'
  };

  const typeInfo = planetTypeInfo[planet.type?.toLowerCase()] || planetTypeInfo['barren'];

  // Calculate population percentage
  const populationPercent = planet.max_population
    ? ((planet.population || 0) / planet.max_population) * 100
    : 0;

  // Format population for display
  const formatPopulation = (pop: number | undefined) => {
    if (!pop) return '0';
    if (pop >= 1000000000) return `${(pop / 1000000000).toFixed(1)}B`;
    if (pop >= 1000000) return `${(pop / 1000000).toFixed(1)}M`;
    if (pop >= 1000) return `${(pop / 1000).toFixed(1)}K`;
    return pop.toString();
  };

  // Get habitability level
  const getHabitabilityLevel = (score: number | undefined) => {
    if (!score) return { label: 'Uninhabitable', color: '#6b7280' };
    if (score >= 80) return { label: 'Excellent', color: '#00ff41' };
    if (score >= 60) return { label: 'Good', color: '#00d9ff' };
    if (score >= 40) return { label: 'Fair', color: '#ffb000' };
    if (score >= 20) return { label: 'Poor', color: '#ff6b00' };
    return { label: 'Harsh', color: '#ef4444' };
  };

  const habitability = getHabitabilityLevel(planet.habitability_score);

  // Get available resources
  const availableResources = planet.resources
    ? Object.entries(planet.resources)
        .filter(([_, value]) => value && (typeof value === 'boolean' || value > 0))
        .map(([resource, _]) => resource)
    : [];

  // Determine if card is clickable
  const isClickable = !isLanded && ((isUnclaimed && onClaim) || (!isUnclaimed && onLand));

  return (
    <div
      className={`planet-card ${isClickable ? 'clickable' : ''} ${isUnclaimed ? 'unclaimed' : ''}`}
      onClick={handleClick}
    >
      {/* Planet Header */}
      <div className="planet-card-header">
        <div className="planet-icon" style={{ filter: `drop-shadow(0 0 12px ${typeInfo.color})` }}>
          {typeInfo.icon}
        </div>
        <div className="planet-info">
          <div className="planet-name">{planet.name}</div>
          <div className="planet-climate-badge" style={{ borderColor: typeInfo.color, color: typeInfo.color }}>
            {typeInfo.climate}
          </div>
        </div>
        <div className="planet-status">
          {planet.owner_id || planet.owner_name || planet.name === 'New Earth' ? (
            <span className="status-owned">
              👤 {planet.owner_name || (planet.name === 'New Earth' ? 'Terran Federation' : 'Owned')}
            </span>
          ) : (
            <span className="status-unclaimed claimable">
              {onClaim ? '✋ Click to Claim' : '○ Unclaimed'}
            </span>
          )}
        </div>
      </div>

      {/* Planet Body */}
      <div className="planet-card-body">
        {/* Habitability */}
        {planet.habitability_score !== undefined && (
          <div className="planet-habitability">
            <span className="habitability-stat">
              <span className="habitability-icon">🌡️</span>
              <span className="habitability-value" style={{ color: habitability.color }}>
                {planet.habitability_score}%
              </span>
            </span>
          </div>
        )}

        {/* Population */}
        {planet.population !== undefined && planet.max_population !== undefined && (
          <div className="planet-population">
            <div className="population-stat">
              <span className="population-icon">👥</span>
              <span className="population-value">{formatPopulation(planet.population)}</span>
            </div>
          </div>
        )}

        {/* Planet Status */}
        {planet.status && planet.status !== 'normal' && (
          <div className="planet-status-info">
            <span className="status-icon">⚠️</span>
            <span className="status-text">{planet.status}</span>
          </div>
        )}
      </div>

      {/* In-fiction confirmation dialog (action proceeds only on confirm) */}
      {pendingConfirm && (
        <ConfirmDialog
          title={pendingConfirm.title}
          message={pendingConfirm.message}
          confirmLabel={pendingConfirm.confirmLabel}
          onConfirm={() => {
            setPendingConfirm(null);
            pendingConfirm.onConfirm();
          }}
          onCancel={() => setPendingConfirm(null)}
        />
      )}
    </div>
  );
};

export default PlanetCard;
