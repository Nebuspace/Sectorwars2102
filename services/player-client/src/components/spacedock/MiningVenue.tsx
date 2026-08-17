import React from 'react';
import './spacedock.css';

// =====================================================================
// Astral Mining — extracted from SpaceDockInterface's inline
// `renderMiningVenue()` closure (WO-UI3-VENUES). State/handlers remain
// owned by SpaceDockInterface and are threaded through as props.
// LEG-109: Install Mining Laser when none fitted; Upgrade when installed.
// Catalog install cost matches gameserver EQUIPMENT_DEFINITIONS.mining_laser.
// =====================================================================

/** Canon catalog cost for first Mining Laser fit (equipment_slots, not module grid). */
export const MINING_LASER_INSTALL_COST_CR = 35_000;

interface MiningVenueProps {
  shipId: string | undefined;
  /** From GET /player/current-ship — null/undefined when no Mining Laser fitted. */
  miningLaserLevel?: number | null;
  licenseBusy: boolean;
  licenseError: string | null;
  licenseSuccess: string | null;
  purchaseClaimLicense: () => void;
  laserBusy: boolean;
  laserError: string | null;
  laserSuccess: string | null;
  installMiningLaser: () => void;
  upgradeMiningLaser: () => void;
  onBack: () => void;
  blackMarketButton: React.ReactNode;
}

const MiningVenue: React.FC<MiningVenueProps> = ({
  shipId,
  miningLaserLevel,
  licenseBusy,
  licenseError,
  licenseSuccess,
  purchaseClaimLicense,
  laserBusy,
  laserError,
  laserSuccess,
  installMiningLaser,
  upgradeMiningLaser,
  onBack,
  blackMarketButton,
}) => {
  const hasShip = Boolean(shipId);
  const hasMiningLaser = miningLaserLevel != null;
  const installCostLabel = MINING_LASER_INSTALL_COST_CR.toLocaleString();

  return (
    <div className="venue-container mining">
      <div className="venue-header">
        <button className="back-button" onClick={onBack}>
          ← Back to Hub
        </button>
        <h2>⛏️ Astral Mining Consortium</h2>
      </div>
      <div className="venue-content-area">
        <div className="services-grid">
          <div className="service-card">
            <div className="service-icon">📜</div>
            <h3>Claim License</h3>
            <p>File a 24-hour Consortium claim for this sector's asteroid field</p>
            <div className="service-status">
              A claim license authorises legal harvesting in an asteroid-field
              sector. The fee scales with the field's richness; renewing an
              active claim costs less than a fresh filing.
            </div>
            {licenseSuccess && (
              <div className="genesis-success-message">
                <span className="success-icon">✅</span>
                {licenseSuccess}
              </div>
            )}
            {licenseError && (
              <div className="genesis-error-message">
                <span className="error-icon">❌</span>
                {licenseError}
              </div>
            )}
            <div className="service-action">
              <button
                className="service-btn"
                onClick={purchaseClaimLicense}
                disabled={licenseBusy || !hasShip}
                title={!hasShip ? 'No active ship' : undefined}
              >
                {licenseBusy ? 'Filing...' : 'Purchase / Renew License'}
              </button>
            </div>
          </div>

          <div className="service-card">
            <div className="service-icon">🔆</div>
            <h3>Mining Laser Refit</h3>
            {hasMiningLaser ? (
              <>
                <p>Upgrade your installed Mining Laser to the next yield tier</p>
                <div className="service-status">
                  Current level: {miningLaserLevel}. A higher Mining Laser level
                  raises ore yield, the precious-metals cap, and the quantum-shard
                  trace drop.
                </div>
              </>
            ) : (
              <>
                <p>Fit a Mining Laser so your ship can harvest asteroid fields</p>
                <div className="service-status">
                  Harvest requires a Mining Laser in an equipment slot (not the
                  deferred module-grid mining family). Catalog cost:{' '}
                  {installCostLabel} cr.
                </div>
              </>
            )}
            {laserSuccess && (
              <div className="genesis-success-message">
                <span className="success-icon">✅</span>
                {laserSuccess}
              </div>
            )}
            {laserError && (
              <div className="genesis-error-message">
                <span className="error-icon">❌</span>
                {laserError}
              </div>
            )}
            <div className="service-action">
              {hasMiningLaser ? (
                <button
                  className="service-btn"
                  onClick={upgradeMiningLaser}
                  disabled={laserBusy || !hasShip}
                  title={!hasShip ? 'No active ship' : undefined}
                >
                  {laserBusy ? 'Refitting...' : 'Upgrade Mining Laser'}
                </button>
              ) : (
                <button
                  className="service-btn"
                  onClick={installMiningLaser}
                  disabled={laserBusy || !hasShip}
                  title={!hasShip ? 'No active ship' : undefined}
                >
                  {laserBusy ? 'Installing...' : `Install Mining Laser (${installCostLabel} cr)`}
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
      {blackMarketButton}
    </div>
  );
};

export default MiningVenue;
