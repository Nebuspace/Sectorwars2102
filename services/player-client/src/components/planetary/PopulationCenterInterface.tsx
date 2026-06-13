import React, { useState } from 'react';
import type { Planet } from '../../contexts/GameContext';
import PioneerOfficeVenue from './PioneerOfficeVenue';
import './population-center.css';

// =====================================================================
// Population Center — the landed UI for a capital population hub
// (the TERRA Capital-welcome planet, e.g. New Earth in the Capital Sector).
//
// Replaces the generic owned-colony console when landed on a hub. Canon:
// FEATURES/planets/colonization.md — the Capital Sector is the welcome hub
// where new arrivals dock and the Pioneer Office brokers colonist
// migration contracts. (Canon nominally sites the Office at the Capital's
// Class-0 station; this surfaces it on the hub planet by design — the
// station colonist buy flow is left intact.)
//
// Venue pattern mirrors SpaceDockInterface: a hub view with service cards
// that switch to focused venues. Renders only real data — no mock state.
// =====================================================================

type VenueType = 'hub' | 'pioneer';

const formatPopulation = (pop: number): string => {
  if (pop >= 1_000_000_000) return `${(pop / 1_000_000_000).toFixed(1)}B`;
  if (pop >= 1_000_000) return `${(pop / 1_000_000).toFixed(1)}M`;
  if (pop >= 1_000) return `${(pop / 1_000).toFixed(1)}K`;
  return `${pop}`;
};

interface Props {
  planet: Planet;
}

const PopulationCenterInterface: React.FC<Props> = ({ planet }) => {
  const [venue, setVenue] = useState<VenueType>('hub');

  const habitability = Math.max(0, Math.min(100, planet?.habitability_score ?? 0));

  return (
    <div className="console-monitor population-center-monitor full-width">
      <div className="monitor-bezel">
        <div className="bezel-corner tl"></div>
        <div className="bezel-corner tr"></div>
        <div className="bezel-corner bl"></div>
        <div className="bezel-corner br"></div>
      </div>
      <div className="monitor-screen">
        <div className="screen-hud-header">
          CAPITAL SECTOR · POPULATION HUB
        </div>
        <div className="screen-hud-content population-center-content">
          {venue === 'hub' ? (
            <div className="pc-hub">
              <div className="pc-welcome">
                <div className="pc-welcome-title">{planet.name}</div>
                <div className="pc-welcome-sub">
                  Welcome to the Capital Sector — seat of the regional Migration Authority.
                </div>
                <div className="pc-badges">
                  <span className="pc-badge pc-badge-admin">REGIONAL ADMINISTRATION</span>
                  <span className="pc-badge pc-badge-safe">OPERATOR-MANAGED · NON-DESTRUCTIBLE</span>
                </div>
              </div>

              <div className="pc-stat-grid">
                <div className="pc-stat">
                  <span className="pc-stat-label">POPULATION</span>
                  <span className="pc-stat-value">{formatPopulation(planet.population ?? 0)}</span>
                </div>
                <div className="pc-stat">
                  <span className="pc-stat-label">HABITABILITY</span>
                  <span className="pc-stat-value">{habitability}%</span>
                </div>
                <div className="pc-stat">
                  <span className="pc-stat-label">CAPACITY</span>
                  <span className="pc-stat-value">∞</span>
                </div>
              </div>

              <div className="pc-venues">
                <button
                  className="pc-venue-card"
                  type="button"
                  onClick={() => setVenue('pioneer')}
                >
                  <span className="pc-venue-icon">🛰️</span>
                  <span className="pc-venue-name">PIONEER OFFICE</span>
                  <span className="pc-venue-desc">
                    Broker migration contracts and load cryosleep transit pods.
                  </span>
                  <span className="pc-venue-tags">
                    Migration Contracts · Cohort Loading · Registry
                  </span>
                </button>
              </div>
            </div>
          ) : (
            <PioneerOfficeVenue planet={planet} onBack={() => setVenue('hub')} />
          )}
        </div>
      </div>
    </div>
  );
};

export default PopulationCenterInterface;
