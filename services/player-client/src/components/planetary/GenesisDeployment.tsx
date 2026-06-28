import React, { useState, useEffect } from 'react';
import { gameAPI } from '../../services/api';
import { useGame } from '../../contexts/GameContext';
import type { PlanetType, GenesisDeployment as GenesisDeploymentType } from '../../types/planetary';
import './genesis-deployment.css';

interface GenesisDeploymentProps {
  onSuccess?: (planetId: string) => void;
  onClose?: () => void;
}

interface PlanetTypeInfo {
  type: PlanetType;
  name: string;
  icon: string;
  description: string;
  characteristics: string[];
  maxColonists: number;
  productionBonuses: {
    fuel: number;
    organics: number;
    equipment: number;
  };
}

const PLANET_TYPES: PlanetTypeInfo[] = [
  {
    type: 'TERRAN',
    name: 'Terran',
    icon: '🌍',
    description: 'Earth-like planets with balanced resources and high habitability',
    characteristics: [
      'Balanced resource production',
      'High maximum population',
      'Ideal for general colonies',
      'Good defensive positions'
    ],
    maxColonists: 100000,
    productionBonuses: { fuel: 1.0, organics: 1.0, equipment: 1.0 }
  },
  {
    type: 'OCEANIC',
    name: 'Oceanic',
    icon: '🌊',
    description: 'Water-covered worlds rich in organic resources',
    characteristics: [
      'Excellent organics production',
      'Limited equipment output',
      'Moderate population capacity',
      'Natural shield advantages'
    ],
    maxColonists: 75000,
    productionBonuses: { fuel: 0.8, organics: 1.5, equipment: 0.7 }
  },
  {
    type: 'MOUNTAINOUS',
    name: 'Mountainous',
    icon: '⛰️',
    description: 'Rocky planets abundant in minerals and fuel',
    characteristics: [
      'High fuel production',
      'Excellent equipment output',
      'Lower population limits',
      'Natural fortress terrain'
    ],
    maxColonists: 50000,
    productionBonuses: { fuel: 1.4, organics: 0.6, equipment: 1.3 }
  },
  {
    type: 'DESERT',
    name: 'Desert',
    icon: '🏜️',
    description: 'Arid worlds with concentrated mineral deposits',
    characteristics: [
      'Superior fuel extraction',
      'Limited organics production',
      'Harsh living conditions',
      'Hidden resource caches'
    ],
    maxColonists: 40000,
    productionBonuses: { fuel: 1.6, organics: 0.4, equipment: 1.1 }
  },
  {
    type: 'ICE',
    name: 'Frozen',
    icon: '❄️',
    description: 'Ice-covered planets with unique research opportunities',
    characteristics: [
      'Research bonus potential',
      'Reduced production rates',
      'Challenging environment',
      'Defensive ice barriers'
    ],
    maxColonists: 35000,
    productionBonuses: { fuel: 0.7, organics: 0.8, equipment: 0.9 }
  }
];

