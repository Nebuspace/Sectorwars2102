import React from 'react';
import { formatCredits } from '../../utils/formatters';
import { ModuleGridInterface } from '../ships';
import './spacedock.css';

// =====================================================================
// Shipyard — extracted verbatim from SpaceDockInterface's inline
// `renderShipyard()` closure (WO-UI3-VENUES sub-part #1, pure refactor —
// zero behavior change). All state/handlers remain owned by
// SpaceDockInterface and are threaded through as props here.
// =====================================================================

// Shipyard catalog entry (GET /api/v1/ships/catalog) — mirrors
// SpaceDockInterface.tsx's identically-named interface.
export interface ShipCatalogEntry {
  type: string;
  name: string;
  base_cost: number;
  purchasable: boolean;
  speed: number;
  turn_cost: number;
  max_cargo: number;
  max_colonists: number;
  max_drones: number;
  max_shields: number;
  hull_points: number;
  attack_rating: number;
  defense_rating: number;
  max_genesis_devices: number;
  description: string;
  reason?: string | null;
}

// Normalize ship type strings for comparison (e.g. "Cargo Hauler" vs "CARGO_HAULER")
const normalizeShipType = (shipType?: string | null): string =>
  (shipType || '').toUpperCase().replace(/[\s-]+/g, '_');

interface ShipyardVenueProps {
  shipId: string | undefined;
  shipType: string | undefined;
  tradedockTier: 'A' | 'B' | null;
  displayCredits: number;
  refreshPlayerState: () => void;
  fetchShipData: () => void;
  shipPurchaseSuccess: string | null;
  shipPurchaseError: string | null;
  shipCatalogLoading: boolean;
  shipCatalog: ShipCatalogEntry[] | null;
  shipCatalogError: string | null;
  fetchShipCatalog: () => void;
  confirmShip: ShipCatalogEntry | null;
  setConfirmShip: (ship: ShipCatalogEntry | null) => void;
  newShipName: string;
  setNewShipName: (name: string) => void;
  shipPurchasing: boolean;
  setShipPurchaseError: (error: string | null) => void;
  setShipPurchaseSuccess: (message: string | null) => void;
  purchaseShip: (entry: ShipCatalogEntry, requestedName: string) => void;
  onBack: () => void;
  onOpenConstruction: () => void;
}

