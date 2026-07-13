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
  /** True when the player's CURRENT position matches THIS planet specifically
   *  (not a sector-wide broadcast — see WO-UI2-WINDSHIELD-TABLEAU item 3). */
  isLanded?: boolean;
  /** True when the player's CURRENT position matches THIS station specifically. */
  isDocked?: boolean;
  /** True while autopilot is under burn (`autopilot.status === 'engaged'`) —
   *  mirrors the demo's row state machine (cockpit-redesign-v10 L1349-1352):
   *  here ? DOCK/LAND/HARVEST : (flying ? HALT : APPROACH). */
  flying?: boolean;
  /** Aborts the in-progress course — same autopilot.abort('all stop') the
   *  glass locrow's 🛑 ALL STOP chip already calls. */
  onHalt?: () => void;
  /** WO-UI2-FLIGHT-FEEL: fired (with the planet/station id) the moment an
   *  "APPROACH ▸" row is clicked, ALONGSIDE the existing confirm-dialog flow
   *  below (not instead of it) — requests the SAME windshield ship-glide a
   *  band-object click performs (GameDashboard wires this to the shared
   *  WindshieldFlightContext's `approach()`). Never fired for LAND/DOCK/
   *  CLAIM/HALT — only the "not here, not flying" APPROACH case. */
  onApproach?: (objectId: string) => void;
}

