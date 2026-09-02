import React, { useEffect, useState } from 'react';
import { gameAPI } from '../../services/api';
import type { Planet } from '../../types/planetary';
import EmptyState from '../common/EmptyState';

const getHabitabilityScore = (planet: Planet): number | null => {
  const score = planet.habitability?.score;
  return typeof score === 'number' ? Math.max(0, Math.min(100, score)) : null;
};

const formatTerraformingReadout = (planet: Planet): string | null => {
  const tf = planet.terraforming;
  if (!tf || tf.active !== true) return null;
  const target = typeof tf.target === 'number' ? tf.target : null;
  const progress = typeof tf.progress === 'number' ? tf.progress : null;
  if (target === null && progress === null) return 'Terraforming';
  const targetPart = target !== null ? `→ ${target}%` : '→ —';
  const progressPart = progress !== null ? ` (${progress.toFixed(0)}%)` : '';
  return `Terraform ${targetPart}${progressPart}`;
};

const COLONIES_ROSTER_LOAD_FALLBACK = 'Failed to load colonies';

/** Transport collapse copy is not gameserver detail (LEG-3282 densify). */
const isNetworkCollapseMessage = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed)
  );
};

/** Exported for TypeError/network honesty Vitest (LEG-3103 / LEG-3282). */
export function formatColoniesRosterLoadError(err: unknown, fallback: string): string {
  if (err instanceof TypeError) return fallback;
  if (err instanceof Error && err.message) {
    if (isNetworkCollapseMessage(err.message)) return fallback;
    return err.message;
  }
  return fallback;
}

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
      .catch((err) => {
        if (cancelled) return;
        setError(formatColoniesRosterLoadError(err, COLONIES_ROSTER_LOAD_FALLBACK));
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
        {planets.map((p) => {
          const hab = getHabitabilityScore(p);
          const terraform = formatTerraformingReadout(p);
          return (
          <li key={p.id} className="sb-colonies-row">
            <span className="sb-colonies-name">{p.name}</span>
            <span className="sb-colonies-sector">{p.sectorName}</span>
            <span className="sb-colonies-pop">
              {(p.colonists ?? 0).toLocaleString()} / {(p.maxColonists ?? 0).toLocaleString()}
            </span>
            {hab !== null && (
              <span className="sb-colonies-hab" title="Habitability score">
                {hab}%
              </span>
            )}
            {terraform && (
              <span className="sb-colonies-terraform" title="Active terraforming project">
                {terraform}
              </span>
            )}
            {p.underSiege && <span className="sb-colonies-siege">UNDER SIEGE</span>}
          </li>
          );
        })}
      </ul>
      <p className="sb-colonies-footer">Travel there to manage.</p>
    </div>
  );
};

export default ColoniesRosterTab;
