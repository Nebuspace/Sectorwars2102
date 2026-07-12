import React from 'react';
import { formatCredits } from '../../utils/formatters';
import { InsuranceManager, MaintenanceManager, ModuleGridInterface, TIER_LABEL } from '../ships';
import './spacedock.css';

// =====================================================================
// Ship Services — extracted verbatim from SpaceDockInterface's inline
// `renderServices()` closure (WO-UI3-VENUES sub-part #1, pure refactor —
// zero behavior change). All state/handlers remain owned by
// SpaceDockInterface and are threaded through as props here.
// =====================================================================

// Ship telemetry shape read by this venue — mirrors the fields
// SpaceDockInterface.tsx's own `shipData` state carries.
interface ShipData {
  id: string;
  name: string;
  combat?: Record<string, unknown> | null;
  cargo?: Record<string, number> | null;
  cargo_capacity?: number;
  current_value?: number;
}

interface ServicesVenueProps {
  shipData: ShipData | null;
  displayCredits: number;
  stationServices: Record<string, any>;
  repairSuccess: string | null;
  repairError: string | null;
  repairBusy: boolean;
  repairShip: () => void;
  showInsurance: boolean;
  setShowInsurance: (show: boolean) => void;
  showMaintenance: boolean;
  setShowMaintenance: (show: boolean) => void;
  showUpgrades: boolean;
  setShowUpgrades: (show: boolean) => void;
  insuranceTier: string | null;
  fetchInsuranceStatus: (shipId: string) => void;
  refreshPlayerState: () => void;
  fetchShipData: () => void;
  onBack: () => void;
  blackMarketButton: React.ReactNode;
}