const PlanetPortPair: React.FC<PlanetPortPairProps> = ({
  planet,
  station,
  onLandOnPlanet,
  onClaimPlanet,
  onDockAtStation,
  isLanded = false,
  isDocked = false,
  flying = false,
  onHalt,
  onApproach
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
    if (!planet || isLanded || flying) return;
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
      // This IS the "🧭 APPROACH ▸" branch (isLanded/flying are already
      // guarded out above) — also kick off the windshield ship-glide, same
      // moment the confirm dialog opens (WO-UI2-FLIGHT-FEEL: previously this
      // click reached ONLY the confirm dialog, never the glide).
      onApproach?.(targetPlanet.id);
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
    if (!station || !onDockAtStation || isDocked || flying) return;
    // Capture narrowed values for the deferred onConfirm closure
    const targetStation = station;
    const dockAtStation = onDockAtStation;
    // Reachable only via the "🧭 APPROACH ▸" action (isDocked/flying already
    // guarded out above) — same glide kickoff as the planet APPROACH case.
    onApproach?.(targetStation.id);
    setPendingConfirm({
      title: 'Docking Request',
      message: `Dock at ${targetStation.name}?`,
      confirmLabel: 'Dock',
      onConfirm: () => dockAtStation(targetStation.id)
    });
  };

  const ownerDisplay = planet ? (planet.owner_name || (planet.owner_id ? 'Claimed' : null)) : null;

  const handleHalt = (e: React.MouseEvent) => {
    e.stopPropagation();
    onHalt?.();
  };

  // Row action label — mirrors the demo's monSys() state machine exactly
  // (cockpit-redesign-v10 L1349-1352): here ? DOCK/LAND ▸ : (flying ?
  // 🛑 HALT ▸ : APPROACH ▸). "here" (isLanded/isDocked) is a per-body id
  // match, not the old sector-wide broadcast.
  const planetAction: { label: string; onClick: (e: React.MouseEvent) => void; armed: boolean; ariaLabel: string } | null =
    !planet ? null : flying
      ? { label: '🛑 HALT ▸', onClick: handleHalt, armed: true, ariaLabel: 'Halt — abort autopilot and hold position' }
      : isLanded
        ? { label: '🛬 LAND ▸', onClick: (e) => { e.stopPropagation(); handlePlanetClick(); }, armed: false, ariaLabel: `Land on ${planet.name}` }
        : isPlanetUnclaimed && onClaimPlanet
          ? { label: '🚩 CLAIM ▸', onClick: (e) => { e.stopPropagation(); handlePlanetClick(); }, armed: false, ariaLabel: `Claim ${planet.name}` }
          : { label: '🧭 APPROACH ▸', onClick: (e) => { e.stopPropagation(); handlePlanetClick(); }, armed: false, ariaLabel: `Approach ${planet.name}` };

  const stationOperational = station?.status?.toLowerCase() === 'operational';
  const stationAction: { label: string; onClick: (e: React.MouseEvent) => void; armed: boolean; ariaLabel: string } | null =
    !station || !onDockAtStation ? null : flying
      ? { label: '🛑 HALT ▸', onClick: handleHalt, armed: true, ariaLabel: 'Halt — abort autopilot and hold position' }
      : !stationOperational
        ? null
        : isDocked
          ? { label: '⚓ DOCK ▸', onClick: handleStationClick, armed: false, ariaLabel: `Dock at ${station.name}` }
          : { label: '🧭 APPROACH ▸', onClick: handleStationClick, armed: false, ariaLabel: `Approach ${station.name}` };

  // Dense one-line qualifiers (WO-UI-MAX-BATCH-1 item 7: "EVERY object = ONE
  // DENSE .row line ... secondary stats go DIM-INLINE after the name ...
  // NOT stacked multi-line — the tall claimable-planet card → one dense
  // line"). Ownership/hub/unclaimed are mutually exclusive (same precedence
  // the old stacked `.planet-meta` block already used); `planet.status` is
  // the real backend PlanetStatus value (HABITABLE/UNINHABITABLE/COLONIZED/
  // DEVELOPED/TERRAFORMING — models/planet.py), not a guessed threshold.
  const planetQualifiers: Array<{ key: string; text: string; cls: string }> = [];
  if (planet) {
    if (isPopulationHub) {
      planetQualifiers.push({ key: 'hub', text: 'POPULATION HUB', cls: 'pq-hub' });
    } else if (isPlanetUnclaimed && onClaimPlanet) {
      planetQualifiers.push({ key: 'unclaimed', text: 'UNCLAIMED', cls: 'pq-unclaimed' });
    } else if (ownerDisplay) {
      planetQualifiers.push({ key: 'owner', text: ownerDisplay.toUpperCase(), cls: 'pq-owner' });
    }
    if (planet.status) {
      planetQualifiers.push({ key: 'status', text: planet.status.toUpperCase(), cls: 'pq-status' });
    }
    if (isPlanetUnclaimed && onClaimPlanet) {
      planetQualifiers.push({ key: 'reqs', text: '💰10,000cr · 👥100+', cls: 'pq-reqs' });
    }
    if (planet.habitability_score !== undefined) {
      planetQualifiers.push({ key: 'temp', text: `🌡️${planet.habitability_score}%`, cls: 'pq-stat' });
    }
    if (planet.population !== undefined) {
      planetQualifiers.push({ key: 'pop', text: `👥${formatPopulation(planet.population)}`, cls: 'pq-stat' });
    }
  }

  const stationQualifiers: Array<{ key: string; text: string; cls: string }> = [];
  if (station) {
    if (stationOwnerDisplay) {
      stationQualifiers.push({ key: 'owner', text: stationOwnerDisplay.toUpperCase(), cls: 'pq-owner' });
    }
    stationQualifiers.push({
      key: 'class',
      text: stationClassInfo
        ? `CLASS ${stationClassInfo.classNumber} · ${stationClassInfo.name.toUpperCase()}`
        : station.port_class !== undefined
          ? `CLASS ${station.port_class}`
          : station.type.replace(/_/g, ' ').toUpperCase(),
      cls: 'pq-status',
    });
  }

  return (
    <div className="planet-port-pair">
      {/* Planet Section - Clickable (only show if planet exists). One dense
          line: icon + name + inline dim qualifiers on the left, the row
          action on the right (`justify-content:space-between`, planet-port-
          pair.css) — replaces the old stacked icon/name + meta-badges +
          stats layout. */}
      {planet && (
        <div
          className={`planet-section ${flying ? 'inactive' : !isLanded ? 'clickable' : 'landed'} ${isPlanetUnclaimed ? 'unclaimed' : ''}`}
          aria-disabled={flying}
          onClick={flying ? undefined : handlePlanetClick}
        >
          <div className="planet-info">
            <span className="planet-icon">{planetIcon}</span>
            <span className="planet-name">{planet.name}</span>
            {planetQualifiers.length > 0 && (
              <span className="planet-quals">
                {planetQualifiers.map((q) => (
                  <span key={q.key} className={q.cls}>{q.text}</span>
                ))}
              </span>
            )}
          </div>
          {/* Row action (WO-UI2-WINDSHIELD-TABLEAU item 3, demo L1350) —
              here?LAND/CLAIM:(flying?HALT:APPROACH). Reuses the shared
              `.act`/`.act.armed` idiom (cockpit-shell.css) already used by
              the SENSOR SWEEP + hazard-analysis rows on this same monitor. */}
          {planetAction && (
            <button
              type="button"
              className={`act${planetAction.armed ? ' armed' : ''}`}
              onClick={planetAction.onClick}
              aria-label={planetAction.ariaLabel}
              title={planetAction.ariaLabel}
            >
              {planetAction.label}
            </button>
          )}
        </div>
      )}

      {/* Station Section - Clickable if exists. Same dense one-line grammar
          as the planet section above — no more `.orbital-connector` arrow
          pairing them side by side (WO-UI-MAX-BATCH-1 item 7: every object
          is its own full-width row, matching the target screenshot's flat
          object list, not a paired orbit card). */}
      {station && (
        <div
          className={`station-section ${!isDocked && !flying && stationOperational ? 'clickable' : 'inactive'}`}
          aria-disabled={flying}
          onClick={flying ? undefined : handleStationClick}
        >
          <div className="station-info">
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
            <span className="station-status">{stationOperational ? '🟢' : '🔴'}</span>
            {stationQualifiers.length > 0 && (
              <span className="planet-quals" title={stationClassInfo?.blurb}>
                {stationQualifiers.map((q) => (
                  <span key={q.key} className={q.cls}>{q.text}</span>
                ))}
              </span>
            )}
          </div>
          {/* Row action (WO-UI2-WINDSHIELD-TABLEAU item 3, demo L1349) —
              here?DOCK:(flying?HALT:APPROACH). null while non-operational
              AND not flying (nothing to approach/halt at a dead station). */}
          {stationAction && (
            <button
              type="button"
              className={`act${stationAction.armed ? ' armed' : ''}`}
              onClick={stationAction.onClick}
              aria-label={stationAction.ariaLabel}
              title={stationAction.ariaLabel}
            >
              {stationAction.label}
            </button>
          )}
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
