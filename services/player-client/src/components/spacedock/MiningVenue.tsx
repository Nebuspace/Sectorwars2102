import React from 'react';
import './spacedock.css';

// =====================================================================
// Astral Mining — extracted verbatim from SpaceDockInterface's inline
// `renderMiningVenue()` closure (WO-UI3-VENUES sub-part #1, pure
// refactor — zero behavior change). All state/handlers remain owned by
// SpaceDockInterface and are threaded through as props here.
// =====================================================================

/** Tip GET /mining/licenses row — keys match list_player_licenses (LEG-435). */
export interface ClaimLicenseRow {
  id: string;
  region_id: string | null;
  sector_number: number;
  expires_at: string | null;
  purchased_at: string | null;
  cost_paid_cr: number;
  is_active: boolean;
}

interface MiningVenueProps {
  shipId: string | undefined;
  licenseBusy: boolean;
  licenseError: string | null;
  licenseSuccess: string | null;
  purchaseClaimLicense: () => void;
  licenses?: ClaimLicenseRow[];
  licensesLoading?: boolean;
  licensesError?: string | null;
  laserBusy: boolean;
  laserError: string | null;
  laserSuccess: string | null;
  upgradeMiningLaser: () => void;
  onBack: () => void;
  blackMarketButton: React.ReactNode;
}

const MiningVenue: React.FC<MiningVenueProps> = ({
  shipId,
  licenseBusy,
  licenseError,
  licenseSuccess,
  purchaseClaimLicense,
  licenses = [],
  licensesLoading = false,
  licensesError = null,
  laserBusy,
  laserError,
  laserSuccess,
  upgradeMiningLaser,
  onBack,
  blackMarketButton,
}) => {
  const hasShip = Boolean(shipId);
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
            <div className="license-list" data-testid="mining-license-list">
              {licensesLoading && (
                <div className="license-list-empty">Loading licenses…</div>
              )}
              {!licensesLoading && licensesError && (
                <div className="genesis-error-message">
                  <span className="error-icon">❌</span>
                  {licensesError}
                </div>
              )}
              {!licensesLoading && !licensesError && licenses.length === 0 && (
                <div className="license-list-empty">
                  No active or recently expired licenses.
                </div>
              )}
              {!licensesLoading && !licensesError && licenses.length > 0 && (
                <table className="license-list-table">
                  <caption>Active and recently expired claim licenses</caption>
                  <thead>
                    <tr>
                      <th scope="col">Sector</th>
                      <th scope="col">Expires</th>
                      <th scope="col">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {licenses.map((row) => (
                      <tr key={row.id} data-license-id={row.id}>
                        <td>{row.sector_number}</td>
                        <td>
                          {row.expires_at
                            ? new Date(row.expires_at).toLocaleString()
                            : '—'}
                        </td>
                        <td>{row.is_active ? 'Active' : 'Recently expired'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
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
            <p>Upgrade your installed Mining Laser to the next yield tier</p>
            <div className="service-status">
              A higher Mining Laser level raises ore yield, the precious-metals
              cap, and the quantum-shard trace drop. Requires a Mining Laser
              already fitted to your ship.
            </div>
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
              <button
                className="service-btn"
                onClick={upgradeMiningLaser}
                disabled={laserBusy || !hasShip}
                title={!hasShip ? 'No active ship' : undefined}
              >
                {laserBusy ? 'Refitting...' : 'Upgrade Mining Laser'}
              </button>
            </div>
          </div>
        </div>
      </div>
      {blackMarketButton}
    </div>
  );
};

export default MiningVenue;
