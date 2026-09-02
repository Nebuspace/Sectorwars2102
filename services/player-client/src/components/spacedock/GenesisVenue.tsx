import React from 'react';
import { formatCredits } from '../../utils/formatters';
import './spacedock.css';

// =====================================================================
// Genesis Store — extracted verbatim from SpaceDockInterface's inline
// `renderGenesisStore()` closure (WO-UI3-VENUES sub-part #1, pure
// refactor — zero behavior change). All state/handlers remain owned by
// SpaceDockInterface and are threaded through as props here.
// =====================================================================

export const GENESIS_VENUE_FALLBACK = 'Connection error. Please try again.';

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

/** Soft-ORDER invent=0 — Genesis purchase honesty (LEG-4070). */
export function formatGenesisVenueError(error: unknown, fallback: string): string {
  const status = httpStatus(error);
  let detailCopy: string | undefined;
  if (error && typeof error === 'object') {
    const raw = (error as { detail?: unknown }).detail;
    if (typeof raw === 'string' && raw.trim()) detailCopy = raw.trim();
    else if (Array.isArray(raw)) {
      const msgs = raw
        .map((e) =>
          e && typeof e === 'object' && typeof (e as { msg?: unknown }).msg === 'string'
            ? (e as { msg: string }).msg
            : null,
        )
        .filter((m): m is string => Boolean(m));
      if (msgs.length) detailCopy = msgs.join('; ');
    }
  }
  const message = error instanceof Error ? error.message : typeof error === 'string' ? error : undefined;
  const messageDetail =
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim()) &&
    !isNetworkCollapseMessage(message)
      ? message.trim()
      : undefined;
  const serverCopy = detailCopy ?? messageDetail;

  if (status === 403) {
    if (serverCopy) return serverCopy;
    return 'You do not have permission to purchase Genesis devices.';
  }
  if (status === 429) {
    return 'Genesis Store rate limit exceeded — wait a moment and try again.';
  }
  if (error instanceof TypeError) return fallback;
  if (typeof message === 'string' && isNetworkCollapseMessage(message)) return fallback;
  if (serverCopy) return serverCopy;
  return fallback;
}

interface GenesisVenueProps {
  shipName: string | undefined;
  shipType: string | undefined;
  currentGenesisDevices: number;
  maxGenesisDevices: number;
  genesisWeeklyRemaining: number | null;
  genesisWeeklyLimit: number;
  genesisRepGate: { required: number; current: number; met: boolean } | null;
  genesisSuccess: string | null;
  genesisError: string | null;
  genesisPurchasing: boolean;
  displayCredits: number;
  // Server-authoritative acquisition price (GET /genesis/available's
  // device_acquisition_cost). Null until it loads, or if the fetch fails/omits
  // the field — GRACEFUL-DEGRADE: render "—" and disable the purchase button
  // rather than guess a price (#139).
  genesisDevicePrice: number | null;
  purchaseGenesisDevice: () => void;
  onBack: () => void;
}