const ServicesVenue: React.FC<ServicesVenueProps> = ({
  shipData,
  displayCredits,
  stationServices,
  repairSuccess,
  repairError,
  repairBusy,
  repairShip,
  showInsurance,
  setShowInsurance,
  showMaintenance,
  setShowMaintenance,
  showUpgrades,
  setShowUpgrades,
  insuranceTier,
  fetchInsuranceStatus,
  refreshPlayerState,
  fetchShipData,
  onBack,
  blackMarketButton,
}) => {
  // Read real hull/shield condition off the current ship. The combat dict
  // mirrors the server's ShipResponse; values are plain numbers there.
  const combat = shipData?.combat ?? null;
  const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
  const hull = num(combat?.hull);
  const maxHull = num(combat?.max_hull);
  const shields = num(combat?.shields);
  const maxShields = num(combat?.max_shields);

  const hullPct = hull !== null && maxHull ? Math.max(0, Math.min(100, (hull / maxHull) * 100)) : null;
  const shieldPct = shields !== null && maxShields ? Math.max(0, Math.min(100, (shields / maxShields) * 100)) : null;

  // Mirror the server's canon pricing (player.py repair endpoint):
  // Basic repair = 5% of ship value per +10% combined hull+shield rating
  const totalMax = (maxHull ?? 0) + (maxShields ?? 0);
  const deficit = ((maxHull ?? 0) - (hull ?? 0)) + ((maxShields ?? 0) - (shields ?? 0));
  const deficitPct = totalMax > 0 ? Math.max(0, (deficit / totalMax) * 100) : 0;
  const repairCost = totalMax > 0
    ? Math.round((shipData?.current_value ?? 0) * 0.05 * (deficitPct / 10))
    : null;
  const atFullCondition = totalMax > 0 && deficitPct <= 0;

  // Cargo: "used" field when present, else sum commodity values while
  // excluding metadata keys (same convention as ShipSelector)
  const cargo = shipData?.cargo ?? {};
  const metadataKeys = ['capacity', 'used', 'contents'];
  const cargoUsed = typeof cargo.used === 'number'
    ? cargo.used
    : Object.entries(cargo)
        .filter(([key, val]) => !metadataKeys.includes(key) && typeof val === 'number')
        .reduce((sum, [, val]) => sum + val, 0);
  const cargoCapacity = shipData?.cargo_capacity ?? 0;
  const cargoPct = cargoCapacity > 0 ? Math.max(0, Math.min(100, (cargoUsed / cargoCapacity) * 100)) : 0;

  // The repair endpoint requires the docked station to offer ship_repair
  const repairOffered = Boolean(stationServices.ship_repair);

  let repairBlockReason: string | null = null;
  if (!repairOffered) {
    repairBlockReason = 'This station does not offer hull repair';
  } else if (!shipData) {
    repairBlockReason = 'Reading ship telemetry...';
  } else if (totalMax <= 0) {
    // Escape pods / malformed combat dicts have no repairable systems;
    // without this branch the button enables with a "—" cost and the
    // click can only ever earn the server's 400.
    repairBlockReason = 'Ship has no repairable systems';
  } else if (atFullCondition) {
    repairBlockReason = 'Ship is at full condition';
  } else if (repairCost !== null && displayCredits < repairCost) {
    repairBlockReason = 'Insufficient credits';
  }

  return (
    <div className="venue-container services">
      <div className="venue-header">
        <button className="back-button" onClick={onBack}>
          ← Back to Hub
        </button>
        <h2>🔧 Ship Services</h2>
      </div>
      <div className="venue-content-area">
        {repairSuccess && (
          <div className="genesis-success-message">
            <span className="success-icon">✅</span>
            {repairSuccess}
          </div>
        )}
        {repairError && (
          <div className="genesis-error-message">
            <span className="error-icon">❌</span>
            {repairError}
          </div>
        )}

        <div className="services-grid">
          <div className="service-card">
            <div className="service-icon">🔧</div>
            <h3>Ship Repair</h3>
            <p>{shipData ? `Restore ${shipData.name}'s hull and shield integrity` : 'Restore hull and shield integrity'}</p>
            <div className="service-status">
              <div className="status-bar">
                <span className="bar-label">Hull</span>
                <div className="bar-track">
                  <div className="bar-fill" style={{ width: `${hullPct ?? 0}%` }}></div>
                </div>
                <span className="bar-value">{hullPct !== null ? `${Math.round(hullPct)}%` : '—'}</span>
              </div>
              <div className="status-bar">
                <span className="bar-label">Shields</span>
                <div className="bar-track">
                  <div className="bar-fill shield" style={{ width: `${shieldPct ?? 0}%` }}></div>
                </div>
                <span className="bar-value">{shieldPct !== null ? `${Math.round(shieldPct)}%` : '—'}</span>
              </div>
            </div>
            <div className="service-action">
              <span className="repair-cost">
                {repairCost === null
                  ? '—'
                  : atFullCondition
                    ? 'No repairs needed'
                    : formatCredits(repairCost)}
              </span>
              <button
                className="service-btn"
                onClick={repairShip}
                disabled={repairBusy || Boolean(repairBlockReason)}
                title={repairBlockReason ?? undefined}
              >
                {repairBusy ? 'Repairing...' : 'Full Repair'}
              </button>
            </div>
          </div>

          <div className="service-card">
            <div className="service-icon">🛠️</div>
            <h3>Maintenance</h3>
            <p>{shipData ? `${shipData.name}'s hull condition & servicing` : 'Hull condition & servicing'}</p>
            <div className="service-status">
              Ships degrade over time; low condition saps combat effectiveness. Service to restore it.
            </div>
            <div className="service-action">
              <button className="service-btn" onClick={() => setShowMaintenance(true)} disabled={!shipData}>
                Manage Maintenance
              </button>
            </div>
          </div>

          <div className="service-card">
            <div className="service-icon">📦</div>
            <h3>Cargo Hold</h3>
            <p>Current hold loading for {shipData?.name ?? 'your ship'}</p>
            <div className="service-status">
              <div className="status-bar">
                <span className="bar-label">Cargo</span>
                <div className="bar-track">
                  <div className="bar-fill" style={{ width: `${cargoPct}%` }}></div>
                </div>
                <span className="bar-value">{cargoCapacity > 0 ? `${Math.round(cargoPct)}%` : '—'}</span>
              </div>
            </div>
            <div className="cargo-info">
              <span>{cargoUsed.toLocaleString()} / {cargoCapacity.toLocaleString()} units</span>
            </div>
          </div>

          {stationServices.ship_upgrades ? (
            <div className="service-card">
              <div className="service-icon">📈</div>
              <h3>Ship Upgrades</h3>
              <p>{shipData ? `Refit ${shipData.name}: hull, shield, cargo & equipment` : 'Hull, shield, and cargo refits'}</p>
              <div className="service-status">
                Spend credits to raise ship subsystem levels or fit specialist equipment.
              </div>
              <div className="service-action">
                <button
                  className="service-btn"
                  onClick={() => setShowUpgrades(true)}
                  disabled={!shipData}
                >
                  Manage Upgrades
                </button>
              </div>
            </div>
          ) : (
            <div className="service-card unavailable">
              <div className="service-icon">📈</div>
              <h3>Ship Upgrades</h3>
              <p>Hull, shield, and cargo refits</p>
              <div className="service-unavailable-note">
                Upgrade bays are not operational at this station. New hulls
                can be commissioned at the Shipyard.
              </div>
              <div className="service-action">
                <span className="service-unavailable-badge">NOT AVAILABLE</span>
              </div>
            </div>
          )}

          {stationServices.insurance ? (
            <div className="service-card">
              <div className="service-icon">📜</div>
              <h3>Hull Insurance</h3>
              <p>{shipData ? `Insure ${shipData.name} against destruction` : 'Insure your ship against destruction'}</p>
              <div className="service-status">
                <div className="coverage-row">
                  Coverage: <strong>{insuranceTier ? (TIER_LABEL[insuranceTier] ?? insuranceTier) : '—'}</strong>
                </div>
                Pay a one-time premium; the registered owner is paid out if the hull is destroyed.
              </div>
              <div className="service-action">
                <button
                  className="service-btn"
                  onClick={() => setShowInsurance(true)}
                  disabled={!shipData}
                >
                  Manage Insurance
                </button>
              </div>
            </div>
          ) : (
            <div className="service-card unavailable">
              <div className="service-icon">📜</div>
              <h3>Hull Insurance</h3>
              <p>Protection against ship destruction</p>
              <div className="service-status">
                <div className="coverage-row">
                  Coverage: <strong>{insuranceTier ? (TIER_LABEL[insuranceTier] ?? insuranceTier) : '—'}</strong>
                </div>
              </div>
              <div className="service-unavailable-note">
                No underwriter currently operates at this station.
              </div>
              <div className="service-action">
                <span className="service-unavailable-badge">NOT AVAILABLE</span>
              </div>
            </div>
          )}
        </div>

        {showInsurance && shipData && (
          <div className="insurance-overlay" onClick={() => setShowInsurance(false)}>
            <div className="insurance-overlay-panel" onClick={(e) => e.stopPropagation()}>
              <InsuranceManager
                shipId={shipData.id}
                playerCredits={displayCredits}
                onChanged={() => { refreshPlayerState(); fetchShipData(); fetchInsuranceStatus(shipData.id); }}
                onClose={() => setShowInsurance(false)}
              />
            </div>
          </div>
        )}

        {showMaintenance && shipData && (
          <div className="maintenance-overlay" onClick={() => setShowMaintenance(false)}>
            <div className="maintenance-overlay-panel" onClick={(e) => e.stopPropagation()}>
              <MaintenanceManager
                shipId={shipData.id}
                playerCredits={displayCredits}
                onChanged={() => { refreshPlayerState(); fetchShipData(); }}
                onClose={() => setShowMaintenance(false)}
              />
            </div>
          </div>
        )}

        {showUpgrades && shipData && (
          <div
            className="insurance-overlay"
            onClick={() => { setShowUpgrades(false); refreshPlayerState(); fetchShipData(); }}
          >
            <div className="insurance-overlay-panel" style={{ position: 'relative' }} onClick={(e) => e.stopPropagation()}>
              <button
                className="ins-close"
                style={{ position: 'absolute', top: 12, right: 12, zIndex: 1 }}
                onClick={() => { setShowUpgrades(false); refreshPlayerState(); fetchShipData(); }}
                aria-label="Close ship upgrades"
              >
                ✕
              </button>
              <ModuleGridInterface
                ship={{ id: shipData.id }}
                playerCredits={displayCredits}
                onChanged={() => { refreshPlayerState(); fetchShipData(); }}
              />
            </div>
          </div>
        )}
      </div>
      {blackMarketButton}
    </div>
  );
};

export default ServicesVenue;
