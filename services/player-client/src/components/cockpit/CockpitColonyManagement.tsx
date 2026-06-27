import React, { useEffect, useState, useCallback } from 'react';
import { gameAPI } from '../../services/api';
import type { Planet } from '../../types/planetary';
import { ColonistAllocator } from '../planetary/ColonistAllocator';
import { BuildingManager } from '../planetary/BuildingManager';
import { DefenseConfiguration } from '../planetary/DefenseConfiguration';
import { GenesisDeployment } from '../planetary/GenesisDeployment';
import { ColonySpecialization as ColonySpecializationComponent } from '../planetary/ColonySpecialization';
import { SiegeStatusMonitor } from '../planetary/SiegeStatusMonitor';
import CitadelPanel from './CitadelPanel';
import GridPanel from './GridPanel';
import TerraformPanel from './TerraformPanel';
import ResearchPanel from './ResearchPanel';
import ProductionPanel, { type ProductionLine } from './ProductionPanel';
import '../planetary/planet-manager.css'; // .modal-overlay / .modal-content
import './cockpit-colony.css';

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
  /** Bump the caller's opsRefresh so the landed poll re-fetches after a mutation. */
  onOpsChange: () => void;
}

/**
 * CockpitColonyManagement — Screen 2 of the colony-cockpit redesign.
 *
 * The full colony-management depth, surfaced as cockpit-native HUD panels woven
 * into the landed planet console (GameDashboard, gated on isLandedPlanetMine).
 * Reuses the existing managers' data/API logic verbatim (CitadelManager /
 * GridManager / TerraformingPanel / EmpireResearchPanel) re-skinned to the CRT
 * aesthetic, and relocates all 6 action modals (allocator, buildings, defense,
 * genesis, specialization, siege) here so nothing the old PlanetManager could
 * do is lost — just relocated.
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
  onOpsChange,
}) => {
  // The rich owned-Planet object for the modals (the landed poll's detail shape
  // is not the same as the /planets/owned shape the modals were written for).
  const [ownedPlanet, setOwnedPlanet] = useState<Planet | null>(null);

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
  const [showAllocator, setShowAllocator] = useState(false);
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

  return (
    <div className="colony-mgmt-region">
      <div className="cmr-heading">
        ⬡ Colony Management — landed console
        <button
          type="button"
          className="cp-action-btn"
          style={{ flex: '0 0 auto', marginLeft: '0.6rem', verticalAlign: 'middle' }}
          onClick={() => setShowGenesis(true)}
          title="Deploy a Genesis Device to seed a new colony in an empty sector"
        >
          🌌 Deploy Genesis
        </button>
      </div>

      {/* Row 1 — Citadel · Grid · Terraform */}
      <div className="colony-mgmt-row row-1">
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
        <GridPanel
          planetId={planetId}
          playerCredits={playerCredits}
          placed={placed}
          onUpdate={handlePanelUpdate}
        />
        <TerraformPanel
          planetId={planetId}
          planetType={planetType}
          playerCredits={playerCredits}
          habitabilityScore={habitabilityScore}
          onUpdate={handlePanelUpdate}
        />
      </div>

      {/* Row 2 — Research flywheel · Production (live) */}
      <div className="colony-mgmt-row row-2">
        <ResearchPanel />
        <ProductionPanel
          lines={productionLines}
          overflowResources={overflowResources}
          onOpenAllocator={() => setShowAllocator(true)}
          onOpenSpecialization={() => setShowSpecialization(true)}
        />
      </div>

      {/* ── The 6 action modals (relocated from PlanetManager) ── */}
      {showAllocator && ownedPlanet && (
        <div className="modal-overlay" onClick={() => setShowAllocator(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <ColonistAllocator
              planet={ownedPlanet}
              onUpdate={(p) => handleModalUpdate(p)}
              onClose={() => setShowAllocator(false)}
            />
          </div>
        </div>
      )}

      {showBuildings && ownedPlanet && (
        <div className="modal-overlay" onClick={() => setShowBuildings(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <BuildingManager
              planet={ownedPlanet}
              onUpdate={(p) => handleModalUpdate(p)}
              onClose={() => setShowBuildings(false)}
            />
          </div>
        </div>
      )}

      {showDefense && ownedPlanet && (
        <div className="modal-overlay" onClick={() => setShowDefense(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <DefenseConfiguration
              planet={ownedPlanet}
              onUpdate={(p) => handleModalUpdate(p)}
              onClose={() => setShowDefense(false)}
            />
          </div>
        </div>
      )}

      {showGenesis && (
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
        </div>
      )}

      {showSpecialization && ownedPlanet && (
        <div className="modal-overlay" onClick={() => setShowSpecialization(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <ColonySpecializationComponent
              planet={ownedPlanet}
              onUpdate={(p) => handleModalUpdate(p)}
              onClose={() => setShowSpecialization(false)}
            />
          </div>
        </div>
      )}

      {showSiege && ownedPlanet && (
        <div className="modal-overlay" onClick={() => setShowSiege(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <SiegeStatusMonitor
              planet={ownedPlanet}
              onUpdate={(p) => handleModalUpdate(p)}
              onClose={() => setShowSiege(false)}
            />
          </div>
        </div>
      )}
    </div>
  );
};

export default CockpitColonyManagement;
