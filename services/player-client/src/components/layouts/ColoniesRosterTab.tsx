import React, { useEffect, useState } from 'react';
import { gameAPI } from '../../services/api';
import type { Planet } from '../../types/planetary';
import EmptyState from '../common/EmptyState';

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

function httpStatus(err: unknown): number | undefined {
  if (err && typeof err === 'object') {
    const direct = (err as { status?: number }).status;
    if (typeof direct === 'number') return direct;
    const resp = (err as { response?: { status?: number } }).response;
    if (typeof resp?.status === 'number') return resp.status;
  }
  return undefined;
}

/** Exported for TypeError/network honesty Vitest (LEG-3103 / LEG-3282). */
export function formatColoniesRosterLoadError(err: unknown, fallback: string): string {
  const status = httpStatus(err);
  const message = err instanceof Error ? err.message : undefined;
  const hasServerDetail =
    !(err instanceof TypeError) &&
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim()) &&
    !isNetworkCollapseMessage(message);

  if (status === 403) {
    if (hasServerDetail) return message!;
    return 'You do not have permission to view your colonies roster.';
  }

  if (status === 429) {
    return 'Colonies roster rate limit exceeded — wait a moment and try again.';
  }

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
