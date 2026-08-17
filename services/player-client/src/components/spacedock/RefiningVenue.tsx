import React, { useEffect } from 'react';
import { useGame } from '../../contexts/GameContext';
import CrystalRefiningPanel from '../quantum/CrystalRefiningPanel';
import '../quantum/quantum-drive.css';
import './spacedock.css';

/**
 * SpaceDock Refining Facility venue (LEG-79 / WO-UIPC-REFINING).
 * Reuses CrystalRefiningPanel (LEG-42) which calls /api/v1/refining/* —
 * DISTINCT from /quantum/refine-charge (Shard→jump Charge).
 * Class-3+/SpaceDock (and Lumen Class-5+) gates are server-enforced;
 * this venue surfaces server {detail} errors honestly.
 */

interface RefiningVenueProps {
  onBack: () => void;
}

const RefiningVenue: React.FC<RefiningVenueProps> = ({ onBack }) => {
  const { playerState, quantumStatus, refreshQuantumStatus } = useGame();

  useEffect(() => {
    void refreshQuantumStatus?.();
  }, [refreshQuantumStatus]);

  const shards = quantumStatus?.quantum_shards ?? 0;
  const crystals = quantumStatus?.quantum_crystals ?? 0;
  const isDocked = Boolean(playerState?.is_docked);

  return (
    <div className="venue-container refining" data-testid="refining-venue">
      <div className="venue-header">
        <button type="button" className="back-button" onClick={onBack}>
          ← Back to Hub
        </button>
        <h2>🏭 Refining Facility</h2>
      </div>
      <div className="venue-content-area">
        <p className="service-status" style={{ marginBottom: '1rem' }}>
          Convert Quantum Shards into Crystals at this station. Instant Crystal refine
          (5 shards + 10,000 cr) and timed Lumen refine (100 shards + 10,000 cr / 12h)
          use the live refining routes — not drive-charge conversion.
        </p>
        <CrystalRefiningPanel
          shards={shards}
          crystals={crystals}
          isDocked={isDocked}
          onBalancesChanged={refreshQuantumStatus}
        />
      </div>
    </div>
  );
};

export default RefiningVenue;
