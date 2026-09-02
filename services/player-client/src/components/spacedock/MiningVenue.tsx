import React from 'react';
import './spacedock.css';

// =====================================================================
// Astral Mining — extracted from SpaceDockInterface's inline
// `renderMiningVenue()` closure (WO-UI3-VENUES). State/handlers remain
// owned by SpaceDockInterface and are threaded through as props.
// LEG-1226 / LEG-109: Install Mining Laser when none fitted; Upgrade when
// installed. Catalog install cost matches gameserver EQUIPMENT_DEFINITIONS.
// ModuleGrid mining-family ladder UI remains Design-only.
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

/** Canon catalog cost for first Mining Laser fit (equipment_slots, not module grid). */
export const MINING_LASER_INSTALL_COST_CR = 35_000;

export const MINING_VENUE_FALLBACK = 'Connection error. Please try again.';

/** Transport collapse copy is not gameserver detail (Soft-ORDER densify). */
const isNetworkCollapseMessage = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed)
  );
};

function httpStatus(err: unknown): number | undefined {
  if (err && typeof err === 'object') {
    const direct = (err as { status?: number }).status;
    if (typeof direct === 'number') return direct;
    const resp = (err as { response?: { status?: number } }).response;
    if (typeof resp?.status === 'number') return resp.status;
  }
  return undefined;
}

/** Soft-ORDER invent=0 — 403/429 + TypeError densify for Astral Mining API paths (LEG-4067). */
export function formatMiningVenueError(error: unknown, fallback: string): string {
  const status = httpStatus(error);
  const message =
    error instanceof Error
      ? error.message
      : typeof error === 'string'
        ? error
        : undefined;
  const hasServerDetail =
    !(error instanceof TypeError) &&
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim()) &&
    !isNetworkCollapseMessage(message);

  if (status === 403) {
    if (hasServerDetail) return message!.trim();
    return 'You do not have permission to use Astral Mining services.';
  }

  if (status === 429) {
    return 'Astral Mining rate limit exceeded — wait a moment and try again.';
  }

  if (error instanceof TypeError) return fallback;
  if (error instanceof Error && error.message) {
    if (isNetworkCollapseMessage(error.message)) return fallback;
    return error.message;
  }
  if (typeof error === 'string') {
    if (isNetworkCollapseMessage(error)) return fallback;
    return error;
  }
  return fallback;
}

interface MiningVenueProps {
  shipId: string | undefined;
  /** From GET /player/current-ship — null/undefined when no Mining Laser fitted. */
  miningLaserLevel?: number | null;
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
  licenses = [],
  licensesLoading = false,
  licensesError = null,
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
