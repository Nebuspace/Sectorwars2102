import React, { useEffect } from 'react';
import type { Planet } from '../../types/planetary';
import {
  SPECIALIZATIONS,
  useColonySpecialization,
} from './ColonySpecialization';
import './specialization-drawer.css';

interface SpecializationDrawerProps {
  planet: Planet;
  onUpdate?: (planet: Planet) => void;
  onClose: () => void;
}

/**
 * SpecializationDrawer — the colony-specialization picker rendered as an
 * IN-TAB DRAWER instead of a full-screen modal.
 *
 * Root cause it fixes (design-brief #6): the old ColonySpecialization mount used
 * a `position:fixed` `.modal-overlay`, but it lived inside a transformed /
 * `backdrop-filter` cockpit ancestor — which becomes the containing block for
 * `position:fixed` descendants — so the overlay sized to the monitor panel (not
 * the viewport) and cockpit chrome bled over it; its CSS was also oversized
 * (`max-width:1200` vs the ~800 host, cards stacked full-width).
 *
 * This drawer instead renders with `position:absolute` INSIDE `.cmc-body` (which
 * we made `position:relative`), so it overlays only the active tab region, is
 * sized by the tab body, and obeys the SCROLL LAW (fits 1440x900 with internal
 * scroll only). The picker is a compact 2-column CSS grid.
 *
 * It reuses ColonySpecialization's data + selection logic VERBATIM via the
 * shared `useColonySpecialization` hook + the exported `SPECIALIZATIONS` table —
 * the spec rules / percentages / requirements / server call are unchanged; only
 * the shell/layout differs.
 */
const SpecializationDrawer: React.FC<SpecializationDrawerProps> = ({
  planet,
  onUpdate,
  onClose,
}) => {
  const {
    selectedSpec,
    setSelectedSpec,
    changing,
    error,
    successMessage,
    currentSpec,
    selectedSpecInfo,
    meetsRequirements,
    handleSpecialize,
  } = useColonySpecialization(planet, onUpdate, onClose);

  // ESC closes the drawer.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    // Scrim is confined to the tab region (absolute, not fixed) — click it to close.
    <div className="spec-drawer-scrim" onClick={onClose}>
      <div
        className="spec-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="Colony specialization"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="spec-drawer-header">
          <h3>Colony Specialization — {planet.name}</h3>
          <button
            type="button"
            className="spec-drawer-close"
            onClick={onClose}
            aria-label="Close specialization"
          >
            ✕
          </button>
        </div>

        <div className="spec-drawer-body">
          {currentSpec && (
            <div className="spec-drawer-current">
              <span className="spec-drawer-current-icon">{currentSpec.icon}</span>
              <div className="spec-drawer-current-text">
                <span className="spec-drawer-current-label">Current</span>
                <strong>{currentSpec.name}</strong>
              </div>
            </div>
          )}

          {error && (
            <div className="spec-drawer-msg error">
              <span aria-hidden="true">⚠️</span> {error}
            </div>
          )}
          {successMessage && (
            <div className="spec-drawer-msg success">
              <span aria-hidden="true">✅</span> {successMessage}
            </div>
          )}

          <div className="spec-drawer-grid">
            {SPECIALIZATIONS.map((spec) => {
              const requirements = meetsRequirements(spec);
              const isSelected = selectedSpec === spec.type;
              const isCurrent = planet.specialization === spec.type;

              return (
                <button
                  type="button"
                  key={spec.type}
                  className={`spec-drawer-card${isSelected ? ' selected' : ''}${!requirements.meets ? ' unavailable' : ''}${isCurrent ? ' current' : ''}`}
                  disabled={!requirements.meets || isCurrent}
                  onClick={() => requirements.meets && !isCurrent && setSelectedSpec(spec.type)}
                >
                  <div className="spec-drawer-card-head">
                    <span className="spec-drawer-card-icon">{spec.icon}</span>
                    <span className="spec-drawer-card-name">{spec.name}</span>
                    {isCurrent && <span className="spec-drawer-badge">Current</span>}
                  </div>

                  <p className="spec-drawer-card-desc">{spec.description}</p>

                  <ul className="spec-drawer-benefits">
                    {spec.benefits.map((benefit, index) => (
                      <li key={index}>{benefit}</li>
                    ))}
                  </ul>

                  <div className="spec-drawer-reqs">
                    {requirements.meets ? (
                      <span className="met">✓ Requirements met</span>
                    ) : (
                      <ul className="missing">
                        {requirements.missing.map((req, index) => (
                          <li key={index}>{req}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                </button>
              );
            })}
          </div>

          {selectedSpec && selectedSpec !== planet.specialization && (
            <div className="spec-drawer-summary">
              Change from <strong>{currentSpec?.name || 'None'}</strong> to{' '}
              <strong>{selectedSpecInfo?.name}</strong>. New bonuses take effect immediately.
            </div>
          )}
        </div>

        <div className="spec-drawer-actions">
          <button
            type="button"
            className="spec-drawer-btn secondary"
            onClick={onClose}
            disabled={changing}
          >
            Cancel
          </button>
          {selectedSpec && selectedSpec !== planet.specialization && (
            <button
              type="button"
              className="spec-drawer-btn primary"
              onClick={handleSpecialize}
              disabled={changing || !meetsRequirements(selectedSpecInfo!).meets}
            >
              {changing ? 'Specializing…' : 'Specialize Colony'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

export default SpecializationDrawer;
