import React, { useEffect, useState, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { gameAPI } from '../../services/api';
import type { Planet } from '../../types/planetary';
import { BuildingManager } from '../planetary/BuildingManager';
import { DefenseConfiguration } from '../planetary/DefenseConfiguration';
import { GenesisDeployment } from '../planetary/GenesisDeployment';
import SpecializationDrawer from '../planetary/SpecializationDrawer';
import { SiegeStatusMonitor } from '../planetary/SiegeStatusMonitor';
import CitadelPanel from './CitadelPanel';
import GridPanel from './GridPanel';
import TerraformPanel from './TerraformPanel';
import ResearchPanel from './ResearchPanel';
import ProductionPanel, { type ProductionLine } from './ProductionPanel';
import type { RoleAllocation, ProdRole } from './CoupledColonistSliders';
import '../planetary/planet-manager.css'; // .modal-overlay / .modal-content
import './cockpit-colony.css';

/** The tabs of the landed-colony management console. */
type ColonyTab = 'citadel' | 'grid' | 'terraform' | 'research' | 'production' | 'defense' | 'safe';

export interface CockpitColonyManagementProps {
  /** Landed planet id (string). The cockpit gates this on landed + owned. */
  planetId: string;
  /** Planet type (for terraforming biome reclass + habitability context). */
  planetType?: string | null;
  playerCredits: number;
  /** Live citadel telemetry from the landed poll (level / drones / name). */
  citadelInfo: any;
  /** Live planet detail from the 15s realtime poll (production / stockpiles). */
  landedPlanetDetail: any;
  /** Current habitability (0..100) for the terraform readout. */
  habitabilityScore?: number | null;
  /** True when the colony is under active siege. */
  underSiege?: boolean;
  /** Live production lines, projected off the realtime poll by the caller. */
  productionLines: ProductionLine[];
  /** Commodities the server flagged as overflowing last tick. */
  overflowResources: string[];
  /** Current per-role colonist head-counts (optimistic). */
  allocations: RoleAllocation;
  /** Server-confirmed per-day production rates per role. */
  productionRates: Partial<Record<ProdRole, number>> | null | undefined;
  /** Workforce budget — citadel cap clamped to colonists. */
  allocBudget: number;
  /** Total colonists on the planet (may exceed the workforce cap). */
  totalColonists: number;
  /** Persist a full allocation via the revived inline persister. */
  onSetAllocations: (next: RoleAllocation) => void;
  /** True while an allocation persist is in flight. */
  allocSyncing?: boolean;
  /** Verbatim server error from the last failed allocation persist. */
  allocError?: string | null;
  /** Store a resource's storable amount into the citadel safe (reuses deposit flow). */
  onStoreToSafe: (key: 'fuel' | 'organics' | 'equipment', amount: number) => void;
  /** Bump the caller's opsRefresh so the landed poll re-fetches after a mutation. */
  onOpsChange: () => void;
  /**
   * The DEFENSE-OPS tab body — rendered by the caller (GameDashboard) so the
   * shield-upgrade / citadel-cancel / defense-building controls keep their
   * existing handlers + live telemetry from the landed closure. Folded in here
   * as a tab so the page no longer stacks it as a separate section.
   */
  defenseTab?: React.ReactNode;
  /**
   * The SAFE tab body — the Citadel-Safe protected-commodity store/take + the
   * auto-deposit toggle, rendered by the caller for the same reason as above.
   */
  safeTab?: React.ReactNode;
}

/**
 * CockpitColonyManagement — Screen 2 of the colony-cockpit redesign, now a
 * TABBED management console.
 *
 * The full colony-management depth, surfaced as cockpit-native HUD panels woven
 * into the landed planet console (GameDashboard, gated on isLandedPlanetMine).
 * Reuses the existing managers' data/API logic verbatim (CitadelManager /
 * GridManager / TerraformingPanel / EmpireResearchPanel) re-skinned to the CRT
 * aesthetic, and relocates the action modals (buildings, defense, genesis,
 * specialization, siege) here so nothing the old PlanetManager could do is lost.
 * Workforce allocation is now an in-tab control on the Production panel (the
 * coupled colonist sliders), so it is no longer a modal.
 *
 * A tab bar selects ONE panel at a time; the active panel renders in a region
 * sized to the remaining viewport height (flex:1 + min-height:0 + overflow on
 * the tab body), so the whole landed view fits 1440x900 with no page scroll —
 * only a single overflowing panel scrolls internally.
 *
 * The 6 modals operate on the RICH owned-Planet object (the same shape
 * /planets/owned returns, which the modals were written against); we fetch it
 * once for this landed planet and re-fetch when ops change.
 */
const CockpitColonyManagement: React.FC<CockpitColonyManagementProps> = ({
  planetId,
  planetType,
  playerCredits,
  citadelInfo,
  landedPlanetDetail,
  habitabilityScore,
  underSiege,
  productionLines,
  overflowResources,
  allocations,
  productionRates,
  allocBudget,
  totalColonists,
  onSetAllocations,
  allocSyncing,
  allocError,
  onStoreToSafe,
  onOpsChange,
  defenseTab,
  safeTab,
}) => {
  // The rich owned-Planet object for the modals (the landed poll's detail shape
  // is not the same as the /planets/owned shape the modals were written for).
  const [ownedPlanet, setOwnedPlanet] = useState<Planet | null>(null);

  // Active management tab. Citadel is the default landing tab.
  const [tab, setTab] = useState<ColonyTab>('citadel');

  const loadOwnedPlanet = useCallback(() => {
    let cancelled = false;
    gameAPI.planetary
      .getOwnedPlanets()
      .then((res: any) => {
        if (cancelled) return;
        const list: Planet[] = res?.planets || [];
        const match = list.find((p) => String(p.id) === String(planetId)) || null;
        setOwnedPlanet(match);
      })
      .catch(() => {
        if (!cancelled) setOwnedPlanet(null);
      });
    return () => {
      cancelled = true;
    };
  }, [planetId]);

  useEffect(() => {
    const cleanup = loadOwnedPlanet();
    return cleanup;
  }, [loadOwnedPlanet]);

  // After a modal mutation: refresh the rich object locally AND bump the caller's
  // ops signal so the landed poll re-fetches the live telemetry the panels read.
  const handleModalUpdate = useCallback(
    (updated?: Planet) => {
      if (updated) setOwnedPlanet(updated);
      else loadOwnedPlanet();
      onOpsChange();
    },
    [loadOwnedPlanet, onOpsChange],
  );

  // Panels reuse the managers directly; their onUpdate just re-pulls live data.
  const handlePanelUpdate = useCallback(() => {
    loadOwnedPlanet();
    onOpsChange();
  }, [loadOwnedPlanet, onOpsChange]);

  // ── Modal visibility ──
  // (Workforce allocation is now an in-tab control on the Production panel — the
  // old "Allocate Workforce" modal is gone; reallocation is canon free + instant.)
  const [showBuildings, setShowBuildings] = useState(false);
  const [showDefense, setShowDefense] = useState(false);
  const [showGenesis, setShowGenesis] = useState(false);
  const [showSpecialization, setShowSpecialization] = useState(false);
  const [showSiege, setShowSiege] = useState(false);

  const citadelLevel = typeof citadelInfo?.citadel_level === 'number' ? citadelInfo.citadel_level : null;
  const stationedDrones = ownedPlanet?.defenses?.drones;

  // Grid placed count: prefer the owned-planet building count when present.
  const placed =
    typeof landedPlanetDetail?.gridPlaced === 'number'
      ? landedPlanetDetail.gridPlaced
      : Array.isArray(ownedPlanet?.buildings)
        ? ownedPlanet!.buildings.length
        : null;

  // Tab definitions — the active one renders its panel; a glanceable readout
  // appears on each tab so the player keeps the key vital without leaving it.
  const tabs: { key: ColonyTab; label: string; icon: string }[] = [
    { key: 'citadel', label: 'Citadel', icon: '⬡' },
    { key: 'grid', label: 'Grid', icon: '▦' },
    { key: 'terraform', label: 'Terraform', icon: '🌍' },
    { key: 'research', label: 'Research', icon: '⟳' },
    { key: 'production', label: 'Production', icon: '🏭' },
    { key: 'defense', label: 'Defense', icon: '🛡️' },
    { key: 'safe', label: 'Safe', icon: '🔐' },
  ];

  return (
    <div className="colony-mgmt-console">
      <div className="cmc-tabbar" role="tablist" aria-label="Colony management">
        {tabs.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            className={`cmc-tab${tab === t.key ? ' active' : ''}`}
            // Switching tabs dismisses the Specialization drawer so its
            // absolute inset:0 overlay can't cover a different tab's panel.
            onClick={() => { setTab(t.key); setShowSpecialization(false); }}
          >
            <span className="cmc-tab-icon" aria-hidden="true">{t.icon}</span>
            <span className="cmc-tab-label">{t.label}</span>
          </button>
        ))}
        {/* Genesis is a cross-colony action, not a tab — keep it always reachable. */}
        <button
          type="button"
          className="cmc-genesis-btn"
          onClick={() => setShowGenesis(true)}
          title="Deploy a Genesis Device to seed a new colony in an empty sector"
        >
          🌌 Genesis
        </button>
      </div>

      <div className="cmc-body" role="tabpanel">
        {tab === 'citadel' && (
          <CitadelPanel
            planetId={planetId}
            playerCredits={playerCredits}
            stationedDrones={stationedDrones}
            citadelLevel={citadelLevel}
            underSiege={underSiege}
            onUpdate={handlePanelUpdate}
            onOpenBuildings={() => setShowBuildings(true)}
            onOpenDefense={() => setShowDefense(true)}
            onOpenSiege={() => setShowSiege(true)}
          />
        )}
        {tab === 'grid' && (
          <GridPanel
            planetId={planetId}
            playerCredits={playerCredits}
            placed={placed}
            onUpdate={handlePanelUpdate}
          />
        )}
        {tab === 'terraform' && (
          <TerraformPanel
            planetId={planetId}
            planetType={planetType}
            playerCredits={playerCredits}
            habitabilityScore={habitabilityScore}
            onUpdate={handlePanelUpdate}
          />
        )}
        {tab === 'research' && <ResearchPanel />}
        {tab === 'production' && (
          <ProductionPanel
            lines={productionLines}
            overflowResources={overflowResources}
            onOpenSpecialization={() => setShowSpecialization(true)}
            allocations={allocations}
            productionRates={productionRates}
            allocBudget={allocBudget}
            totalColonists={totalColonists}
            onSetAllocations={onSetAllocations}
            allocSyncing={allocSyncing}
            allocError={allocError}
            onStoreToSafe={onStoreToSafe}
          />
        )}
        {tab === 'defense' && (defenseTab ?? <div className="cp-empty">Defense telemetry unavailable</div>)}
        {tab === 'safe' && (safeTab ?? <div className="cp-empty">Vault telemetry unavailable</div>)}

        {/* Specialization is an IN-TAB DRAWER (position:absolute inside .cmc-body)
            rather than a position:fixed modal — fixes the trapped-fixed sizing /
            chrome-bleed bug (design-brief #6). It overlays the active tab region
            and is sized by the tab body. Opened from the Production tab's button. */}
        {showSpecialization && ownedPlanet && (
          <SpecializationDrawer
            planet={ownedPlanet}
            onUpdate={(p) => handleModalUpdate(p)}
            onClose={() => setShowSpecialization(false)}
          />
        )}
      </div>

      {/* ── The action modals (relocated from PlanetManager) ──
          Each is portaled to document.body so its position:fixed .modal-overlay
          escapes the transformed/backdrop-filter cockpit ancestor (which would
          otherwise become the containing block and trap it to the monitor panel
          — the same root cause the Specialization drawer fixes, design-brief #6).
          Their internal logic is unchanged; only WHERE they mount differs.
          (Mirrors the createPortal pattern the colonist-transfer modal uses in
          GameDashboard.) */}
      {showBuildings && ownedPlanet && createPortal(
        <div className="modal-overlay" onClick={() => setShowBuildings(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <BuildingManager
              planet={ownedPlanet}
              onUpdate={(p) => handleModalUpdate(p)}
              onClose={() => setShowBuildings(false)}
            />
          </div>
        </div>,
        document.body,
      )}

      {showDefense && ownedPlanet && createPortal(
        <div className="modal-overlay" onClick={() => setShowDefense(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <DefenseConfiguration
              planet={ownedPlanet}
              onUpdate={(p) => handleModalUpdate(p)}
              onClose={() => setShowDefense(false)}
            />
          </div>
        </div>,
        document.body,
      )}

      {showGenesis && createPortal(
        <div className="modal-overlay" onClick={() => setShowGenesis(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <GenesisDeployment
              onSuccess={() => {
                setShowGenesis(false);
                handleModalUpdate();
              }}
              onClose={() => setShowGenesis(false)}
            />
          </div>
        </div>,
        document.body,
      )}

      {showSiege && ownedPlanet && createPortal(
        <div className="modal-overlay" onClick={() => setShowSiege(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <SiegeStatusMonitor
              planet={ownedPlanet}
              onUpdate={(p) => handleModalUpdate(p)}
              onClose={() => setShowSiege(false)}
            />
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
};

export default CockpitColonyManagement;
