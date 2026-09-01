import React, { useEffect, useMemo, useState } from 'react';
import { gameAPI } from '../../services/api';
import type { Planet } from '../../types/planetary';
import { resourceIcon } from '../../services/resourceCatalog';
import EmptyState from '../common/EmptyState';

/** Efficiency = % of colonists assigned to production (matches PlanetManager). */
const getEfficiency = (planet: Planet): number => {
  const colonists = planet.colonists ?? 0;
  if (colonists <= 0) return 0;
  const unused = planet.allocations?.unused ?? 0;
  return Math.max(0, Math.min(100, Math.round((100 * (colonists - unused)) / colonists)));
};

const formatNumber = (num: number): string => {
  if (num >= 1_000_000) return `${(num / 1_000_000).toFixed(1)}M`;
  if (num >= 1_000) return `${(num / 1_000).toFixed(1)}K`;
  return Math.round(num).toString();
};

const rate = (planet: Planet, key: 'fuel' | 'organics' | 'equipment'): number =>
  planet.productionRates?.[key] ?? 0;

const EMPIRE_PRODUCTION_LOAD_FALLBACK = 'Failed to load production data';

/** Transport collapse copy is not gameserver detail (LEG-3283 densify). */
const isNetworkCollapseMessage = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed)
  );
};

/** Exported for TypeError/network honesty Vitest (LEG-3173 / LEG-3283). */
export function formatEmpireProductionLoadError(err: unknown, fallback: string): string {
  if (err instanceof TypeError) return fallback;
  if (err instanceof Error && err.message) {
    if (isNetworkCollapseMessage(err.message)) return fallback;
    return err.message;
  }
  return fallback;
}

/**
 * EmpireProductionDashboard — read-only empire-wide production summary for
 * the StatusBar dossier (LEG-516 / LEG-DEC-230). Distinct from
 * ColoniesRosterTab (roster-only) and PlanetManager (full management console).
 * Reuses GET /api/v1/planets/owned — no new endpoints.
 */
const EmpireProductionDashboard: React.FC = () => {
  const [planets, setPlanets] = useState<Planet[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    gameAPI.planetary
      .getOwnedPlanets()
      .then((response: { planets?: Planet[] }) => {
        if (cancelled) return;
        setPlanets(response?.planets ?? []);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(formatEmpireProductionLoadError(err, EMPIRE_PRODUCTION_LOAD_FALLBACK));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const totals = useMemo(() => {
    if (!planets) return null;
    return planets.reduce(
      (acc, p) => {
        acc.fuel += rate(p, 'fuel');
        acc.organics += rate(p, 'organics');
        acc.equipment += rate(p, 'equipment');
        acc.population += p.colonists ?? 0;
        acc.efficiency += getEfficiency(p);
        return acc;
      },
      { fuel: 0, organics: 0, equipment: 0, population: 0, efficiency: 0 },
    );
  }, [planets]);

  if (error) {
    return <div className="sb-production-error">{error}</div>;
  }

  if (planets === null) {
    return <div className="sb-production-loading">Loading…</div>;
  }

  if (planets.length === 0) {
    return (
      <EmptyState
        icon="📊"
        title="No Production Data"
        message="You don't own any colonies yet. Found a planet to see empire-wide production totals here."
      />
    );
  }

  const avgEfficiency =
    planets.length > 0 && totals ? Math.round(totals.efficiency / planets.length) : 0;
  const siegedCount = planets.filter((p) => p.underSiege).length;

  return (
    <div className="sb-production-dashboard" data-testid="empire-production-dashboard">
      <div className="sb-production-totals" data-testid="empire-production-totals">
        <span className="sb-production-total" title="Total fuel production per day">
          {resourceIcon('fuel')} {formatNumber(totals!.fuel)}/day
        </span>
        <span className="sb-production-total" title="Total organics production per day">
          {resourceIcon('organics')} {formatNumber(totals!.organics)}/day
        </span>
        <span className="sb-production-total" title="Total equipment production per day">
          {resourceIcon('equipment')} {formatNumber(totals!.equipment)}/day
        </span>
        <span className="sb-production-total" title="Total population">
          🌐 {formatNumber(totals!.population)}
        </span>
        <span className="sb-production-total" title="Average allocation efficiency">
          ⚡ {avgEfficiency}%
        </span>
        {siegedCount > 0 && (
          <span className="sb-production-total sb-production-siege" title="Colonies under siege">
            ⚠ {siegedCount} under siege
          </span>
        )}
      </div>

      <div
        className="sb-production-table"
        role="table"
        aria-label="Empire production by colony"
      >
        <div className="sb-production-row sb-production-head" role="row">
          <span className="sb-production-cell name" role="columnheader">Colony</span>
          <span className="sb-production-cell sector" role="columnheader">Sector</span>
          <span className="sb-production-cell num" role="columnheader">
            {resourceIcon('fuel')}
          </span>
          <span className="sb-production-cell num" role="columnheader">
            {resourceIcon('organics')}
          </span>
          <span className="sb-production-cell num" role="columnheader">
            {resourceIcon('equipment')}
          </span>
          <span className="sb-production-cell num" role="columnheader">Eff</span>
        </div>
        {planets.map((p) => (
          <div
            key={p.id}
            className="sb-production-row"
            role="row"
            data-testid={`empire-production-row-${p.id}`}
          >
            <span className="sb-production-cell name" role="cell">
              {p.name}
              {p.underSiege && (
                <span className="sb-production-siege-badge" aria-label="Under siege">
                  {' '}
                  ⚠
                </span>
              )}
            </span>
            <span className="sb-production-cell sector" role="cell">
              {p.sectorName}
            </span>
            <span className="sb-production-cell num" role="cell">
              {formatNumber(rate(p, 'fuel'))}
            </span>
            <span className="sb-production-cell num" role="cell">
              {formatNumber(rate(p, 'organics'))}
            </span>
            <span className="sb-production-cell num" role="cell">
              {formatNumber(rate(p, 'equipment'))}
            </span>
            <span className="sb-production-cell num" role="cell">
              {getEfficiency(p)}%
            </span>
          </div>
        ))}
      </div>
      <p className="sb-production-footer">Read-only summary · land to adjust allocations.</p>
    </div>
  );
};

export default EmpireProductionDashboard;
