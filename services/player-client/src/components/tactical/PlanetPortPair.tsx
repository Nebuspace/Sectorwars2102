import React, { useState } from 'react';
import { getStationClassInfo, StationClassMark } from '../common/stationIdentity';
import ConfirmDialog, { type PendingConfirm } from './ConfirmDialog';
import './planet-port-pair.css';

interface Planet {
  id: string;
  name: string;
  type: string;
  status: string;
  sector_id: number;
  owner_id?: string | null;
  owner_name?: string | null;
  population?: number;
  max_population?: number;
  habitability_score?: number;
  is_population_hub?: boolean;
}

interface Station {
  id: string;
  name: string;
  port_class?: number;  // Station class 0-11 (from specification)
  station_class?: string | number;  // Newer backend field; same 0-11 classes
  is_spacedock?: boolean;
  type: string;
  status: string;
  owner_id?: string | null;
  owner_name?: string | null;
  faction_affiliation?: string | null;
  services?: {
    fuel?: boolean;
    repairs?: boolean;
    trading?: boolean;
    shipyard?: boolean;
    equipment?: boolean;
    information?: boolean;
  };
}

interface PlanetPortPairProps {
  planet: Planet | null;
  station?: Station | null;
  onLandOnPlanet: (planetId: string) => void;
  onClaimPlanet?: (planetId: string) => void;
  onDockAtStation?: (stationId: string) => void;
  isLanded?: boolean;
  isDocked?: boolean;
}