const GenesisVenue: React.FC<GenesisVenueProps> = ({
  shipName,
  shipType,
  currentGenesisDevices,
  maxGenesisDevices,
  genesisWeeklyRemaining,
  genesisWeeklyLimit,
  genesisRepGate,
  genesisSuccess,
  genesisError,
  genesisPurchasing,
  displayCredits,
  genesisDevicePrice,
  purchaseGenesisDevice,
  onBack,
}) => {
  const canHoldGenesis = maxGenesisDevices > 0;
  const hasCapacity = currentGenesisDevices < maxGenesisDevices;

  return (
    <div className="venue-container genesis">
      <div className="venue-header">
        <button className="back-button" onClick={onBack}>
          ← Back to Hub
        </button>
        <h2>🌍 Genesis Store</h2>
      </div>
      <div className="venue-content-area">
        <div className="genesis-intro">
          <div className="genesis-banner">
            <div className="banner-icon">🌍</div>
            <div className="banner-text">
              <h3>Create New Worlds</h3>
              <p>Genesis Devices are advanced terraforming technology that allow you to create new planets in empty sectors.</p>
            </div>
          </div>
        </div>

        {/* Ship Genesis Capacity Display */}
        <div className={`genesis-ship-status ${canHoldGenesis ? 'capable' : 'incapable'}`}>
          <div className="ship-genesis-header">
            <span className="ship-icon">🚀</span>
            <div className="ship-genesis-info">
              <h4>Your Ship: {shipName || 'Unknown'}</h4>
              {/* A default-named ship (e.g. "Defender") doubles its own type
                  ("Defender" / "DEFENDER") — drop the redundant line. */}
              {(!shipName || !shipType || shipName.toUpperCase() !== shipType.toUpperCase()) && (
                <span className="ship-type">{shipType || 'Unknown Type'}</span>
              )}
            </div>
          </div>
          {canHoldGenesis ? (
            <div className="genesis-capacity">
              <div className="capacity-display">
                <div className="genesis-orbs">
                  {Array.from({ length: maxGenesisDevices }, (_, i) => (
                    <div
                      key={i}
                      className={`genesis-orb ${i < currentGenesisDevices ? 'filled' : 'empty'}`}
                      title={i < currentGenesisDevices ? 'Genesis Device Loaded' : 'Empty Slot'}
                    >
                      {i < currentGenesisDevices ? '🌍' : '⭕'}
                    </div>
                  ))}
                </div>
                <div className="capacity-text">
                  <span className="count">{currentGenesisDevices} / {maxGenesisDevices}</span>
                  <span className="label">Genesis Devices</span>
                </div>
              </div>
              {currentGenesisDevices > 0 && (
                <div className="genesis-power-indicator">
                  <span className="power-glow">✨</span>
                  <span className="power-text">World-Creating Power Ready</span>
                  <span className="power-glow">✨</span>
                </div>
              )}
            </div>
          ) : (
            <div className="genesis-incapable-warning">
              <span className="warning-icon">⚠️</span>
              <span>This ship cannot carry Genesis Devices. You need a Cargo Hauler, Defender, Colony Ship, Carrier, or Warp Jumper.</span>
            </div>
          )}
        </div>

        {/* Success/Error Messages */}
        {genesisSuccess && (
          <div className="genesis-success-message">
            <span className="success-icon">✅</span>
            {genesisSuccess}
          </div>
        )}
        {genesisError && (
          <div className="genesis-error-message" role="alert" aria-live="polite" aria-atomic="true">
            <span className="error-icon">❌</span>
            {genesisError}
          </div>
        )}

        <div className="genesis-devices-grid single">
          <div className="genesis-device-card device">
            <div className="device-header">
              <span className="device-tier">Genesis Device</span>
              <div className="device-icon">🌍</div>
            </div>
            <div className="device-details">
              <h3>Genesis Device</h3>
              <ul className="device-specs">
                <li>🔩 Stored on your ship; fuse 1 (Basic), 3 (Enhanced), or 1 + your Colony Ship (Advanced)</li>
                <li>🪐 Tier &amp; biome are chosen when you deploy — not now</li>
                <li>💳 Sequence cost (25k / 75k / 250k) is paid at deploy</li>
                <li>📅 {genesisWeeklyRemaining !== null ? `${genesisWeeklyRemaining} of ${genesisWeeklyLimit} acquisitions left this week` : `Limited to ${genesisWeeklyLimit} per week`}</li>
                {genesisRepGate && !genesisRepGate.met && (
                  <li id="genesis-rep-gate-note" className="genesis-rep-gate-note">
                    🎖️ Requires Heroic Federation standing (≥{genesisRepGate.required}) — you&apos;re at {genesisRepGate.current}
                  </li>
                )}
              </ul>
            </div>
            <div className="device-footer">
              <div className="device-price">{genesisDevicePrice != null ? formatCredits(genesisDevicePrice) : '—'}</div>
              <button
                className="purchase-device-btn"
                onClick={() => purchaseGenesisDevice()}
                disabled={
                  genesisPurchasing
                  || genesisDevicePrice == null
                  || displayCredits < genesisDevicePrice
                  || !canHoldGenesis
                  || !hasCapacity
                  || genesisWeeklyRemaining === 0
                  || Boolean(genesisRepGate && !genesisRepGate.met)
                }
                aria-describedby={genesisRepGate && !genesisRepGate.met ? 'genesis-rep-gate-note' : undefined}
              >
                {genesisPurchasing ? 'Acquiring…'
                  : genesisDevicePrice == null ? 'Price Unavailable'
                  : !canHoldGenesis ? 'Ship Incompatible'
                  : !hasCapacity ? 'Ship At Capacity'
                  : genesisRepGate && !genesisRepGate.met ? 'Reputation Too Low'
                  : genesisWeeklyRemaining === 0 ? 'Weekly Limit Reached'
                  : 'Acquire Device'}
              </button>
            </div>
          </div>
        </div>

        <div className="genesis-info">
          <h4>📋 How it works</h4>
          <ul>
            <li>Acquire devices here (max {genesisWeeklyLimit}/week), then fly to an <strong>empty sector</strong> to deploy.</li>
            <li>Choose the tier at deploy: <strong>Basic</strong> (1 device), <strong>Enhanced</strong> (3 devices), or <strong>Advanced</strong> (1 device + sacrifice a Colony Ship for an instant colony).</li>
            <li>Carry capacity depends on your hull (Cargo Hauler 2, Defender 3, Colony Ship / Carrier 5, Warp Jumper 1).</li>
          </ul>
        </div>
      </div>
    </div>
  );
};

export default GenesisVenue;
