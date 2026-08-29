/**
 * Live limpet tracker readout — consumes WebSocket lastLimpetSignal.
 */
import React from 'react';
import { useWebSocket } from '../../contexts/WebSocketContext';

export const LimpetTrackerReadout: React.FC = () => {
  const { lastLimpetSignal, limpetSignalEventSignal } = useWebSocket();

  return (
    <div className="threat-section" data-testid="limpet-tracker" data-signal={limpetSignalEventSignal}>
      <div className="threat-section-title" role="heading" aria-level={3}>
        LIMPET TRACKER
      </div>
      {lastLimpetSignal ? (
        <div className="threat-law-clean" role="status" data-testid="limpet-tracker-fix">
          Sector {lastLimpetSignal.sector_id ?? '—'}
          {lastLimpetSignal.tracked_ship_id
            ? ` · ship ${lastLimpetSignal.tracked_ship_id}`
            : ''}
        </div>
      ) : (
        <div className="empty-state" role="status">
          No active limpet signal
        </div>
      )}
    </div>
  );
};

export default LimpetTrackerReadout;