const PlanetPortPair: React.FC<PlanetPortPairProps> = ({
  planet,
  station,
  onLandOnPlanet,
  onClaimPlanet,
  onDockAtStation,
  isLanded = false,
  isDocked = false
}) => {
  // Planet type icons
  const planetTypeIcons: { [key: string]: string } = {
    'terran': '🌍',
    'ice': '🧊',
    'volcanic': '🌋',
    'gas_giant': '🪐',
    'barren': '🌑',
    'oceanic': '🌊',
    'desert': '🏜️',
    'jungle': '🌴'
  };

  // Station class identity (shared stationIdentity module); null when unknown
  const stationClassInfo = station
    ? getStationClassInfo(station.station_class ?? station.port_class)
    : null;

  // Format population
  const formatPopulation = (pop: number | undefined) => {
    if (!pop) return '0';
    if (pop >= 1000000000) return `${(pop / 1000000000).toFixed(1)}B`;
    if (pop >= 1000000) return `${(pop / 1000000).toFixed(1)}M`;
    if (pop >= 1000) return `${(pop / 1000).toFixed(1)}K`;
    return pop.toString();
  };

  const planetIcon = planet ? (planetTypeIcons[planet.type?.toLowerCase()] || '🌍') : null;

  // Get station owner display name
  const stationOwnerDisplay = station?.owner_name || (station?.faction_affiliation ? `${station.faction_affiliation} Faction` : null);

  // Determine if planet is unclaimed — the server now protects population
  // hubs itself, so a generic owner check is all the client needs
  // Capital population hubs are public worlds — the server's claim endpoint
  // refuses them, so the card must not dangle a "Click to Claim" it can't
  // honor. Belt-and-braces population check mirrors the server guard.
  const isPopulationHub = Boolean(
    planet && (planet.is_population_hub || (planet.population ?? 0) >= 1_000_000)
  );
  const isPlanetUnclaimed =
    planet && !planet.owner_id && !planet.owner_name && !isPopulationHub;

  // In-fiction confirmation dialog state (replaces native confirm())
  const [pendingConfirm, setPendingConfirm] = useState<PendingConfirm | null>(null);

  const handlePlanetClick = () => {
    if (!planet || isLanded) return;
    // Capture narrowed values for the deferred onConfirm closure
    const targetPlanet = planet;

    if (isPlanetUnclaimed) {
      // Unclaimed, claimable planet — claim it (claiming auto-lands).
      if (!onClaimPlanet) return;
      const claimPlanet = onClaimPlanet;
      setPendingConfirm({
        title: 'Claim Planet',
        message: `Claim ${targetPlanet.name}?\n\nRequirements: 10,000 credits and at least 100 colonists aboard.\nClaiming makes you the owner and automatically lands your ship.`,
        confirmLabel: 'Claim',
        onConfirm: () => claimPlanet(targetPlanet.id)
      });
    } else {
      // Owned planet OR a public population hub (e.g. New Earth) — both are
      // landable (you land on a hub to recruit colonists); hubs simply can't be
      // claimed. Route straight to the Land confirm, same as the helm-rail
      // LAND button (onLandOnPlanet -> handleLand -> landOnPlanet).
      setPendingConfirm({
        title: 'Landing Request',
        message: `Land on ${targetPlanet.name}?`,
        confirmLabel: 'Land',
        onConfirm: () => onLandOnPlanet(targetPlanet.id)
      });
    }
  };

  const handleStationClick = (e: React.MouseEvent) => {
    e.stopPropagation(); // Prevent planet click
    if (!station || !onDockAtStation || isDocked) return;
    // Capture narrowed values for the deferred onConfirm closure
    const targetStation = station;
    const dockAtStation = onDockAtStation;
    setPendingConfirm({
      title: 'Docking Request',
      message: `Dock at ${targetStation.name}?`,
      confirmLabel: 'Dock',
      onConfirm: () => dockAtStation(targetStation.id)
    });
  };

  const ownerDisplay = planet ? (planet.owner_name || (planet.owner_id ? 'Claimed' : null)) : null;

  return (
    <div className="planet-port-pair">
      {/* Planet Section - Clickable (only show if planet exists) */}
      {planet && (
        <div
          className={`planet-section ${!isLanded ? 'clickable' : 'landed'} ${isPlanetUnclaimed ? 'unclaimed' : ''}`}
          onClick={handlePlanetClick}
        >
          <div className="planet-details">
            {/* icon + name always on ONE line — icon scaled to line-height */}
            <div className="planet-name-line">
              <span className="planet-icon">{planetIcon}</span>
              <span className="planet-name">{planet.name}</span>
            </div>
            {/* badges: claim/hub/owner — rendered only when present */}
            {((isPlanetUnclaimed && !!onClaimPlanet) || isPopulationHub || !!ownerDisplay) && (
              <div className="planet-meta">
                {isPlanetUnclaimed && onClaimPlanet ? (
                  <>
                    <span className="planet-claim-hint">Click to Claim</span>
                    <span className="planet-claim-reqs">💰 10,000cr · 👥 100+ colonists aboard</span>
                  </>
                ) : isPopulationHub ? (
                  <span className="planet-hub-tag">POPULATION HUB · REGIONAL ADMINISTRATION</span>
                ) : (
                  ownerDisplay && <span className="planet-owner">{ownerDisplay}</span>
                )}
              </div>
            )}
            <div className="planet-stats">
              {planet.habitability_score !== undefined && (
                <span className="stat">🌡️ {planet.habitability_score}%</span>
              )}
              {planet.population !== undefined && (
                <span className="stat">👥 {formatPopulation(planet.population)}</span>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Orbital Connector - only show if both planet and station exist */}
      {planet && station && <div className="orbital-connector">→</div>}

      {/* Station Section - Clickable if exists */}
      {station && (
        <div
          className={`station-section ${!isDocked && station.status.toLowerCase() === 'operational' ? 'clickable' : 'inactive'}`}
          onClick={handleStationClick}
        >
          <div className="station-details">
            <div className="station-name-line">
              {/* icon + name + status on ONE line */}
              <div className="station-name-status">
                <span
                  className="station-icon"
                  style={stationClassInfo ? { color: stationClassInfo.accent } : undefined}
                >
                  {stationClassInfo ? (
                    <StationClassMark group={stationClassInfo.group} size={16} />
                  ) : (
                    '🛰️'
                  )}
                </span>
                <span className="station-name">{station.name}</span>
                <span className="station-status">
                  {station.status.toLowerCase() === 'operational' ? '🟢' : '🔴'}
                </span>
              </div>
              {stationOwnerDisplay && <span className="station-owner">{stationOwnerDisplay}</span>}
              {stationClassInfo ? (
                <span
                  className="station-class"
                  style={{ color: stationClassInfo.accent }}
                  title={stationClassInfo.blurb}
                >
                  Class {stationClassInfo.classNumber} · {stationClassInfo.name}
                </span>
              ) : (
                station.port_class !== undefined && (
                  <span className="station-class">Class {station.port_class}</span>
                )
              )}
              <span className="station-type">{station.type.replace(/_/g, ' ')}</span>
            </div>
          </div>
        </div>
      )}

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

export default PlanetPortPair;
