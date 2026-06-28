import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { gridAPI } from '../../services/api';
import './grid-manager.css';

/**
 * GridManager — CRT-2 player-facing management of the authoritative citadel
 * grid (CRT-1 made derive_citadel_level authoritative + size-gated). Wires the
 * existing pure grid primitives (structures.can_place_gated / place /
 * decommission_with_refund + building_catalog) into the player UI via three
 * endpoints owned by the gameserver:
 *
 *   GET  /api/v1/planets/{id}/grid               → the grid view
 *   POST /api/v1/planets/{id}/grid/place         → { kind, x, y, level }
 *   POST /api/v1/planets/{id}/grid/decommission  → { building_id }
 *
 * The payload shape is the gameserver's to define; we read every field
 * defensively (snake_case primary with camelCase fallbacks) so a slightly
 * different contract degrades gracefully rather than crashing the cockpit.
 *
 * Scroll-law: the grid IS the primary action — it renders first, full-width,
 * above the (collapsible) catalog and the placed-building list.
 */

// ----- Defensive view types (mirrors structures.py / building_catalog.py) -----

interface GridPlot {
  x: number;
  y: number;
  cleared?: boolean;
  surveyed?: boolean;
  hazard?: { kind?: string; sev?: number } | null;
  building_id?: string | null;
  buildingId?: string | null;
  terrain?: string;
}

interface GridBuilding {
  id: string;
  kind: string;
  name?: string;
  domain?: string;
  x: number;
  y: number;
  level: number;
  footprint?: [number, number];
  /** complete_at === null means operational; a timestamp means still building. */
  complete_at?: string | null;
  completeAt?: string | null;
  condition?: number;
}

/** A placeable building kind from the static catalog (building_catalog.get). */
interface CatalogEntry {
  kind: string;
  name?: string;
  domain?: string;
  footprint?: [number, number];
  max_level?: number;
  maxLevel?: number;
  min_citadel_level?: number;
  minCitadelLevel?: number;
  tech_gate?: string | null;
  techGate?: string | null;
  /** cost = { "<level>": { credits, <material>: n } } */
  cost?: Record<string, Record<string, number>>;
  /** Optional cost summary the server may precompute for level 1. */
  cost_credits?: number;
  costCredits?: number;
}

interface GridView {
  success?: boolean;
  planet_id?: string;
  planet_name?: string;
  grid?: { cols?: number; rows?: number };
  cols?: number;
  rows?: number;
  plots?: GridPlot[];
  buildings?: GridBuilding[];
  citadel_level?: number;
  citadelLevel?: number;
  max_citadel_level?: number;
  maxCitadelLevel?: number;
  /** Placeable building catalog (kind → spec) OR a list of specs. */
  catalog?: CatalogEntry[] | Record<string, CatalogEntry>;
  /** The owning player's unlocked research-node set. */
  researched?: string[];
  unlocked?: string[];
}

interface GridManagerProps {
  planetId: string;
  playerCredits: number;
  onUpdate?: () => void;
}

// ----- Helpers -----

const DOMAIN_ICON: Record<string, string> = {
  economy: '⚙️',
  civic: '🏛️',
  defense: '🛡️',
  terraform: '🌱',
  monument: '🏆',
};

const DOMAIN_LABEL: Record<string, string> = {
  economy: 'Economy',
  civic: 'Civic',
  defense: 'Defense',
  terraform: 'Terraform',
  monument: 'Monument',
};

const compact = (n: number): string => {
  if (n >= 1_000_000) return `${n % 1_000_000 === 0 ? n / 1_000_000 : (n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${n % 1_000 === 0 ? n / 1_000 : (n / 1_000).toFixed(1)}k`;
  return `${n}`;
};

/** Footprint [w,h] for a catalog entry, defaulting to a single cell. */
const footprintOf = (entry: CatalogEntry): [number, number] => {
  const fp = entry.footprint;
  return Array.isArray(fp) && fp.length === 2 ? [Number(fp[0]) || 1, Number(fp[1]) || 1] : [1, 1];
};

/** Level-1 credit cost for a catalog entry (server summary first, else cost[1]). */
const level1Credits = (entry: CatalogEntry): number => {
  const summary = entry.cost_credits ?? entry.costCredits;
  if (typeof summary === 'number') return summary;
  const lvl1 = entry.cost?.['1'];
  return lvl1 && typeof lvl1.credits === 'number' ? lvl1.credits : 0;
};

