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
    type: 'terran',
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
    type: 'oceanic',
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
    type: 'mountainous',
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
    type: 'desert',
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
    type: 'frozen',
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
  const { currentShip, currentSector, updateShipGenesis } = useGame();
  const [planetName, setPlanetName] = useState('');
  // Default the target to the player's current sector — you deploy where your
  // ship is. The deploy API validates that the sector is empty/eligible. The
  // player can still override with another sector id.
  const currentSectorId = currentSector?.sector_id != null ? String(currentSector.sector_id) : '';
  const [selectedSectorId, setSelectedSectorId] = useState(currentSectorId);
  const [deploying, setDeploying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const genesisDevices = currentShip?.genesis_devices ?? 0;

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

    if (genesisDevices <= 0) {
      setError('No Genesis Devices available');
      return;
    }

    try {
      setDeploying(true);
      setError(null);
      setSuccessMessage(null);

      const response = await gameAPI.planetary.deployGenesis(
        selectedSectorId.trim(),
        planetName.trim()
      );

      if (response.success) {
        updateShipGenesis(response.genesisDevicesRemaining);
        setSuccessMessage(`Genesis Device deployed! ${planetName} will be ready in ${Math.floor(response.deploymentTime / 60)} minutes.`);
        
        // Clear form
        setPlanetName('');
        setSelectedSectorId('');
        
        // Notify parent
        if (onSuccess) {
          onSuccess(response.planetId);
        }

        // Close after success message
        setTimeout(() => {
          if (onClose) onClose();
        }, 3000);
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
        <div className="device-status">
          <div className="status-item">
            <span className="status-label">Genesis Devices Available:</span>
            <span className={`status-value ${genesisDevices === 0 ? 'empty' : ''}`}>
              {genesisDevices}
            </span>
          </div>
          <div className="status-item">
            <span className="status-label">Deployment Time:</span>
            <span className="status-value">5 minutes</span>
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
            <p>You have no Genesis Devices available. Purchase more from specialized ports or complete faction missions to earn them.</p>
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
                  <span className="summary-value">~48 hours (invulnerable)</span>
                </div>
              </div>
            </div>

            <div className="action-buttons">
              <button
                className="button secondary"
                onClick={onClose}
                disabled={deploying}
              >
                Cancel
              </button>
              <button
                className="button primary"
                onClick={handleDeploy}
                disabled={deploying || !planetName || !selectedSectorId}
              >
                {deploying ? 'Deploying...' : 'Deploy Genesis Device'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
};