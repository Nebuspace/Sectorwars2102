import React from 'react';
import CockpitPanel from './CockpitPanel';

export interface ProductionLine {
  key: 'fuel' | 'organics' | 'equipment';
  icon: string;
  name: string;
  /** Live projected stockpile (already ticking via the caller's clock). */
  stock: number;
  /** Production rate per day. */
  rate: number;
  /** Storage fill ratio 0..1 (0 when uncapped). */
  ratio: number;
  capped: boolean;
  nearCap: boolean;
  atCap: boolean;
  /** Per-resource storage cap (0 when uncapped). */
  cap: number;
}

export interface ProductionPanelProps {
  /** Live production lines, projected off the realtime poll (landedPlanetDetail). */
  lines: ProductionLine[];
  /** Commodities the server flagged as overflowing at the last tick. */
  overflowResources: string[];
  /** Open the Colonist Allocator modal (full workforce assignment). */
  onOpenAllocator: () => void;
  /** Open the Colony Specialization modal. */
  onOpenSpecialization: () => void;
}

const fmt = (n: number) => Math.floor(n).toLocaleString();

/**
 * ProductionPanel — the PRODUCTION HUD instrument. Ticks LIVE off
 * landedPlanetDetail (the 15s realtime poll + the caller's per-second
 * projection clock) — no extra fetch. Surfaces fuel / organics / equipment
 * stockpiles + rates + storage fill, plus the Allocator modal.
 */
const ProductionPanel: React.FC<ProductionPanelProps> = ({ lines, overflowResources, onOpenAllocator, onOpenSpecialization }) => (
  <CockpitPanel
    title="Production"
    accent="#7dd3fc"
    readout={<span className="cp-live-tag">┄ LIVE ┄</span>}
  >
    {lines.length === 0 ? (
      <div className="cp-empty">Colony ledger unavailable</div>
    ) : (
      <div className="cp-production-lines">
        {lines.map((l) => (
          <div className="cp-prod-row" key={l.key}>
            <span className="cp-prod-icon">{l.icon}</span>
            <span className="cp-prod-name">{l.name}</span>
            <span className="cp-prod-stock">
              📦 {fmt(l.stock)}
              {l.capped ? `/${l.cap.toLocaleString()}` : ''}
              <span className="cp-prod-rate"> +{Math.round(l.rate).toLocaleString()}/d</span>
            </span>
            {l.capped && (
              <div className="cp-prod-bar">
                <div
                  className={`cp-prod-bar-fill${l.atCap ? ' at-cap' : l.nearCap ? ' near-cap' : ''}`}
                  style={{ width: `${Math.round(l.ratio * 100)}%` }}
                />
              </div>
            )}
          </div>
        ))}
      </div>
    )}
    {overflowResources.length > 0 && (
      <div className="cp-prod-overflow" role="alert">
        ⚠️ Storage full: {overflowResources.join(', ')} — output above the cap is wasted.
      </div>
    )}
    <div className="cp-actions">
      <button type="button" className="cp-action-btn" onClick={onOpenAllocator} title="Assign colonists to production roles">
        📊 Allocate Workforce
      </button>
      <button type="button" className="cp-action-btn" onClick={onOpenSpecialization} title="Choose a colony specialization">
        🎯 Specialization
      </button>
    </div>
  </CockpitPanel>
);

export default ProductionPanel;
