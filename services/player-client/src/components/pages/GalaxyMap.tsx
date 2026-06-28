import React, { useState, useEffect, useRef } from 'react';
import { useGame } from '../../contexts/GameContext';
import GameLayout from '../layouts/GameLayout';
import CockpitInstrument from '../cockpit/CockpitInstrument';
import Galaxy3DRenderer from '../galaxy/Galaxy3DRenderer';
import ErrorBoundary from '../common/ErrorBoundary';
import './galaxy-map.css';
import '../galaxy/styles/galaxy-3d.css';

interface MapSector {
  id: number;
  name: string;
  type: string;
  x: number;
  y: number;
  isConnected: boolean;
  isDiscovered: boolean;
  isCurrent: boolean;
}

interface MapConnection {
  from: number;
  to: number;
  isTunnel: boolean;
  isOneWay: boolean;
}

const GalaxyMap: React.FC = () => {
  const { playerState, currentSector, availableMoves, getAvailableMoves, moveToSector, scanForLatentTunnels } = useGame();
  const [localSectors, setLocalSectors] = useState<MapSector[]>([]);
  const [connections, setConnections] = useState<MapConnection[]>([]);
  const [selectedSector, setSelectedSector] = useState<MapSector | null>(null);
  const [mapOffset, setMapOffset] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(1);
  const [viewMode, setViewMode] = useState<'2d' | '3d'>('2d');
  // WO-LW — latent-warp scan: in-app feedback (no native alert — freeze-trap).
  const [isScanning, setIsScanning] = useState(false);
  const [scanResult, setScanResult] = useState<{ ok: boolean; message: string } | null>(null);
  const mapRef = useRef<HTMLDivElement>(null);
  
  // Simulated data for visualization - in a real game, this would come from API
  useEffect(() => {
    if (currentSector) {
      // Clear previous data
      setLocalSectors([]);
      setConnections([]);
      
      // Add current sector (use sector_id for the numeric display ID, not id which is UUID)
      const currentSectorObj: MapSector = {
        id: currentSector.sector_id,
        name: currentSector.name,
        type: currentSector.type,
        x: 0,
        y: 0,
        isConnected: true,
        isDiscovered: true,
        isCurrent: true
      };
      
      const newSectors: MapSector[] = [currentSectorObj];
      const newConnections: MapConnection[] = [];
      
      // Track sector IDs already added to avoid duplicate React keys
      const addedSectorIds = new Set<number>([currentSector.sector_id]);

      // Add directly connected sectors (warps), skipping duplicates of current sector
      if (availableMoves.warps && availableMoves.warps.length) {
        const filteredWarps = availableMoves.warps.filter(
          warp => !addedSectorIds.has(warp.sector_id)
        );
        filteredWarps.forEach((warp, index) => {
          // Layout in a circle around current sector
          const angle = (2 * Math.PI * index) / filteredWarps.length;
          const radius = 150;
          const x = Math.cos(angle) * radius;
          const y = Math.sin(angle) * radius;

          newSectors.push({
            id: warp.sector_id,
            name: warp.name,
            type: warp.type,
            x,
            y,
            isConnected: true,
            isDiscovered: true,
            isCurrent: false
          });

          addedSectorIds.add(warp.sector_id);

          newConnections.push({
            from: currentSector.sector_id,
            to: warp.sector_id,
            isTunnel: false,
            isOneWay: false // Assuming bidirectional by default
          });
        });
      }

      // Add warp tunnel connections, skipping duplicates of current sector or warps
      if (availableMoves.tunnels && availableMoves.tunnels.length) {
        const filteredTunnels = availableMoves.tunnels.filter(
          tunnel => !addedSectorIds.has(tunnel.sector_id)
        );
        filteredTunnels.forEach((tunnel, index) => {
          // Layout tunnels further out
          const angle = (2 * Math.PI * index) / filteredTunnels.length;
          const radius = 300;
          const x = Math.cos(angle) * radius;
          const y = Math.sin(angle) * radius;

          newSectors.push({
            id: tunnel.sector_id,
            name: tunnel.name,
            type: tunnel.type,
            x,
            y,
            isConnected: true,
            isDiscovered: true,
            isCurrent: false
          });

          addedSectorIds.add(tunnel.sector_id);

          newConnections.push({
            from: currentSector.sector_id,
            to: tunnel.sector_id,
            isTunnel: true,
            isOneWay: false // Could use tunnel.tunnel_type to determine this
          });
        });
      }
      
      setLocalSectors(newSectors);
      setConnections(newConnections);
    }
  }, [currentSector, availableMoves]);
  
  useEffect(() => {
    if (playerState) {
      // Get current location's exits when map loads
      getAvailableMoves();
    }
  }, [playerState]);
  
  // Map interaction handlers
  const handleMouseDown = (e: React.MouseEvent) => {
    if (e.button === 0) { // Left mouse button
      setIsDragging(true);
      setDragStart({ x: e.clientX, y: e.clientY });
    }
  };
  
  const handleMouseMove = (e: React.MouseEvent) => {
    if (isDragging) {
      const deltaX = e.clientX - dragStart.x;
      const deltaY = e.clientY - dragStart.y;
      setMapOffset({
        x: mapOffset.x + deltaX,
        y: mapOffset.y + deltaY
      });
      setDragStart({ x: e.clientX, y: e.clientY });
    }
  };
  
  const handleMouseUp = () => {
    setIsDragging(false);
  };
  
  const handleWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    const zoomDelta = -e.deltaY * 0.001;
    const newZoom = Math.max(0.5, Math.min(2, zoom + zoomDelta));
    setZoom(newZoom);
  };
  
  const handleSectorClick = (sector: MapSector) => {
    setSelectedSector(sector);
  };
  
  const handleTravelClick = () => {
    if (selectedSector && selectedSector.id !== currentSector?.sector_id) {
      moveToSector(selectedSector.id);
      setSelectedSector(null);
    }
  };

  // WO-LW — sweep this sector for latent (hidden) warp tunnels. The context
  // method refreshes available moves on a reveal, so the new tunnels flow into
  // the map's tunnel rendering automatically via the availableMoves effect.
  const handleScanLatentWarps = async () => {
    if (isScanning) return;
    setIsScanning(true);
    setScanResult(null);
    try {
      const result = await scanForLatentTunnels();
      if (result) {
        const revealed = result.revealed ?? 0;
        setScanResult({
          ok: true,
          message: revealed > 0
            ? `Latent scan: ${revealed} warp tunnel${revealed === 1 ? '' : 's'} revealed`
            : (result.message || 'Latent scan: no hidden tunnels detected'),
        });
      } else {
        setScanResult({ ok: false, message: 'Latent scan unavailable' });
      }
    } catch (error: any) {
      setScanResult({
        ok: false,
        message: error?.response?.data?.detail || error?.response?.data?.message || 'Latent scan failed',
      });
    } finally {
      setIsScanning(false);
    }
  };
  
  return (
    <GameLayout>
      <CockpitInstrument title="NAV CHART" accent="#00D9FF" subtitle="GALACTIC CARTOGRAPHY">
      <div className="galaxy-map-container">
        <div className="map-header">
          {/* Page-level title removed — the instrument LED header carries
              NAV CHART (Law 3); this strip keeps only the view controls. */}
          <div className="map-controls">
            <button 
              className={`view-mode-button ${viewMode === '3d' ? 'active' : ''}`}
              onClick={() => setViewMode('3d')}
              title="3D Galaxy View"
            >
              🌌 3D
            </button>
            <button
              className={`view-mode-button ${viewMode === '2d' ? 'active' : ''}`}
              onClick={() => setViewMode('2d')}
              title="2D Galaxy Map"
            >
              📍 2D
            </button>
            <button
              className="view-mode-button"
              onClick={handleScanLatentWarps}
              disabled={isScanning}
              title="Sweep this sector for hidden warp tunnels"
            >
              {isScanning ? '📡 Scanning…' : '📡 Scan latent warps'}
            </button>
            {viewMode === '2d' && (
              <>
                <button 
                  className="zoom-button" 
                  onClick={() => setZoom(Math.min(2, zoom + 0.1))}
                >
                  +
                </button>
                <button 
                  className="zoom-button" 
                  onClick={() => setZoom(Math.max(0.5, zoom - 0.1))}
                >
                  -
                </button>
                <button 
                  className="reset-button" 
                  onClick={() => {
                    setMapOffset({ x: 0, y: 0 });
                    setZoom(1);
                  }}
                >
                  Reset
                </button>
              </>
            )}
          </div>
          {scanResult && (
            <div
              className="latent-scan-result"
              role="status"
              style={{
                marginTop: '0.5rem',
                padding: '0.4rem 0.75rem',
                borderRadius: '4px',
                fontSize: '0.85rem',
                alignSelf: 'flex-end',
                backgroundColor: '#131b2c',
                border: `1px solid ${scanResult.ok ? '#2a6f4d' : '#6f2a2a'}`,
                color: scanResult.ok ? '#7ee0a8' : '#ff9b9b',
              }}
            >
              {scanResult.message}
            </div>
          )}
        </div>

        {viewMode === '3d' ? (
          <div className="map-view map-view-3d">
            <ErrorBoundary fallback={
              <div style={{ padding: '20px', textAlign: 'center' }}>
                <h3>3D Galaxy View Unavailable</h3>
                <p>There was an issue loading the 3D galaxy map.</p>
                <button onClick={() => setViewMode('2d')}>
                  Switch to 2D Map
                </button>
              </div>
            }>
              <Galaxy3DRenderer 
                className="galaxy-3d-view"
                onSectorSelect={(sector) => {
                  // Convert from full Sector to MapSector for compatibility
                  const mapSector: MapSector = {
                    id: sector.sector_id,
                    name: sector.name,
                    type: (sector as any).sector_type || 'normal',
                    x: 0, // Position handled by 3D renderer
                    y: 0,
                    isConnected: true,
                    isDiscovered: true,
                    isCurrent: sector.sector_id === currentSector?.sector_id
                  };
                  setSelectedSector(mapSector);
                }}
              />
            </ErrorBoundary>
          </div>
        ) : (
          <div 
            className="map-view map-view-2d"
            onMouseDown={handleMouseDown}
            onMouseMove={handleMouseMove}
            onMouseUp={handleMouseUp}
            onMouseLeave={handleMouseUp}
            onWheel={handleWheel}
          >
          <div 
            className="map-content"
            ref={mapRef}
            style={{
              transform: `translate(${mapOffset.x}px, ${mapOffset.y}px) scale(${zoom})`,
              cursor: isDragging ? 'grabbing' : 'grab'
            }}
          >
            {/* Draw connections */}
            <svg className="connections-layer" width="100%" height="100%">
              {connections.map((conn, i) => {
                const fromSector = localSectors.find(s => s.id === conn.from);
                const toSector = localSectors.find(s => s.id === conn.to);
                
                if (!fromSector || !toSector) return null;
                
                // Calculate center of map as the reference
                const mapWidth = mapRef.current?.clientWidth || 800;
                const mapHeight = mapRef.current?.clientHeight || 600;
                const centerX = mapWidth / 2;
                const centerY = mapHeight / 2;
                
                const x1 = centerX + fromSector.x;
                const y1 = centerY + fromSector.y;
                const x2 = centerX + toSector.x;
                const y2 = centerY + toSector.y;
                
                return (
                  <g key={`conn-${i}`}>
                    <line 
                      x1={x1} y1={y1} x2={x2} y2={y2}
                      className={conn.isTunnel ? 'warp-tunnel' : 'warp-path'}
                      strokeDasharray={conn.isTunnel ? "5,5" : ""}
                    />
                    {conn.isOneWay && (
                      <polygon 
                        points={`${x2},${y2} ${x2-10},${y2-5} ${x2-10},${y2+5}`}
                        className="direction-arrow"
                        transform={`rotate(${Math.atan2(y2-y1, x2-x1) * (180/Math.PI)}, ${x2}, ${y2})`}
                      />
                    )}
                  </g>
                );
              })}
            </svg>
            
            {/* Draw sectors */}
            <div className="sectors-layer">
              {localSectors.map(sector => {
                // Calculate position based on map center
                const mapWidth = mapRef.current?.clientWidth || 800;
                const mapHeight = mapRef.current?.clientHeight || 600;
                const centerX = mapWidth / 2;
                const centerY = mapHeight / 2;
                
                const posX = centerX + sector.x;
                const posY = centerY + sector.y;
                
                return (
                  <div 
                    key={`sector-${sector.id}`}
                    className={`sector-node ${sector.isCurrent ? 'current' : ''} ${
                      selectedSector?.id === sector.id ? 'selected' : ''
                    } ${sector.isConnected ? 'connected' : ''} ${sector.type.toLowerCase()}`}
                    style={{
                      left: `${posX}px`,
                      top: `${posY}px`
                    }}
                    onClick={() => handleSectorClick(sector)}
                  >
                    <div className="sector-id">{sector.id}</div>
                  </div>
                );
              })}
            </div>
          </div>
          </div>
        )}
        
        {selectedSector && (
          <div className="sector-details-panel">
            <h3>{selectedSector.id === currentSector?.sector_id ? 'Current Location' : 'Selected Sector'}</h3>
            <div className="sector-info">
              <div className="sector-name">
                Sector {selectedSector.id}: {selectedSector.name}
              </div>
              <div className="sector-type">
                {selectedSector.type}
              </div>
              {selectedSector.id !== currentSector?.sector_id && selectedSector.isConnected ? (
                <button
                  className="travel-button"
                  onClick={handleTravelClick}
                >
                  Travel to Sector
                </button>
              ) : selectedSector.id === currentSector?.sector_id ? (
                <div className="current-sector-badge">
                  Current Sector
                </div>
              ) : null}
            </div>
          </div>
        )}
      </div>
      </CockpitInstrument>
    </GameLayout>
  );
};

export default GalaxyMap;