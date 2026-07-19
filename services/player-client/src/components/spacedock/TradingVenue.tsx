import React from 'react';
import TradingInterface from '../trading/TradingInterface';
import './spacedock.css';

// =====================================================================
// Trading Hub — extracted verbatim from SpaceDockInterface's inline
// `renderTrading()` closure (WO-UI3-VENUES sub-part #1, pure refactor —
// zero behavior change). All state/handlers remain owned by
// SpaceDockInterface and are threaded through as props here.
// =====================================================================

interface TradingVenueProps {
  onBack: () => void;
  blackMarketButton: React.ReactNode;
}

const TradingVenue: React.FC<TradingVenueProps> = ({ onBack, blackMarketButton }) => (
  <div className="venue-container trading">
    <div className="venue-header">
      <button className="back-button" onClick={onBack}>
        ← Back to Hub
      </button>
      <h2>🏪 Trading Hub</h2>
    </div>
    <div className="venue-content-area trading-venue">
      <TradingInterface onClose={() => {}} />
    </div>
    {blackMarketButton}
  </div>
);

export default TradingVenue;