const ShipyardVenue: React.FC<ShipyardVenueProps> = ({
  shipId,
  shipType,
  tradedockTier,
  displayCredits,
  refreshPlayerState,
  fetchShipData,
  shipPurchaseSuccess,
  shipPurchaseError,
  shipCatalogLoading,
  shipCatalog,
  shipCatalogError,
  fetchShipCatalog,
  confirmShip,
  setConfirmShip,
  newShipName,
  setNewShipName,
  shipPurchasing,
  setShipPurchaseError,
  setShipPurchaseSuccess,
  purchaseShip,
  onBack,
  onOpenConstruction,
}) => {
  const currentShipType = normalizeShipType(shipType);

  return (
    <div className="venue-container shipyard">
      <div className="venue-header">
        <button className="back-button" onClick={onBack}>
          ← Back to Hub
        </button>
        <h2>🛠️ Shipyard</h2>
      </div>
      <div className="venue-content-area">
        <div className="shipyard-sections">
          <div className="shipyard-section">
            <h3>🏗️ Construction Slips</h3>
            {tradedockTier ? (
              <>
                <p className="section-description">
                  This Tier-{tradedockTier} TradeDock runs full construction slips. Ship orders and build tracking live in the Construction venue.
                </p>
                <button className="action-button" onClick={onOpenConstruction}>
                  Open Construction Venue
                </button>
              </>
            ) : (
              <>
                <p className="section-description">
                  This facility isn&apos;t a TradeDock — construction slips only run at a Tier A/B TradeDock station.
                </p>
                <button className="action-button" disabled>Reserve Dock Slip</button>
              </>
            )}
          </div>

          {/* WO-SM-5 (reachability gate-fix): the slot-grid module UI lives here
              in the ACTIVE Shipyard venue (the venue card already advertises
              "Ship Customization"). It was previously mounted only in the legacy
              .service-card "Ship Upgrades" overlay, which the venue-card hub no
              longer renders — so the grid was unreachable in the live UI. */}
          {shipId && (
            <div className="shipyard-section">
              <h3>🔧 Ship Customization</h3>
              <p className="section-description">
                Fit modules into your hull's slot grid — supercharged slots, class locks, and salvage on removal.
              </p>
              <ModuleGridInterface
                ship={{ id: shipId }}
                playerCredits={displayCredits}
                onChanged={() => { refreshPlayerState(); fetchShipData(); }}
              />
            </div>
          )}

          <div className="shipyard-section">
            <h3>🚀 Ship Catalog</h3>
            <p className="section-description">Browse and purchase pre-fabricated vessels</p>

            {shipPurchaseSuccess && (
              <div className="genesis-success-message">
                <span className="success-icon">✅</span>
                {shipPurchaseSuccess}
              </div>
            )}
            {shipPurchaseError && !confirmShip && (
              <div className="genesis-error-message">
                <span className="error-icon">❌</span>
                {shipPurchaseError}
              </div>
            )}

            {shipCatalogLoading && !shipCatalog && (
              <div className="catalog-loading">Accessing shipyard registry...</div>
            )}
            {shipCatalogError && !shipCatalogLoading && (
              <div className="genesis-error-message">
                <span className="error-icon">❌</span>
                {shipCatalogError}
                <button className="action-button" onClick={fetchShipCatalog}>Retry</button>
              </div>
            )}
            {!shipCatalogError && shipCatalog && (
              <div className="ship-catalog">
                {shipCatalog.map(ship => {
                  const isCurrent = currentShipType !== '' && normalizeShipType(ship.type) === currentShipType;
                  return (
                    <div
                      key={ship.type}
                      className={`ship-card${!ship.purchasable ? ' unavailable' : ''}${isCurrent ? ' current-ship' : ''}`}
                    >
                      <div className="ship-info">
                        <span className="ship-name">
                          {ship.name}
                          {isCurrent && <span className="current-ship-badge">YOUR SHIP</span>}
                        </span>
                        <div className="ship-stats">
                          <span title="Cargo holds">📦 {ship.max_cargo}</span>
                          <span title="Speed">⚡ {ship.speed}</span>
                          <span title="Drone capacity">🤖 {ship.max_drones}</span>
                          <span title="Shield capacity">🛡️ {ship.max_shields}</span>
                          <span title="Hull points">🔩 {ship.hull_points}</span>
                          <span title="Genesis Device capacity">🧬 {ship.max_genesis_devices || 0}</span>
                        </div>
                      </div>
                      {ship.purchasable ? (
                        <>
                          <div className="ship-price">{formatCredits(ship.base_cost)}</div>
                          <button
                            className="buy-ship-btn"
                            onClick={() => {
                              setConfirmShip(ship);
                              setNewShipName('');
                              setShipPurchaseError(null);
                              setShipPurchaseSuccess(null);
                            }}
                            disabled={shipPurchasing || displayCredits < ship.base_cost}
                            title={displayCredits < ship.base_cost ? 'Insufficient credits' : undefined}
                          >
                            Purchase
                          </button>
                        </>
                      ) : (
                        <div className="ship-unavailable-reason">
                          {ship.reason || 'Not available for purchase'}
                        </div>
                      )}
                    </div>
                  );
                })}
                {shipCatalog.length === 0 && (
                  <p className="section-description">No vessels currently listed at this shipyard.</p>
                )}
              </div>
            )}
          </div>
        </div>

        {confirmShip && (
          <div
            className="ship-confirm-overlay"
            onClick={() => !shipPurchasing && setConfirmShip(null)}
          >
            <div className="ship-confirm-panel" onClick={e => e.stopPropagation()}>
              <h3>Confirm Purchase — {confirmShip.name}</h3>
              {confirmShip.description && (
                <p className="section-description">{confirmShip.description}</p>
              )}
              <label className="ship-name-label">
                Ship name (optional)
                <input
                  type="text"
                  value={newShipName}
                  onChange={e => setNewShipName(e.target.value)}
                  placeholder={confirmShip.name}
                  maxLength={50}
                  disabled={shipPurchasing}
                />
              </label>
              <div className="confirm-cost-rows">
                <div className="confirm-cost-row">
                  <span>Cost</span>
                  <span>{formatCredits(confirmShip.base_cost)}</span>
                </div>
                <div className="confirm-cost-row">
                  <span>Your credits</span>
                  <span>{formatCredits(displayCredits)}</span>
                </div>
                <div className={`confirm-cost-row balance${displayCredits - confirmShip.base_cost < 0 ? ' negative' : ''}`}>
                  <span>After purchase</span>
                  <span>{formatCredits(displayCredits - confirmShip.base_cost)}</span>
                </div>
              </div>
              {shipPurchaseError && (
                <div className="genesis-error-message">
                  <span className="error-icon">❌</span>
                  {shipPurchaseError}
                </div>
              )}
              <div className="confirm-actions">
                <button
                  className="action-button"
                  onClick={() => setConfirmShip(null)}
                  disabled={shipPurchasing}
                >
                  Cancel
                </button>
                <button
                  className="action-button primary"
                  onClick={() => purchaseShip(confirmShip, newShipName)}
                  disabled={shipPurchasing || displayCredits < confirmShip.base_cost}
                >
                  {shipPurchasing ? 'Processing...' : 'Confirm Purchase'}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default ShipyardVenue;