/** Per-planet material costs (non-credit keys) for level 1, for display. */
const level1Materials = (entry: CatalogEntry): Array<[string, number]> => {
  const lvl1 = entry.cost?.['1'];
  if (!lvl1) return [];
  return Object.entries(lvl1).filter(([k]) => k !== 'credits') as Array<[string, number]>;
};

const MATERIAL_ICON: Record<string, string> = {
  fuel_ore: '⛽',
  organics: '🌿',
  equipment: '⚙️',
};

const matLabel = (k: string): string =>
  `${MATERIAL_ICON[k] || ''} ${k.replace(/_/g, ' ')}`.trim();

const techGateOf = (entry: CatalogEntry): string | null =>
  entry.tech_gate ?? entry.techGate ?? null;

const buildingComplete = (b: GridBuilding): string | null | undefined =>
  b.complete_at ?? b.completeAt;

const GridManager: React.FC<GridManagerProps> = ({ planetId, playerCredits, onUpdate }) => {
  const [view, setView] = useState<GridView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [actionMessage, setActionMessage] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null);

  // PLACE flow (plot-first): click an empty plot → open the catalog popup
  // targeting THAT plot → pick a kind → place it there → close.
  const [popupPlot, setPopupPlot] = useState<{ x: number; y: number } | null>(null);
  // DECOMMISSION flow: a selected placed building (id).
  const [selectedBuildingId, setSelectedBuildingId] = useState<string | null>(null);

  const fetchGrid = useCallback(async () => {
    try {
      setLoading(true);
      const data = await gridAPI.getGrid(planetId);
      setView(data);
      setError(null);
    } catch (err: any) {
      setError(err?.message || 'Failed to load planet grid');
    } finally {
      setLoading(false);
    }
  }, [planetId]);

  useEffect(() => {
    fetchGrid();
  }, [fetchGrid]);

  // ESC closes the build popup (placing nothing).
  useEffect(() => {
    if (!popupPlot) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setPopupPlot(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [popupPlot]);

  // ----- Normalize the (defensively-read) view into stable locals -----

  const cols = view?.grid?.cols ?? view?.cols ?? 0;
  const rows = view?.grid?.rows ?? view?.rows ?? 0;
  const plots = useMemo<GridPlot[]>(() => view?.plots ?? [], [view]);
  const buildings = useMemo<GridBuilding[]>(() => view?.buildings ?? [], [view]);
  const citadelLevel = view?.citadel_level ?? view?.citadelLevel ?? 0;
  const maxCitadelLevel = view?.max_citadel_level ?? view?.maxCitadelLevel ?? 5;

  const researched = useMemo<Set<string>>(
    () => new Set(view?.researched ?? view?.unlocked ?? []),
    [view],
  );

  /** The placeable catalog, normalized to a list keyed by `kind`. */
  const catalog = useMemo<CatalogEntry[]>(() => {
    const raw = view?.catalog;
    if (!raw) return [];
    const list = Array.isArray(raw) ? raw : Object.values(raw);
    return list.filter((e): e is CatalogEntry => !!e && typeof e.kind === 'string');
  }, [view]);

  // Plot lookup: (x,y) → plot; and building lookup: id → building.
  const plotAt = useMemo(() => {
    const m = new Map<string, GridPlot>();
    for (const p of plots) m.set(`${p.x},${p.y}`, p);
    return m;
  }, [plots]);

  const buildingById = useMemo(() => {
    const m = new Map<string, GridBuilding>();
    for (const b of buildings) m.set(b.id, b);
    return m;
  }, [buildings]);

  // Each cell's occupying building id (covers multi-cell footprints by reading
  // the plot's own building_id, the server's authoritative occupancy mark).
  const buildingIdForCell = useCallback(
    (x: number, y: number): string | null => {
      const p = plotAt.get(`${x},${y}`);
      return p ? (p.building_id ?? p.buildingId ?? null) : null;
    },
    [plotAt],
  );

  // ----- Actions -----

  const handlePlace = useCallback(
    async (entry: CatalogEntry, x: number, y: number) => {
      if (actionLoading) return;
      try {
        setActionLoading(true);
        setActionMessage(null);
        const res = await gridAPI.place(planetId, entry.kind, x, y, 1);
        const deferred = res?.materials_deferred
          ? ' (materials charge deferred — credits only)'
          : '';
        setActionMessage({
          kind: 'ok',
          text: `${entry.name || entry.kind} enqueued at (${x},${y})${deferred}.`,
        });
        setPopupPlot(null);
        await fetchGrid();
        onUpdate?.();
      } catch (err: any) {
        // apiRequest surfaces the server's human message for 402 (insufficient
        // credits) / 403 (research gate) / 400 (invalid placement) alike.
        setActionMessage({ kind: 'err', text: err?.message || 'Placement failed' });
      } finally {
        setActionLoading(false);
      }
    },
    [actionLoading, planetId, fetchGrid, onUpdate],
  );

  const handleDecommission = useCallback(
    async (buildingId: string) => {
      if (actionLoading) return;
      try {
        setActionLoading(true);
        setActionMessage(null);
        const res = await gridAPI.decommission(planetId, buildingId);
        const refund = Number(res?.refund_credits ?? res?.refundCredits ?? 0);
        setActionMessage({
          kind: 'ok',
          text: `Decommissioned — ${refund.toLocaleString()} credits refunded (25% of invested).`,
        });
        setSelectedBuildingId(null);
        await fetchGrid();
        onUpdate?.();
      } catch (err: any) {
        setActionMessage({ kind: 'err', text: err?.message || 'Decommission failed' });
      } finally {
        setActionLoading(false);
      }
    },
    [actionLoading, planetId, fetchGrid, onUpdate],
  );

  const handleCellClick = useCallback(
    (x: number, y: number) => {
      const occupant = buildingIdForCell(x, y);
      if (occupant) {
        // Occupied cell: toggle decommission selection (unchanged behavior).
        setSelectedBuildingId((cur) => (cur === occupant ? null : occupant));
        setPopupPlot(null);
        return;
      }
      // Non-placeable empty cell (hazard / uncleared): nothing to build here —
      // the cell is already styled not-allowed, so don't open the build popup.
      const plot = plotAt.get(`${x},${y}`);
      const cleared = plot?.cleared !== false; // default cleared unless explicitly false
      const hazard = plot?.hazard ?? null;
      if (!cleared || hazard) {
        return;
      }
      // Empty/placeable cell: open the catalog popup targeting THIS plot.
      setSelectedBuildingId(null);
      setActionMessage(null);
      setPopupPlot({ x, y });
    },
    [buildingIdForCell, plotAt],
  );

  // ----- Render -----

  if (loading) {
    return (
      <div className="grid-manager grid-loading">
        <div className="grid-spinner" />
        <span>Surveying construction grid...</span>
      </div>
    );
  }

  if (error || !view) {
    return (
      <div className="grid-manager grid-error">
        <span>{error || 'Grid unavailable'}</span>
        <button onClick={fetchGrid} className="grid-retry-btn">Retry</button>
      </div>
    );
  }

  if (citadelLevel < 1) {
    return (
      <div className="grid-manager grid-empty">
        <span className="grid-empty-title">No Construction Grid</span>
        <span className="grid-empty-sub">
          Build an Outpost citadel (L1) on the Citadel tab to break ground —
          plots unlock for construction once your colony is founded.
        </span>
      </div>
    );
  }

  const selectedBuilding = selectedBuildingId ? buildingById.get(selectedBuildingId) : null;
  const atSizeCap = citadelLevel >= maxCitadelLevel;

  return (
    <div className="grid-manager">
      <div className="grid-header">
        <h3>Construction Grid</h3>
        <div className="grid-header-badges">
          <span
            className="grid-level-badge"
            title="Citadel level is derived from the buildings on this grid (authoritative)."
          >
            Citadel L{citadelLevel}
          </span>
          <span
            className={`grid-cap-badge${atSizeCap ? ' at-cap' : ''}`}
            title={
              maxCitadelLevel < 5
                ? `This planet's surface area caps its citadel at Level ${maxCitadelLevel}. Build a larger world to reach higher.`
                : `Max citadel for this planet size is Level ${maxCitadelLevel}.`
            }
          >
            {atSizeCap ? '🛑' : '📐'} Max L{maxCitadelLevel}
          </span>
        </div>
      </div>

      <div className="grid-hint">
        {selectedBuildingId
          ? 'Building selected — decommission it below, or click another plot.'
          : 'Click an empty plot to choose a building to place. Click a placed building to decommission it.'}
      </div>

      {/* PRIMARY ACTION — the plot grid (scroll-law: rendered first, full width). */}
      <div
        className="grid-board"
        style={{
          gridTemplateColumns: `repeat(${Math.max(1, cols)}, 1fr)`,
        }}
        role="grid"
        aria-label={`Construction grid, ${cols} by ${rows} plots`}
      >
        {Array.from({ length: rows }, (_, y) =>
          Array.from({ length: cols }, (_, x) => {
            const plot = plotAt.get(`${x},${y}`);
            const offGrid = !plot;
            const occupantId = plot ? (plot.building_id ?? plot.buildingId ?? null) : null;
            const occupant = occupantId ? buildingById.get(occupantId) : null;
            // Render the building glyph only on its anchor (top-left) cell.
            const isAnchor = occupant && occupant.x === x && occupant.y === y;
            const hazard = plot?.hazard ?? null;
            const cleared = plot?.cleared !== false; // default cleared unless explicitly false
            const isSelectedBuilding = occupantId && occupantId === selectedBuildingId;
            const operational = occupant ? buildingComplete(occupant) == null : false;
            const isPopupTarget = !!popupPlot && popupPlot.x === x && popupPlot.y === y;
            const isPlaceable = !occupantId && cleared && !hazard && !offGrid;
            const cls = [
              'grid-cell',
              offGrid ? 'off-grid' : '',
              occupantId ? 'occupied' : 'empty',
              hazard ? 'hazard' : '',
              !cleared ? 'uncleared' : '',
              isSelectedBuilding ? 'selected' : '',
              isPlaceable ? 'placeable' : '',
              isPopupTarget ? 'popup-target' : '',
              occupant && !operational ? 'building-pending' : '',
            ]
              .filter(Boolean)
              .join(' ');
            return (
              <button
                key={`${x},${y}`}
                type="button"
                className={cls}
                disabled={offGrid || actionLoading}
                onClick={() => !offGrid && handleCellClick(x, y)}
                title={
                  offGrid
                    ? 'No plot here'
                    : occupant
                      ? `${occupant.name || occupant.kind} L${occupant.level}${operational ? '' : ' — under construction'} · click to select for decommission`
                      : hazard
                        ? `Hazard (${hazard.kind || 'blocked'}) — must be cleared before building`
                        : !cleared
                          ? 'Uncleared plot'
                          : `Empty plot (${x},${y}) — click to choose a building`
                }
              >
                {isAnchor && occupant ? (
                  <span className="cell-building">
                    <span className="cell-icon" aria-hidden="true">
                      {DOMAIN_ICON[occupant.domain || ''] || '🏗️'}
                    </span>
                    <span className="cell-level">L{occupant.level}</span>
                    {!operational && <span className="cell-pending" aria-hidden="true">⏳</span>}
                  </span>
                ) : occupantId && !isAnchor ? (
                  <span className="cell-span" aria-hidden="true" />
                ) : hazard ? (
                  <span className="cell-hazard" aria-hidden="true" title={hazard.kind || 'hazard'}>
                    ☢
                  </span>
                ) : (
                  <span className="cell-empty-dot" aria-hidden="true" />
                )}
              </button>
            );
          }),
        )}
      </div>

      {/* DECOMMISSION panel — appears when a placed building is selected. */}
      {selectedBuilding && (
        <div className="grid-decommission">
          <div className="decomm-info">
            <span className="decomm-name">
              {DOMAIN_ICON[selectedBuilding.domain || ''] || '🏗️'}{' '}
              {selectedBuilding.name || selectedBuilding.kind} — L{selectedBuilding.level}
            </span>
            <span className="decomm-sub">
              At plot ({selectedBuilding.x},{selectedBuilding.y})
              {buildingComplete(selectedBuilding) != null ? ' · under construction' : ' · operational'}
            </span>
            <span className="decomm-refund-note">
              Decommissioning refunds 25% of the credits invested.
            </span>
          </div>
          <div className="decomm-actions">
            <button
              className="grid-btn decomm-btn"
              disabled={actionLoading}
              onClick={() => handleDecommission(selectedBuilding.id)}
              title="Remove this building and refund 25% of invested credits"
            >
              Decommission
            </button>
            <button
              className="grid-btn cancel-btn"
              disabled={actionLoading}
              onClick={() => setSelectedBuildingId(null)}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* BUILD POPUP — plot-first: opened by clicking an empty plot, lists the
          placeable catalog; picking an affordable/ungated kind places it on the
          clicked plot via the existing place flow, then closes. */}
      {popupPlot && (
        <div
          className="grid-popup-overlay"
          role="presentation"
          onClick={() => setPopupPlot(null)}
        >
          <div
            className="grid-popup"
            role="dialog"
            aria-modal="true"
            aria-label={`Build on plot (${popupPlot.x}, ${popupPlot.y})`}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="grid-popup-header">
              <span className="grid-popup-title">
                Build on plot ({popupPlot.x},{popupPlot.y})
              </span>
              <button
                type="button"
                className="grid-popup-close"
                aria-label="Close build menu"
                onClick={() => setPopupPlot(null)}
              >
                ✕
              </button>
            </div>

            {/* Placement errors (server reject: 400/402/403) surface INSIDE the
                popup so they aren't hidden behind the scrim. */}
            {actionMessage?.kind === 'err' && (
              <div className="grid-popup-error" role="alert">
                <span aria-hidden="true">⚠️</span> {actionMessage.text}
              </div>
            )}

            <div className="catalog-list">
              {catalog.length === 0 && (
                <div className="catalog-empty">No placeable buildings available.</div>
              )}
              {catalog.map((entry) => {
                const gate = techGateOf(entry);
                const gated = gate != null && !researched.has(gate);
                const credits = level1Credits(entry);
                const materials = level1Materials(entry);
                const canAfford = playerCredits >= credits;
                const [w, h] = footprintOf(entry);
                const minCit = entry.min_citadel_level ?? entry.minCitadelLevel;
                const belowMinCitadel = typeof minCit === 'number' && citadelLevel < minCit;
                const disabled = gated || actionLoading || belowMinCitadel || !canAfford;
                const reason = gated
                  ? `Requires research: ${gate}`
                  : belowMinCitadel
                    ? `Requires citadel L${minCit}`
                    : !canAfford
                      ? `Need ${credits.toLocaleString()} cr (you have ${playerCredits.toLocaleString()})`
                      : `Place ${entry.name || entry.kind} on (${popupPlot.x},${popupPlot.y})`;
                return (
                  <button
                    key={entry.kind}
                    type="button"
                    className={`catalog-item${gated ? ' gated' : ''}${!canAfford && !gated && !belowMinCitadel ? ' unaffordable' : ''}`}
                    disabled={disabled}
                    onClick={() => handlePlace(entry, popupPlot.x, popupPlot.y)}
                    title={reason}
                  >
                    <span className="item-icon" aria-hidden="true">
                      {DOMAIN_ICON[entry.domain || ''] || '🏗️'}
                    </span>
                    <span className="item-body">
                      <span className="item-name">{entry.name || entry.kind}</span>
                      <span className="item-meta">
                        <span className="item-domain">
                          {DOMAIN_LABEL[entry.domain || ''] || entry.domain || ''}
                        </span>
                        <span className="item-footprint" title={`Footprint ${w}×${h} plots`}>
                          {w}×{h}
                        </span>
                      </span>
                      <span className="item-cost">
                        <span className={`cost-credits${!canAfford && !gated ? ' short' : ''}`}>
                          💰 {compact(credits)}
                        </span>
                        {materials.map(([mat, amt]) => (
                          <span key={mat} className="cost-material" title={`${amt} ${mat.replace(/_/g, ' ')}`}>
                            {matLabel(mat)} {compact(amt)}
                          </span>
                        ))}
                      </span>
                    </span>
                    <span className="item-state">
                      {gated ? (
                        <span className="item-gated" title={`Requires research node: ${gate}`}>
                          🔒 Requires research: {gate}
                        </span>
                      ) : belowMinCitadel ? (
                        <span className="item-gated">🏰 Requires L{minCit}</span>
                      ) : !canAfford ? (
                        <span className="item-gated">💰 Short</span>
                      ) : (
                        <span className="item-arrow">＋</span>
                      )}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* While the build popup is open its errors render INSIDE it (above), so
          don't also echo an error here behind the scrim. Success messages (popup
          already closed) and out-of-popup errors (e.g. decommission) still show. */}
      {actionMessage && !(popupPlot && actionMessage.kind === 'err') && (
        <div className={`grid-message ${actionMessage.kind === 'err' ? 'err' : 'ok'}`}>
          {actionMessage.text}
        </div>
      )}
    </div>
  );
};

export default GridManager;
