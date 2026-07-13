import React, { useEffect, useRef } from 'react';
import type { Sector } from '../../contexts/GameContext';

interface HazardAnalysisCardProps {
  sector: Sector | null;
  onClose: () => void;
}

/**
 * HazardAnalysisCard — the HAZARD segment's owning surface (WO-UI1-CHROME-
 * COMPLETE item 6: "HAZARD→the analysis card"). Mirrors the ratified
 * prototype's `hazInfo()` (RATIFIED.html:1110-1115), which pops a self-
 * contained card ("HAZARD ANALYSIS — {sector} · {level}/10") rather than
 * navigating anywhere — no deck/GameDashboard reach needed, unlike LAW/
 * THREAT/COMM. Real fields only (currentSector.hazard_level/radiation_level/
 * special_features/description) — the SAME data GameDashboard's SOLAR
 * SYSTEM[SYSTEM] `.system-hazard-fold` block already surfaces; no invented
 * flavor text (the prototype's own `h.type`/`h.fx` have no real-data
 * analogue in this codebase's Sector model).
 *
 * Pixel a11y fix-pass (WCAG 2.1.1 / 2.4.3): this is only ever mounted while
 * open (Annunciator.tsx conditionally renders it on `hazardCardOpen`), so
 * "on mount" IS "on open" -- focus moves to the close button the instant it
 * appears, and Escape closes it from anywhere inside. Focus RESTORE to the
 * lamp that opened the card is the caller's responsibility (see
 * Annunciator.tsx's `handleCloseHazardCard` -- this component only ever
 * calls the `onClose` it's handed, it doesn't know which button opened it).
 */
const HazardAnalysisCard: React.FC<HazardAnalysisCardProps> = ({ sector, onClose }) => {
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeButtonRef.current?.focus();
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape') {
      e.stopPropagation();
      onClose();
    }
  };

  return (
    <div
      className="annunciator-card"
      role="dialog"
      aria-modal="true"
      aria-label="Hazard analysis"
      style={{ pointerEvents: 'auto' }}
      onKeyDown={handleKeyDown}
    >
      <div className="annunciator-card-header">
        <span>HAZARD ANALYSIS{sector ? ` — ${sector.name}` : ''}</span>
        <button
          type="button"
          ref={closeButtonRef}
          className="annunciator-card-close"
          onClick={onClose}
          aria-label="Close hazard analysis"
        >
          ✕
        </button>
      </div>
      {sector ? (
        <div className="annunciator-card-body">
          <div className="annunciator-card-row">
            <span>HAZARD</span>
            <span>{sector.hazard_level}/10</span>
          </div>
          <div className="annunciator-card-row">
            <span>RADIATION</span>
            <span>{(sector.radiation_level * 100).toFixed(1)}%</span>
          </div>
          {sector.special_features && sector.special_features.length > 0 && (
            <div className="annunciator-card-row annunciator-card-features">
              {sector.special_features.map((feature) => (
                <span key={feature} className="annunciator-card-badge">
                  {feature.replace(/_/g, ' ').toUpperCase()}
                </span>
              ))}
            </div>
          )}
          {sector.description && <p className="annunciator-card-description">{sector.description}</p>}
          <p className="annunciator-card-note">Lamp grammar: CAUTION lights above zero · click to acknowledge.</p>
        </div>
      ) : (
        <div className="annunciator-card-body">
          <p className="annunciator-card-description">No sector telemetry.</p>
        </div>
      )}
    </div>
  );
};

export default HazardAnalysisCard;