export const GenesisDeployment: React.FC<GenesisDeploymentProps> = ({ 
  onSuccess,
  onClose 
}) => {
  const { currentShip, currentSector, updateShipGenesis, playerState } = useGame();
  const [planetName, setPlanetName] = useState('');
  // Default the target to the player's current sector — you deploy where your
  // ship is. The deploy API validates that the sector is empty/eligible. The
  // player can still override with another sector id.
  const currentSectorId = currentSector?.sector_id != null ? String(currentSector.sector_id) : '';
  const [selectedSectorId, setSelectedSectorId] = useState(currentSectorId);
  const [deploying, setDeploying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  // Tier: basic fuses 1 device, enhanced fuses 3, advanced sacrifices the
  // Colony Ship for an instant Settlement colony (canon).
  const [tier, setTier] = useState<'basic' | 'enhanced' | 'advanced'>('basic');
  // Registration controls the new world's registry visibility + Fed legal status.
  // Default 'registered' (on the charts in your name, no Fed protection).
  const [registration, setRegistration] = useState<'clandestine' | 'registered' | 'chartered'>('registered');
  // Holds the new colony's name while the deploy animation plays.
  const [deployAnim, setDeployAnim] = useState<string | null>(null);
  // Two-step confirm guard for the destructive advanced (ship-sacrifice) tier.
  const [advancedArmed, setAdvancedArmed] = useState(false);

  const genesisDevices = currentShip?.genesis_devices ?? 0;

  const isColonyShip = (currentShip?.type || '').toUpperCase() === 'COLONY_SHIP';

  // Canon tiers (genesis-devices.md): basic fuses 1 device, enhanced fuses 3,
  // advanced spends 1 device + sacrifices the Colony Ship for an instant colony.
  const TIERS = [
    { id: 'basic' as const, label: 'Basic', devices: 1, cost: 25000, hab: '40–60', blurb: 'One device · a starter world (forms ~48h)', sacrifice: false },
    { id: 'enhanced' as const, label: 'Enhanced', devices: 3, cost: 75000, hab: '55–75', blurb: 'Three devices fused · a richer world (forms ~48h)', sacrifice: false },
    { id: 'advanced' as const, label: 'Advanced', devices: 1, cost: 250000, hab: '70–90', blurb: 'Sacrifices your Colony Ship · INSTANT Settlement colony (5,000 colonists, L2 citadel, 4 turrets)', sacrifice: true },
  ];
  const tierInfo = TIERS.find(t => t.id === tier)!;
  const tierEligible = (t: typeof TIERS[number]) =>
    genesisDevices >= t.devices && (!t.sacrifice || isColonyShip);

  // Registration fees (the server is authoritative; these mirror the fee contract
  // so the player sees the cost before committing).
  //   Registered  = 10,000
  //   Clandestine = 60,000
  //   Chartered   = 10,000 + 40,000 * (1 - clamp(rep/1000, 0, 1) * 0.75)
  // Chartered scales DOWN with reputation: high standing earns a cheaper charter.
  const personalReputation = playerState?.personal_reputation ?? 0;
  const charteredFee = Math.round(
    10000 + 40000 * (1 - Math.max(0, Math.min(1, personalReputation / 1000)) * 0.75)
  );
  const REGISTRATIONS = [
    {
      id: 'clandestine' as const,
      label: 'Clandestine',
      fee: 60000,
      blurb: 'Off the registry — no Federation protection. Stays hidden from lookups.',
    },
    {
      id: 'registered' as const,
      label: 'Registered',
      fee: 10000,
      blurb: 'On the charts in your name — no Federation protection.',
    },
    {
      id: 'chartered' as const,
      label: 'Chartered',
      fee: charteredFee,
      blurb: 'Federation legal protection — fee scales down with your reputation.',
    },
  ];
  const registrationInfo = REGISTRATIONS.find(r => r.id === registration)!;
  const totalCost = tierInfo.cost + registrationInfo.fee;

  // Fall back to basic if the selected tier becomes ineligible.
  useEffect(() => {
    if (!tierEligible(tierInfo)) { setTier('basic'); setAdvancedArmed(false); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tier, genesisDevices, isColonyShip]);

  useEffect(() => {
    // Keep the default in sync if the player moves while the panel is open.
    if (currentSectorId) setSelectedSectorId(prev => prev || currentSectorId);
  }, [currentSectorId]);

  const validatePlanetName = (name: string): boolean => {
    // Basic validation
    if (name.length < 3) return false;
    if (name.length > 30) return false;
    if (!/^[a-zA-Z0-9\s\-']+$/.test(name)) return false;
    return true;
  };

  const handleDeploy = async () => {
    // Validation
    if (!planetName.trim()) {
      setError('Please enter a planet name');
      return;
    }

    if (!validatePlanetName(planetName)) {
      setError('Planet name must be 3-30 characters and contain only letters, numbers, spaces, hyphens, and apostrophes');
      return;
    }

    if (!selectedSectorId) {
      setError('Please select a target sector');
      return;
    }

    if (genesisDevices < tierInfo.devices) {
      setError(`The ${tierInfo.label} sequence needs ${tierInfo.devices} device${tierInfo.devices !== 1 ? 's' : ''} — you have ${genesisDevices}.`);
      return;
    }

    if (tierInfo.sacrifice && !isColonyShip) {
      setError('Advanced genesis requires a Colony Ship to sacrifice.');
      return;
    }

    // Two-step confirm for the destructive advanced (ship-sacrifice) tier.
    if (tierInfo.sacrifice && !advancedArmed) {
      setAdvancedArmed(true);
      return;
    }

    try {
      setDeploying(true);
      setError(null);
      setSuccessMessage(null);

      const deployedName = planetName.trim();
      const wasSacrifice = tierInfo.sacrifice;
      const response = await gameAPI.planetary.deployGenesis(
        selectedSectorId.trim(),
        deployedName,
        tier,
        registration
      );

      if (response.success) {
        updateShipGenesis(response.genesisDevicesRemaining);
        setAdvancedArmed(false);
        // Play the genesis formation animation, then surface the success line.
        setDeployAnim(deployedName);
        setTimeout(() => {
          setSuccessMessage(
            wasSacrifice
              ? `Colony Ship sacrificed — ${deployedName} is established instantly at Settlement level. You've ejected to an escape pod.`
              : `Genesis sequence initiated — ${deployedName} is forming (~48h). It will appear in your Colonial Registry when ready.`
          );
          setDeployAnim(null);
        }, 2800);

        // Clear form
        setPlanetName('');
        setSelectedSectorId('');

        // Notify parent
        if (onSuccess) {
          onSuccess(response.planetId);
        }

        // Close after the animation + success message
        setTimeout(() => {
          if (onClose) onClose();
        }, 5200);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to deploy Genesis Device');
    } finally {
      setDeploying(false);
    }
  };

  const handlePlanetNameChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    setPlanetName(value);
    
    // Clear error when user starts typing
    if (error && error.includes('planet name')) {
      setError(null);
    }
  };

  return (
    <div className="genesis-deployment">
      <div className="deployment-header">
        <h3>Deploy Genesis Device</h3>
        <button className="close-button" onClick={onClose}>✕</button>
      </div>

      <div className="deployment-content">
        {deployAnim && (
          <div className="genesis-anim-stage" role="img" aria-label={`Genesis sequence forming ${deployAnim}`}>
            <span className="genesis-anim-shock" />
            <span className="genesis-anim-shock genesis-anim-shock-2" />
            <span className="genesis-anim-core" />
            <span className="genesis-anim-planet" />
            <div className="genesis-anim-label">
              <span className="genesis-anim-name">{deployAnim}</span>
              <span className="genesis-anim-status">GENESIS SEQUENCE INITIATED</span>
            </div>
          </div>
        )}

        <div className="device-status">
          <div className="status-item">
            <span className="status-label">Genesis Devices Available:</span>
            <span className={`status-value ${genesisDevices === 0 ? 'empty' : ''}`}>
              {genesisDevices}
            </span>
          </div>
          <div className="status-item">
            <span className="status-label">Formation Time:</span>
            <span className="status-value">~48 hours</span>
          </div>
        </div>

        {error && (
          <div className="error-message">
            <span className="error-icon">⚠️</span>
            {error}
          </div>
        )}

        {successMessage && (
          <div className="success-message">
            <span className="success-icon">✅</span>
            {successMessage}
          </div>
        )}

        {genesisDevices === 0 ? (
          <div className="no-devices-warning">
            <span className="warning-icon">⚠️</span>
            <p>You have no Genesis Devices available. Purchase more from specialized ports.</p>
          </div>
        ) : (
          <>
            <div className="deployment-form">
              <div className="form-section">
                <label htmlFor="planet-name">Planet Name</label>
                <input
                  id="planet-name"
                  type="text"
                  value={planetName}
                  onChange={handlePlanetNameChange}
                  placeholder="Enter planet name..."
                  maxLength={30}
                  className={error && error.includes('planet name') ? 'error' : ''}
                />
                <span className="input-hint">3-30 characters, letters, numbers, spaces, hyphens, and apostrophes only</span>
              </div>

              <div className="form-section">
                <label>Genesis Sequence</label>
                <div className="genesis-tier-select">
                  {TIERS.map(t => {
                    const eligible = tierEligible(t);
                    const reason = genesisDevices < t.devices
                      ? `Needs ${t.devices} device${t.devices !== 1 ? 's' : ''} (you have ${genesisDevices})`
                      : (t.sacrifice && !isColonyShip ? 'Requires a Colony Ship to sacrifice' : t.blurb);
                    return (
                      <button
                        type="button"
                        key={t.id}
                        className={`genesis-tier-card ${tier === t.id ? 'selected' : ''} ${t.sacrifice ? 'sacrifice' : ''}`}
                        disabled={!eligible}
                        title={reason}
                        onClick={() => { setTier(t.id); setAdvancedArmed(false); }}
                      >
                        <span className="tier-name">{t.label}</span>
                        <span className="tier-devices">{t.sacrifice ? '1 device + ship' : `${t.devices} device${t.devices !== 1 ? 's' : ''}`}</span>
                        <span className="tier-meta">{t.cost.toLocaleString()} cr · hab {t.hab}</span>
                      </button>
                    );
                  })}
                </div>
                {tierInfo.sacrifice ? (
                  <span className="input-hint genesis-sacrifice-warn">⚠️ Advanced SACRIFICES your Colony Ship ({currentShip?.name || 'current hull'}) — you eject to an escape pod. In exchange the colony is built instantly at Settlement level (no 48h wait).</span>
                ) : (
                  <span className="input-hint">Fuse more devices for a richer world. You have {genesisDevices} loaded{isColonyShip ? '; Advanced sacrifices this Colony Ship for an instant colony' : ''}.</span>
                )}
              </div>

              <div className="form-section genesis-registration-section">
                <label>Registration</label>
                <div className="genesis-tier-select genesis-registration-select">
                  {REGISTRATIONS.map(r => (
                    <button
                      type="button"
                      key={r.id}
                      className={`genesis-tier-card genesis-registration-card ${registration === r.id ? 'selected' : ''}`}
                      title={r.blurb}
                      onClick={() => setRegistration(r.id)}
                    >
                      <span className="tier-name">{r.label}</span>
                      <span className="tier-meta">{r.fee.toLocaleString()} cr</span>
                      <span className="registration-blurb">{r.blurb}</span>
                    </button>
                  ))}
                </div>
                <span className="input-hint">
                  {registration === 'chartered'
                    ? `Chartered fee scales with reputation (yours: ${personalReputation >= 0 ? '+' : ''}${personalReputation}). The final charge is confirmed by the Federation registry.`
                    : registration === 'clandestine'
                      ? 'A clandestine world stays off the public registry — no one can look it up, but the Federation will not protect it.'
                      : 'A registered world appears on the charts under your name. The Federation does not protect registered worlds.'}
                </span>
              </div>

              <div className="form-section">
                <label htmlFor="sector-select">Target Sector</label>
                <input
                  id="sector-select"
                  type="text"
                  value={selectedSectorId}
                  onChange={(e) => setSelectedSectorId(e.target.value)}
                  placeholder="Enter sector number..."
                  className={error && error.includes('sector') ? 'error' : ''}
                />
                <span className="input-hint">
                  {currentSectorId
                    ? `Defaults to your current sector (${currentSectorId}). The target must be empty — undock and fly to an empty sector to seed a world.`
                    : 'Enter the number of an empty sector. Navigate to an empty sector first.'}
                </span>
              </div>
            </div>

            <div className="genesis-biome-note">
              <span className="biome-icon">🌍</span>
              <p>The genesis process forms the world over ~48 hours; its <strong>biome is determined by the device</strong> (higher tiers bias toward richer worlds). The planet is invulnerable while it forms, then appears in your Colonial Registry.</p>
            </div>

            <div className="deployment-summary">
              <h4>Deployment Summary</h4>
              <div className="summary-grid">
                <div className="summary-item">
                  <span className="summary-label">Planet Name:</span>
                  <span className="summary-value">{planetName || 'Not set'}</span>
                </div>
                <div className="summary-item">
                  <span className="summary-label">Sequence:</span>
                  <span className="summary-value">{tierInfo.label} — {tierInfo.devices} device{tierInfo.devices !== 1 ? 's' : ''} · {tierInfo.cost.toLocaleString()} cr</span>
                </div>
                <div className="summary-item">
                  <span className="summary-label">Registration:</span>
                  <span className="summary-value">{registrationInfo.label} · {registrationInfo.fee.toLocaleString()} cr</span>
                </div>
                <div className="summary-item">
                  <span className="summary-label">Biome:</span>
                  <span className="summary-value">Determined by the genesis device</span>
                </div>
                <div className="summary-item">
                  <span className="summary-label">Target Sector:</span>
                  <span className="summary-value">
                    {selectedSectorId ? `Sector ${selectedSectorId}` : 'Not selected'}
                  </span>
                </div>
                <div className="summary-item">
                  <span className="summary-label">Formation:</span>
                  <span className="summary-value">{tierInfo.sacrifice ? 'Instant — Settlement level' : '~48 hours (invulnerable)'}</span>
                </div>
                <div className="summary-item summary-total">
                  <span className="summary-label">Total Cost:</span>
                  <span className="summary-value">{totalCost.toLocaleString()} cr</span>
                </div>
              </div>
            </div>

            <div className="action-buttons">
              <button
                className="button secondary"
                onClick={() => { if (advancedArmed) { setAdvancedArmed(false); } else { onClose && onClose(); } }}
                disabled={deploying}
              >
                {advancedArmed ? 'Back' : 'Cancel'}
              </button>
              <button
                className={`button primary ${tierInfo.sacrifice && advancedArmed ? 'danger' : ''}`}
                onClick={handleDeploy}
                disabled={deploying || !!deployAnim || !planetName || !selectedSectorId || !tierEligible(tierInfo)}
              >
                {deploying
                  ? 'Deploying...'
                  : tierInfo.sacrifice
                    ? (advancedArmed ? `⚠️ Confirm — sacrifice ${currentShip?.name || 'Colony Ship'}` : 'Deploy Advanced (sacrifices ship)')
                    : `Deploy ${tierInfo.label} (${tierInfo.devices} device${tierInfo.devices !== 1 ? 's' : ''})`}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
};