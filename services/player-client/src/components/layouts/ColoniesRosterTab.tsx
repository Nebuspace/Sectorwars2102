import React, { useEffect, useState } from 'react';
import { gameAPI } from '../../services/api';
import type { Planet } from '../../types/planetary';
import EmptyState from '../common/EmptyState';

/**
 * ColoniesRosterTab — the StatusBar dossier dropdown's "Colonies" tab
 * (WO-UI0-STATUSBAR sub-part a, Accept #5). Per the ratified cockpit-redesign
 * brief: "Colonies (read-only roster — 'travel there to manage')" — this is
 * intentionally NOT PlanetManager (components/planetary/), which is the full
 * management console (allocations, defenses, siege detail, genesis-forming
 * state) and far too heavy to embed in a fixed-size dropdown. No existing
 * component renders just a compact roster, so this is new — but it reuses
 * the SAME data source PlanetManager already calls
 * (gameAPI.planetary.getOwnedPlanets → GET /api/v1/planets/owned) rather than
 * inventing a new endpoint or duplicating any allocation/siege logic.
 */
const ColoniesRosterTab: React.FC = () => {
  const [planets, setPlanets] = useState<Planet[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    gameAPI.planetary
      .getOwnedPlanets()
      .then((response: any) => {
        if (cancelled) return;
        setPlanets(response?.planets || []);
      })
      .catch(() => {
        if (cancelled) return;
        setError('Failed to load colonies');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return <div className="sb-colonies-error">{error}</div>;
  }

  if (planets === null) {
    return <div className="sb-colonies-loading">Loading…</div>;
  }

  if (planets.length === 0) {
    return (
      <EmptyState
        icon="🌌"
        title="No Colonies"
        message="You don't own any planets yet. Deploy a Genesis Device from your ship to found your first colony."
      />
    );
  }

  return (
    <div className="sb-colonies-roster">
      <ul className="sb-colonies-list">
        {planets.map((p) => (
          <li key={p.id} className="sb-colonies-row">
            <span className="sb-colonies-name">{p.name}</span>
            <span className="sb-colonies-sector">{p.sectorName}</span>
            <span className="sb-colonies-pop">
              {(p.colonists ?? 0).toLocaleString()} / {(p.maxColonists ?? 0).toLocaleString()}
            </span>
            {p.underSiege && <span className="sb-colonies-siege">UNDER SIEGE</span>}
          </li>
        ))}
      </ul>
      <p className="sb-colonies-footer">Travel there to manage.</p>
    </div>
  );
};

export default ColoniesRosterTab;
