import React, { useState, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useGame, StationSlips } from '../../contexts/GameContext';
import { useWebSocket } from '../../contexts/WebSocketContext';
import GameLayout from '../layouts/GameLayout';
import { StationClassBadge, getTraderPersonality } from '../common/stationIdentity';
import './trading-interface.css';

interface Resource {
  name: string;
  quantity: number;
  buy_price: number;
  sell_price: number;
  station_buys: boolean;
  station_sells: boolean;
  last_updated?: string;
}

interface TradeCalculation {
  resourceType: string;
  quantity: number;
  unitPrice: number;
  totalCost: number;
  isAffordable: boolean;
  fitsInCargo: boolean;
}

interface BumpableOccupant {
  player_id: string;
  name: string;
  tenure_hours: number;
}

// Payload surfaced by GameContext.dockAtStation when the station's
// transient slips are all occupied (HTTP 409 — requester auto-enqueued)
interface DockFullInfo {
  stationId: string;
  stationName: string;
  detail?: string;
  slips: { capacity: number; occupied: number };
  queue_position?: number | null;
  bumpable: BumpableOccupant[];
  bump_cost: number;
}

const formatName = (name: string) => name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

interface TradingInterfaceProps {
  onClose?: () => void;
}

const TradingInterface: React.FC<TradingInterfaceProps> = ({ onClose }) => {
  // Wrap in GameLayout when standalone (no onClose prop = used as a route).
  // When embedded as a modal (onClose provided), render bare so the parent's
  // shell isn't duplicated inside the modal.
  const isStandalone = !onClose;
  const Wrapper = isStandalone ? GameLayout : React.Fragment;

  const {
    playerState,
    currentShip,
    marketInfo,
    getMarketInfo,
    buyResource,
    sellResource,
    dockAtStation,
    getStationSlips,
    bumpDockOccupant,
    stationsInSector,
    isLoading,
    error
  } = useGame();

  // Use stationsInSector as ports (they're the same concept in this game)
  const portsInSector = stationsInSector || [];

  const { addNotification, isConnected } = useWebSocket();

  // All hooks must be called before any early returns
  const [selectedPort, setSelectedPort] = useState<string>('');
  const [selectedResource, setSelectedResource] = useState<string>('');
  const [tradeQuantity, setTradeQuantity] = useState<number>(1);
  const [tradeMode, setTradeMode] = useState<'buy' | 'sell'>('buy');
  const [tradeCalculation, setTradeCalculation] = useState<TradeCalculation | null>(null);
  const [showConfirmDialog, setShowConfirmDialog] = useState(false);
  const [dockingStationId, setDockingStationId] = useState<string | null>(null);

  // Transient slip availability per station (lazy, fetched when the
  // undocked port list renders)
  const [slipsByStation, setSlipsByStation] = useState<Record<string, StationSlips>>({});
  const [dockFull, setDockFull] = useState<DockFullInfo | null>(null);
  const [bumpConfirming, setBumpConfirming] = useState(false);
  const [bumping, setBumping] = useState(false);
  const [bumpError, setBumpError] = useState<string | null>(null);

  // Track which port we've already fetched market info for
  const lastFetchedPortId = useRef<string | null>(null);

  // Track which stations we've already requested slip info for
  const slipsFetchedFor = useRef<Set<string>>(new Set());

  // Lazily load slip availability for each port shown in the undocked list
  useEffect(() => {
    if (playerState?.is_docked) {
      // Occupancy changes while we're docked; refetch fresh after undocking
      slipsFetchedFor.current.clear();
      return;
    }
    portsInSector.forEach(port => {
      if (slipsFetchedFor.current.has(port.id)) return;
      getStationSlips(port.id).then(info => {
        if (info) {
          // Mark fetched only on success so a transient failure retries
          slipsFetchedFor.current.add(port.id);
          setSlipsByStation(prev => ({ ...prev, [port.id]: info }));
        }
      });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playerState?.is_docked, portsInSector]);

  // Auto-select first port if only one available (run once on mount)
  const firstPortId = portsInSector.length === 1 ? portsInSector[0].id : null;
  useEffect(() => {
    if (firstPortId && !selectedPort) {
      setSelectedPort(firstPortId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [firstPortId]); // Only react to actual port ID changes, not array reference changes

  // Load market info when port is selected - with deduplication
  useEffect(() => {
    if (selectedPort && selectedPort !== lastFetchedPortId.current) {
      lastFetchedPortId.current = selectedPort;
      getMarketInfo(selectedPort);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedPort]); // Only trigger when selectedPort changes

  // Helper to safely get cargo used space
  const getCargoUsed = (): number => {
    if (!currentShip?.cargo) return 0;
    // Cargo can be {used, capacity, contents} OR {resource: amount}
    if (typeof currentShip.cargo === 'object' && 'used' in currentShip.cargo) {
      return Number(currentShip.cargo.used) || 0;
    }
    // Legacy format: sum up resource amounts, but only numbers
    return Object.values(currentShip.cargo)
      .filter((v): v is number => typeof v === 'number')
      .reduce((a, b) => a + b, 0);
  };

  // Helper to safely get cargo capacity
  const getCargoCapacity = (): number => {
    if (!currentShip) return 0;
    // Check cargo.capacity first, then cargo_capacity
    if (currentShip.cargo && typeof currentShip.cargo === 'object' && 'capacity' in currentShip.cargo) {
      return Number(currentShip.cargo.capacity) || 0;
    }
    return currentShip.cargo_capacity || 0;
  };

  // Helper to get player's quantity of a resource
  const getPlayerResourceAmount = (resourceType: string): number => {
    if (!currentShip?.cargo) return 0;
    // Check contents field first (new format)
    if (typeof currentShip.cargo === 'object' && 'contents' in currentShip.cargo) {
      const contents = currentShip.cargo.contents as Record<string, number>;
      return Number(contents[resourceType]) || 0;
    }
    // Legacy format
    return Number((currentShip.cargo as Record<string, number>)[resourceType]) || 0;
  };

  // Helper to get total units of cargo the player is carrying
  const getPlayerCargoCount = (): number => {
    if (!currentShip?.cargo) return 0;
    // Check contents field first (new format)
    if (typeof currentShip.cargo === 'object' && 'contents' in currentShip.cargo) {
      const contents = currentShip.cargo.contents as Record<string, number>;
      return Object.values(contents)
        .filter((v): v is number => typeof v === 'number')
        .reduce((a, b) => a + b, 0);
    }
    // Legacy format
    return Object.values(currentShip.cargo as Record<string, number>)
      .filter((v): v is number => typeof v === 'number')
      .reduce((a, b) => a + b, 0);
  };

  // Calculate trade costs when parameters change
  useEffect(() => {
    if (selectedResource && marketInfo && tradeQuantity > 0) {
      const resource = marketInfo.resources[selectedResource];
      if (resource) {
        // sell_price = what the station charges when the player buys;
        // buy_price = what the station pays when the player sells
        const unitPrice = tradeMode === 'buy' ? resource.sell_price : resource.buy_price;
        const totalCost = unitPrice * tradeQuantity;

        // Check affordability and cargo space
        const isAffordable = tradeMode === 'buy'
          ? (playerState?.credits || 0) >= totalCost
          : true; // Can always sell if you have the resource

        const currentCargo = getCargoUsed();
        const cargoCapacity = getCargoCapacity();
        const fitsInCargo = tradeMode === 'buy'
          ? (currentCargo + tradeQuantity) <= cargoCapacity
          : true; // Selling always frees up cargo space

        setTradeCalculation({
          resourceType: selectedResource,
          quantity: tradeQuantity,
          unitPrice,
          totalCost,
          isAffordable,
          fitsInCargo
        });
      }
    } else {
      setTradeCalculation(null);
    }
  }, [selectedResource, marketInfo, tradeQuantity, tradeMode, playerState, currentShip]);

  // Show loading state if player state isn't loaded yet
  if (!playerState) {
    return (
      <Wrapper>
        <div className="trading-interface">
          <div className="trading-header">
            <h2>Trading Interface</h2>
          </div>
          <div className="not-docked-message">
            <div className="message-icon">⏳</div>
            <h3>Loading...</h3>
            <p>Initializing trading systems...</p>
          </div>
        </div>
      </Wrapper>
    );
  }

  // Re-fetch slip availability for one station (e.g. after a 409)
  const refreshStationSlips = async (stationId: string) => {
    const info = await getStationSlips(stationId);
    if (info) {
      setSlipsByStation(prev => ({ ...prev, [stationId]: info }));
    }
  };

  const handleDock = async (stationId: string) => {
    setDockingStationId(stationId);
    setDockFull(null);
    setBumpConfirming(false);
    setBumpError(null);
    try {
      const result = await dockAtStation(stationId);

      if (result?.full) {
        // All slips occupied — we've been auto-enqueued. Show the inline
        // queue/bump panel instead of a generic error.
        const port = portsInSector.find(p => p.id === stationId);
        setDockFull({
          stationId,
          stationName: port?.name || 'this station',
          detail: result.detail,
          slips: result.slips || { capacity: 0, occupied: 0 },
          queue_position: result.queue_position,
          bumpable: result.bumpable || [],
          bump_cost: result.bump_cost || 0
        });
        refreshStationSlips(stationId);
        return;
      }

      // After successful dock, select this port for trading
      setSelectedPort(stationId);
      addNotification({
        title: 'Docked',
        content: 'Successfully docked at station.',
        level: 'success'
      });
    } catch (err: any) {
      addNotification({
        title: 'Docking Failed',
        content: err.response?.data?.detail || err.response?.data?.message || 'Failed to dock at station.',
        level: 'error'
      });
    } finally {
      setDockingStationId(null);
    }
  };

  // Longest-tenured occupant is the canonical bump target
  const getBumpTarget = (): BumpableOccupant | null => {
    if (!dockFull || dockFull.bumpable.length === 0) return null;
    return dockFull.bumpable.reduce((a, b) => (b.tenure_hours > a.tenure_hours ? b : a));
  };

  const formatTenure = (hours: number): string =>
    hours < 1 ? '<1h' : `${Math.round(hours)}h`;

  // 'pay 5× fee: 500cr' when the multiplier is clean, else just the cost
  const formatBumpCost = (): string => {
    if (!dockFull) return '';
    const fee = slipsByStation[dockFull.stationId]?.fee;
    if (fee && dockFull.bump_cost % fee === 0) {
      return `pay ${dockFull.bump_cost / fee}× fee: ${formatCredits(dockFull.bump_cost)}cr`;
    }
    return `pay ${formatCredits(dockFull.bump_cost)}cr`;
  };

  const handleBump = async () => {
    const target = getBumpTarget();
    if (!dockFull || !target || bumping) return;

    setBumping(true);
    setBumpError(null);
    try {
      await bumpDockOccupant(dockFull.stationId, target.player_id);

      // Bump docks us — flow into the normal docked state
      const stationId = dockFull.stationId;
      const stationName = dockFull.stationName;
      setDockFull(null);
      setBumpConfirming(false);
      setSelectedPort(stationId);
      addNotification({
        title: 'Docked',
        content: `Slip secured at ${stationName} — ${target.name} was evicted.`,
        level: 'success'
      });
    } catch (err: any) {
      setBumpError(
        err.response?.data?.detail ||
        err.response?.data?.message ||
        'Bump failed — the slip may have already changed hands.'
      );
      if (dockFull) {
        refreshStationSlips(dockFull.stationId);
      }
    } finally {
      setBumping(false);
    }
  };

  const handlePortChange = (stationId: string) => {
    setSelectedPort(stationId);
    setSelectedResource('');
    setTradeQuantity(1);
  };

  const handleResourceChange = (resourceType: string) => {
    setSelectedResource(resourceType);
    setTradeQuantity(1);
    // Directly open the trade modal so the player can immediately buy/sell
    setShowConfirmDialog(true);
  };

  const handleTradeModeChange = (mode: 'buy' | 'sell') => {
    setTradeMode(mode);
    setTradeQuantity(1);
  };

  const getMaxQuantity = (): number => {
    if (!selectedResource || !marketInfo || !playerState || !currentShip) return 0;
    
    const resource = marketInfo.resources[selectedResource];
    if (!resource) return 0;

    if (tradeMode === 'buy') {
      // For buying: limited by credits, cargo space, and port inventory
      // (the station charges sell_price when the player buys)
      const affordableQuantity = Math.floor(playerState.credits / resource.sell_price);
      const currentCargo = getCargoUsed();
      const cargoSpace = getCargoCapacity() - currentCargo;
      const portInventory = resource.quantity;

      return Math.min(affordableQuantity, cargoSpace, portInventory);
    } else {
      // For selling: limited by what player has
      return getPlayerResourceAmount(selectedResource);
    }
  };

  const setMaxQuantity = () => {
    const maxQty = getMaxQuantity();
    setTradeQuantity(maxQty);
  };

  const canExecuteTrade = (): boolean => {
    if (!tradeCalculation || !playerState?.is_docked) return false;

    const resource = marketInfo?.resources[selectedResource];
    if (!resource) return false;

    if (tradeMode === 'buy') {
      return resource.station_sells && tradeCalculation.isAffordable && tradeCalculation.fitsInCargo && tradeQuantity <= resource.quantity;
    } else {
      const playerHas = getPlayerResourceAmount(selectedResource);
      return resource.station_buys && tradeQuantity <= playerHas;
    }
  };

  const executeTrade = async () => {
    if (!canExecuteTrade() || !selectedPort || !selectedResource) return;

    try {
      let result;
      if (tradeMode === 'buy') {
        result = await buyResource(selectedPort, selectedResource, tradeQuantity);
      } else {
        result = await sellResource(selectedPort, selectedResource, tradeQuantity);
      }

      // Show success notification
      const defaultMsg = tradeMode === 'buy'
        ? `Bought ${tradeQuantity} ${selectedResource}`
        : `Sold ${tradeQuantity} ${selectedResource}`;
      addNotification({
        title: 'Trade Successful',
        content: result?.message || defaultMsg,
        level: 'success'
      });

      // Reset form
      setTradeQuantity(1);
      setShowConfirmDialog(false);
      
    } catch (error: any) {
      const serverMessage: string = error.response?.data?.detail || error.response?.data?.message || '';
      let content = serverMessage || 'Failed to execute trade';
      // Map directionality errors to friendlier hints pointing at the other tab
      if (serverMessage.includes('does not sell')) {
        content = `This station doesn't sell ${formatName(selectedResource)} — but it may buy it. Try the Sell Resources tab.`;
      } else if (serverMessage.includes('does not buy')) {
        content = `This station doesn't buy ${formatName(selectedResource)} — but it may sell it. Try the Buy Resources tab.`;
      }
      addNotification({
        title: 'Trade Failed',
        content,
        level: 'error'
      });
    }
  };

  const formatCredits = (amount: number): string => {
    return new Intl.NumberFormat().format(amount);
  };

  const getResourceIcon = (resourceType: string): string => {
    const icons: Record<string, string> = {
      'Food': '🌾',
      'Fuel': '⚡',
      'Ore': '🪨', 
      'Tech': '🔧',
      'Organics': '🧬',
      'Equipment': '⚙️',
      'Luxuries': '💎',
      'Colonists': '👥'
    };
    return icons[resourceType] || '📦';
  };

  if (!playerState?.is_docked) {
    return (
      <Wrapper>
      <div className="trading-interface">
        <div className="trading-header">
          <h2>Trading Interface</h2>
          <div className="status-indicator disconnected">Not Docked</div>
        </div>
        <div className="not-docked-message">
          <div className="message-icon">🚀</div>
          <h3>Dock at a Station to Trade</h3>
          <p>You must be docked at a port to access trading facilities.</p>
          {portsInSector.length > 0 && (
            <div className="available-ports">
              <h4>Available Ports in Sector:</h4>
              <ul>
                {portsInSector.map(port => {
                  const slips = slipsByStation[port.id];
                  const slipsFull = !!slips && slips.free === 0;
                  return (
                    <li key={port.id} className={`port-item ${slipsFull ? 'slips-full' : ''}`.trim()}>
                      <div className="port-info">
                        <span className="port-name">{port.name}</span>
                        <span className="port-type">{port.type}</span>
                      </div>
                      <button
                        className="dock-button"
                        onClick={() => handleDock(port.id)}
                        disabled={dockingStationId !== null}
                        title={slipsFull
                          ? `All ${slips.capacity} transient slips occupied — docking joins the queue (${slips.queue_length} waiting) or lets you bump a long-tenured occupant.`
                          : undefined}
                      >
                        {dockingStationId === port.id
                          ? 'Docking...'
                          : slipsFull ? 'Dock (Queue)' : 'Dock'}
                      </button>
                      {slips && (
                        <div className={`port-slips-line ${slipsFull ? 'full' : ''}`.trim()}>
                          Transient slips: {slips.occupied}/{slips.capacity} occupied · fee {formatCredits(slips.fee)}cr
                          {slipsFull && typeof (slips as any).estimated_wait_minutes === 'number' && (
                            <> · Estimated wait: ~{(slips as any).estimated_wait_minutes} min</>
                          )}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>

              {dockFull && (() => {
                const bumpTarget = getBumpTarget();
                return (
                  <div className="dock-full-panel" role="alert">
                    <div className="dock-full-header">
                      <span className="dock-full-title">
                        All {dockFull.slips.capacity} slips occupied
                      </span>
                      {dockFull.queue_position != null && (
                        <span className="queue-badge">
                          Queue position {dockFull.queue_position}
                        </span>
                      )}
                    </div>
                    <p className="dock-full-detail">
                      {dockFull.detail || `${dockFull.stationName} has no free transient slips. You've been added to the docking queue.`}
                    </p>
                    {bumpTarget && (
                      <div className="bump-option">
                        {!bumpConfirming ? (
                          <button
                            className="bump-button"
                            onClick={() => { setBumpConfirming(true); setBumpError(null); }}
                            disabled={bumping}
                          >
                            Evict {bumpTarget.name} — docked {formatTenure(bumpTarget.tenure_hours)} — {formatBumpCost()}
                          </button>
                        ) : (
                          <div className="bump-confirm-row">
                            <span className="bump-confirm-prompt">
                              Evict {bumpTarget.name} and {formatBumpCost()}?
                            </span>
                            <button
                              className="bump-confirm-button"
                              onClick={handleBump}
                              disabled={bumping}
                            >
                              {bumping ? 'Evicting...' : 'Confirm Eviction'}
                            </button>
                            <button
                              className="bump-cancel-button"
                              onClick={() => setBumpConfirming(false)}
                              disabled={bumping}
                            >
                              Cancel
                            </button>
                          </div>
                        )}
                        {bumpError && <div className="bump-error">{bumpError}</div>}
                      </div>
                    )}
                    <button
                      className="dock-full-dismiss"
                      onClick={() => { setDockFull(null); setBumpConfirming(false); setBumpError(null); }}
                    >
                      Dismiss
                    </button>
                  </div>
                );
              })()}
            </div>
          )}
        </div>
      </div>
      </Wrapper>
    );
  }

  // Optional flavor data from the backend; both render-guarded below
  const traderPersonality = getTraderPersonality(marketInfo?.port?.trader_personality_type);

  return (
    <Wrapper>
    <div className="trading-interface">
      <div className="trading-header">
        <h2>Trading Interface</h2>
        <div className="connection-status">
          <div className={`status-indicator ${isConnected ? 'connected' : 'disconnected'}`}>
            {isConnected ? 'Real-time' : 'Offline'}
          </div>
        </div>
      </div>

      {error && (
        <div className="error-message">
          <span className="error-icon">⚠️</span>
          {error}
        </div>
      )}

      <div className="trading-content">
        {/* Station Selection */}
        <div className="port-selection">
          <label htmlFor="port-select">Select Station:</label>
          <select 
            id="port-select"
            value={selectedPort} 
            onChange={(e) => handlePortChange(e.target.value)}
            disabled={portsInSector.length <= 1}
          >
            <option value="">Choose a port...</option>
            {portsInSector.map(port => (
              <option key={port.id} value={port.id}>
                {port.name} ({port.type})
              </option>
            ))}
          </select>
        </div>

        {/* Trade Mode Selection */}
        <div className="trade-mode-selection">
          <button 
            className={`mode-button ${tradeMode === 'buy' ? 'active' : ''}`}
            onClick={() => handleTradeModeChange('buy')}
          >
            Buy Resources
          </button>
          <button 
            className={`mode-button ${tradeMode === 'sell' ? 'active' : ''}`}
            onClick={() => handleTradeModeChange('sell')}
          >
            Sell Resources
          </button>
        </div>

        {/* Market Information */}
        {marketInfo && (
          <div className="market-info">
            <div className="market-info-header">
              <h3>Market at {marketInfo.port.name}</h3>
              <StationClassBadge station_class={marketInfo.port.station_class} />
              {traderPersonality && (
                <span
                  className="trader-personality-chip"
                  title={`Trader temperament: ${traderPersonality.label}`}
                >
                  {traderPersonality.label}
                </span>
              )}
            </div>
            <div className="port-details">
              <span>Type: {marketInfo.port.type}</span>
              {marketInfo.port.faction && <span>Faction: {marketInfo.port.faction}</span>}
              {/* Render the server's effective tax rate verbatim — 0 means
                  genuinely untaxed (unowned port); `|| 0.1` used to fabricate
                  a 10% rate out of an honest zero. */}
              <span>Tax Rate: {((marketInfo.port.tax_rate ?? 0) * 100).toFixed(1)}%</span>
            </div>

            <div className="player-status-bar">
              <span className="status-credits">💰 Credits: {formatCredits(playerState?.credits || 0)}</span>
              <span className="status-cargo">📦 Cargo: {getCargoUsed()} / {getCargoCapacity()}</span>
            </div>

            {tradeMode === 'sell' && getPlayerCargoCount() === 0 ? (
              <div className="empty-cargo-state">
                <div className="empty-icon">📦</div>
                <h4>Cargo Hold Empty</h4>
                <p>Buy goods first to sell them at this station.</p>
                <button
                  className="switch-mode-button"
                  onClick={() => handleTradeModeChange('buy')}
                >
                  Switch to Buy Mode
                </button>
              </div>
            ) : (
            <div className="resources-grid">
              {Object.entries(marketInfo.resources).map(([resourceType, resource]) => {
                const playerAmount = getPlayerResourceAmount(resourceType);
                const supportsDirection = tradeMode === 'buy' ? resource.station_sells : resource.station_buys;
                const canTrade = tradeMode === 'buy'
                  ? resource.station_sells && resource.quantity > 0
                  : resource.station_buys && playerAmount > 0;

                return (
                  <div
                    key={resourceType}
                    className={`resource-card ${!canTrade ? 'disabled' : ''} ${!supportsDirection ? 'direction-unsupported' : ''} ${selectedResource === resourceType ? 'selected' : ''}`}
                    onClick={() => canTrade && handleResourceChange(resourceType)}
                    role="button"
                    tabIndex={canTrade ? 0 : -1}
                    onKeyDown={(e) => {
                      if (canTrade && (e.key === 'Enter' || e.key === ' ')) {
                        e.preventDefault();
                        handleResourceChange(resourceType);
                      }
                    }}
                  >
                    <div className="resource-icon">{getResourceIcon(resourceType)}</div>
                    <div className="resource-name">{formatName(resourceType)}</div>
                    <div className="resource-direction-badges">
                      {resource.station_sells && <span className="direction-badge sells">Station Sells</span>}
                      {resource.station_buys && <span className="direction-badge buys">Station Buys</span>}
                    </div>
                    <div className="resource-prices">
                      {/* Player buys at the station's sell_price, sells at its buy_price */}
                      {resource.station_sells && <div className="buy-price">You pay: {formatCredits(resource.sell_price)}</div>}
                      {resource.station_buys && <div className="sell-price">You get: {formatCredits(resource.buy_price)}</div>}
                    </div>
                    <div className="resource-quantity">
                      {tradeMode === 'buy'
                        ? `Available: ${resource.quantity}`
                        : `You have: ${playerAmount}`
                      }
                    </div>
                    {canTrade && (
                      <div className="resource-trade-hint">
                        Click to {tradeMode}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
            )}
          </div>
        )}

        {/* Trade Action Button - Opens Modal */}
        {selectedResource && marketInfo && (
          <div className="trade-action-bar">
            <div className="selected-resource-info">
              <span className="resource-icon-large">{getResourceIcon(selectedResource)}</span>
              <div className="resource-details">
                <span className="resource-name-large">{formatName(selectedResource)}</span>
                <span className="resource-price">
                  {tradeMode === 'buy'
                    ? `Buy @ ${formatCredits(marketInfo.resources[selectedResource]?.sell_price || 0)}/unit`
                    : `Sell @ ${formatCredits(marketInfo.resources[selectedResource]?.buy_price || 0)}/unit`
                  }
                </span>
              </div>
            </div>
            <button
              className="open-trade-modal-button"
              onClick={() => setShowConfirmDialog(true)}
            >
              {tradeMode === 'buy' ? 'Buy' : 'Sell'} {formatName(selectedResource)}
            </button>
          </div>
        )}
      </div>

      {/* Trade Modal - Rendered via Portal to escape stacking context */}
      {showConfirmDialog && selectedResource && marketInfo && createPortal(
        <div className="modal-overlay" onClick={() => setShowConfirmDialog(false)}>
          <div className="trade-modal" onClick={(e) => e.stopPropagation()}>
            <div className="trade-modal-header">
              <div className="modal-resource-info">
                <span className="modal-resource-icon">{getResourceIcon(selectedResource)}</span>
                <h3>
                  <span className={`modal-trade-mode ${tradeMode}`}>{tradeMode === 'buy' ? 'BUY' : 'SELL'}</span>
                  {formatName(selectedResource)}
                </h3>
              </div>
              <button className="modal-close" onClick={() => setShowConfirmDialog(false)}>×</button>
            </div>

            <div className="trade-modal-body">
              {/* Quantity Slider */}
              <div className="quantity-section">
                <label>Quantity</label>
                <div className="quantity-slider-container">
                  <input
                    type="range"
                    min="1"
                    max={Math.max(1, getMaxQuantity())}
                    value={tradeQuantity}
                    onChange={(e) => setTradeQuantity(parseInt(e.target.value))}
                    className="quantity-slider"
                  />
                  <div className="quantity-input-group">
                    <button
                      className="qty-btn"
                      onClick={() => setTradeQuantity(Math.max(1, tradeQuantity - 1))}
                      disabled={tradeQuantity <= 1}
                    >
                      −
                    </button>
                    <input
                      type="number"
                      min="1"
                      max={getMaxQuantity()}
                      value={tradeQuantity}
                      onChange={(e) => setTradeQuantity(Math.max(1, Math.min(getMaxQuantity(), parseInt(e.target.value) || 1)))}
                      className="quantity-input"
                    />
                    <button
                      className="qty-btn"
                      onClick={() => setTradeQuantity(Math.min(getMaxQuantity(), tradeQuantity + 1))}
                      disabled={tradeQuantity >= getMaxQuantity()}
                    >
                      +
                    </button>
                  </div>
                </div>
                <div className="quantity-presets">
                  <button onClick={() => setTradeQuantity(1)}>1</button>
                  <button onClick={() => setTradeQuantity(Math.floor(getMaxQuantity() * 0.25) || 1)}>
                    25% ({Math.floor(getMaxQuantity() * 0.25) || 1})
                  </button>
                  <button onClick={() => setTradeQuantity(Math.floor(getMaxQuantity() * 0.5) || 1)}>
                    50% ({Math.floor(getMaxQuantity() * 0.5) || 1})
                  </button>
                  <button onClick={() => setTradeQuantity(Math.floor(getMaxQuantity() * 0.75) || 1)}>
                    75% ({Math.floor(getMaxQuantity() * 0.75) || 1})
                  </button>
                  <button onClick={() => setTradeQuantity(getMaxQuantity() || 1)}>
                    Max ({getMaxQuantity()})
                  </button>
                </div>
              </div>

              {/* Trade Summary */}
              <div className="trade-summary-card">
                <div className="summary-row">
                  <span>Unit Price</span>
                  <span className="value">{formatCredits(marketInfo.resources[selectedResource]?.[tradeMode === 'buy' ? 'sell_price' : 'buy_price'] || 0)}</span>
                </div>
                <div className="summary-row">
                  <span>Quantity</span>
                  <span className="value">× {tradeQuantity}</span>
                </div>
                <div className="summary-divider"></div>
                <div className="summary-row total">
                  <span>{tradeMode === 'buy' ? 'Total Cost' : 'Total Earnings'}</span>
                  <span className="value highlight">
                    {formatCredits((marketInfo.resources[selectedResource]?.[tradeMode === 'buy' ? 'sell_price' : 'buy_price'] || 0) * tradeQuantity)}
                  </span>
                </div>

                {tradeMode === 'buy' && (
                  <>
                    <div className="summary-divider"></div>
                    <div className="summary-row">
                      <span>Your Credits</span>
                      <span className="value">{formatCredits(playerState?.credits || 0)}</span>
                    </div>
                    <div className="summary-row">
                      <span>After Purchase</span>
                      <span className={`value ${((playerState?.credits || 0) - ((marketInfo.resources[selectedResource]?.sell_price || 0) * tradeQuantity)) < 0 ? 'error' : 'success'}`}>
                        {formatCredits((playerState?.credits || 0) - ((marketInfo.resources[selectedResource]?.sell_price || 0) * tradeQuantity))}
                      </span>
                    </div>
                    <div className="summary-row">
                      <span>Cargo Space</span>
                      <span className={`value ${(getCargoUsed() + tradeQuantity) > getCargoCapacity() ? 'error' : ''}`}>
                        {getCargoUsed() + tradeQuantity} / {getCargoCapacity()}
                      </span>
                    </div>
                  </>
                )}

                {tradeMode === 'sell' && (
                  <>
                    <div className="summary-divider"></div>
                    <div className="summary-row">
                      <span>Your Credits</span>
                      <span className="value">{formatCredits(playerState?.credits || 0)}</span>
                    </div>
                    <div className="summary-row">
                      <span>After Sale</span>
                      <span className="value success">
                        {formatCredits((playerState?.credits || 0) + ((marketInfo.resources[selectedResource]?.buy_price || 0) * tradeQuantity))}
                      </span>
                    </div>
                  </>
                )}
              </div>
            </div>

            <div className="trade-modal-footer">
              <button className="cancel-btn" onClick={() => setShowConfirmDialog(false)}>
                Cancel
              </button>
              <button
                className="confirm-trade-btn"
                onClick={executeTrade}
                disabled={!canExecuteTrade() || isLoading}
              >
                {isLoading ? 'Processing...' : `Confirm ${tradeMode === 'buy' ? 'Purchase' : 'Sale'}`}
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}
    </div>
    </Wrapper>
  );
};

export default TradingInterface;