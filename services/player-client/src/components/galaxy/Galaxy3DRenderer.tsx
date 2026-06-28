import { Suspense, useRef, useState, useEffect, useMemo } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { OrbitControls, Html } from '@react-three/drei';
import { Vector3 } from 'three';
import * as THREE from 'three';

import { useGame } from '../../contexts/GameContext';
import { useWebSocket } from '../../contexts/WebSocketContext';
import { Sector } from '../../contexts/GameContext';
import SectorNode3D from './SectorNode3D';
import PlayerMarker3D from './PlayerMarker3D';
import ConnectionPath3D from './ConnectionPath3D';
import StarField from './StarField';

interface Galaxy3DRendererProps {
  className?: string;
  onSectorSelect?: (sector: Sector) => void;
}

interface LODLevel {
  distance: number;
  detail: 'high' | 'medium' | 'low';
  showLabels: boolean;
  showEffects: boolean;
}

const LOD_CONFIG: Record<string, LODLevel> = {
  near: { distance: 50, detail: 'high', showLabels: true, showEffects: true },
  medium: { distance: 200, detail: 'medium', showLabels: true, showEffects: false },
  far: { distance: Infinity, detail: 'low', showLabels: false, showEffects: false }
};

// Galaxy visualization component
function GalaxyScene({ onSectorSelect }: { onSectorSelect?: (sector: Sector) => void }) {
  const { camera } = useThree();
  const { currentSector, availableMoves, isLoading } = useGame();
  const { sectorPlayers, isConnected } = useWebSocket();
  
  const [selectedSector, setSelectedSector] = useState<Sector | null>(null);
  const [cameraDistance, setCameraDistance] = useState(100);
  const groupRef = useRef<THREE.Group>(null);

  // Calculate camera distance for LOD optimization
  useFrame(() => {
    if (camera) {
      const distance = camera.position.length();
      setCameraDistance(distance);
    }
  });

  // Determine LOD level based on camera distance
  const lodLevel = useMemo(() => {
    if (cameraDistance < LOD_CONFIG.near.distance) return LOD_CONFIG.near;
    if (cameraDistance < LOD_CONFIG.medium.distance) return LOD_CONFIG.medium;
    return LOD_CONFIG.far;
  }, [cameraDistance]);

  // Create sectors array from current sector and available moves
  const sectors = useMemo(() => {
    const sectorMap = new Map<number, Sector>();
    
    // Add current sector (use sector_id as numeric key, not id which is UUID)
    if (currentSector) {
      sectorMap.set(currentSector.sector_id, currentSector);
    }
    
    // Add sectors from available moves (create minimal Sector objects)
    // Use Map to avoid duplicates if same sector appears in both warps and tunnels
    availableMoves.warps.forEach(warp => {
      if (!sectorMap.has(warp.sector_id)) {
        sectorMap.set(warp.sector_id, {
          id: warp.sector_id,
          sector_id: warp.sector_id,
          name: warp.name,
          type: warp.type,
          hazard_level: 0,
          radiation_level: 0,
          resources: {},
          players_present: [],
          special_features: [],
          // Neighbour anomaly markers (WO-SFM): the move option carries the
          // formations that include this sector, identity withheld until
          // discovered. May be absent on older API responses → default [].
          special_formations: warp.special_formations ?? []
        });
      }
    });

    availableMoves.tunnels.forEach(tunnel => {
      if (!sectorMap.has(tunnel.sector_id)) {
        sectorMap.set(tunnel.sector_id, {
          id: tunnel.sector_id,
          sector_id: tunnel.sector_id,
          name: tunnel.name,
          type: tunnel.type,
          hazard_level: 0,
          radiation_level: 0,
          resources: {},
          players_present: [],
          special_features: [],
          special_formations: tunnel.special_formations ?? []
        });
      }
    });
    
    return Array.from(sectorMap.values());
  }, [currentSector, availableMoves]);

  // Galaxy layout calculation with 3D positioning
  const sectorPositions = useMemo(() => {
    if (!sectors || sectors.length === 0) return new Map();
    
    const positions = new Map<number, Vector3>();
    
    // Simple layout with current sector at center
    sectors.forEach((sector, index) => {
      if (sector.sector_id === currentSector?.sector_id) {
        // Current sector at center
        positions.set(sector.sector_id, new Vector3(0, 0, 0));
      } else {
        // Other sectors in a circle around current
        const angle = (2 * Math.PI * (index - 1)) / (sectors.length - 1);
        const radius = 50;
        const z = (Math.random() - 0.5) * 20; // Random Z variation

        const x = Math.cos(angle) * radius;
        const y = Math.sin(angle) * radius;

        positions.set(sector.sector_id, new Vector3(x, y, z));
      }
    });
    
    return positions;
  }, [sectors, currentSector]);

  // Handle sector selection
  const handleSectorClick = (sector: Sector) => {
    setSelectedSector(sector);
    onSectorSelect?.(sector);
  };

  // Auto-focus on current sector when it changes
  useEffect(() => {
    if (currentSector && sectorPositions.has(currentSector.sector_id)) {
      const position = sectorPositions.get(currentSector.sector_id)!;
      // Smoothly move camera to focus on current sector
      camera.lookAt(position);
    }
  }, [currentSector, sectorPositions, camera]);

  // Only show the placeholder while sector data is genuinely missing (the
  // true first load). Never gate on the global loading flag: tearing down
  // the Three.js scene on a background refresh destroys canvas/camera
  // state. Once sectors exist the scene stays mounted, period.
  if (!sectors || sectors.length === 0) {
    // isLoading is initial-hydration-only now, so it distinguishes "still
    // loading" from "loaded but genuinely empty" (e.g. moves fetch failed).
    return (
      <Html center>
        <div className="loading-container">
          {isLoading ? (
            <>
              <div className="loading-spinner"></div>
              <div>Loading Galaxy...</div>
            </>
          ) : (
            <div>NO CHART DATA — sector telemetry unavailable</div>
          )}
        </div>
      </Html>
    );
  }

  return (
    <group ref={groupRef}>
      {/* Background star field */}
      <StarField />
      
      {/* Sector nodes */}
      {sectors.map((sector) => {
        const position = sectorPositions.get(sector.sector_id);
        if (!position) return null;

        // Apply LOD - skip distant sectors in low detail mode
        if (lodLevel.detail === 'low' && cameraDistance > 300) {
          const distanceToSector = camera.position.distanceTo(position);
          if (distanceToSector > 100) return null; // Cull distant sectors
        }

        return (
          <SectorNode3D
            key={sector.sector_id}
            sector={sector}
            position={position}
            isSelected={selectedSector?.sector_id === sector.sector_id}
            isCurrent={currentSector?.sector_id === sector.sector_id}
            onClick={handleSectorClick}
            lodLevel={lodLevel}
            playerCount={sector.sector_id === currentSector?.sector_id ? sectorPlayers.length : 0}
          />
        );
      })}
      
      {/* Connection paths between sectors */}
      {currentSector && (() => {
        const currentPosition = sectorPositions.get(currentSector.sector_id);
        if (!currentPosition) return null;

        const connections: JSX.Element[] = [];

        // Warp connections
        availableMoves.warps.forEach((warp, index) => {
          const targetPosition = sectorPositions.get(warp.sector_id);
          if (targetPosition) {
            connections.push(
              <ConnectionPath3D
                key={`warp-${currentSector.sector_id}-${warp.sector_id}-${index}`}
                start={currentPosition}
                end={targetPosition}
                type="warp"
                lodLevel={lodLevel}
              />
            );
          }
        });

        // Tunnel connections
        availableMoves.tunnels.forEach((tunnel, index) => {
          const targetPosition = sectorPositions.get(tunnel.sector_id);
          if (targetPosition) {
            connections.push(
              <ConnectionPath3D
                key={`tunnel-${currentSector.sector_id}-${tunnel.sector_id}-${index}`}
                start={currentPosition}
                end={targetPosition}
                type="tunnel"
                lodLevel={lodLevel}
              />
            );
          }
        });

        return connections;
      })()}
      
      {/* Player markers */}
      {isConnected && currentSector && sectorPlayers.length > 0 && (() => {
        const currentPosition = sectorPositions.get(currentSector.sector_id);
        if (!currentPosition) return null;

        return sectorPlayers.map((player, index) => (
          <PlayerMarker3D
            key={`${player.user_id}-${currentSector.sector_id}`}
            player={player}
            position={currentPosition.clone().add(new Vector3(
              (index - sectorPlayers.length / 2) * 2, // Spread players around sector
              (index % 2) * 2,
              0
            ))}
            lodLevel={lodLevel}
          />
        ));
      })()}
      
      {/* Selected sector info */}
      {selectedSector && lodLevel.showLabels && (
        <Html
          position={sectorPositions.get(selectedSector.sector_id)?.toArray() || [0, 0, 0]}
          center
          className="sector-info-popup"
        >
          <div className="sector-info-card">
            <h3>{selectedSector.name}</h3>
            <p>Type: {selectedSector.type}</p>
            <p>Players: {selectedSector.sector_id === currentSector?.sector_id ? sectorPlayers.length : 0}</p>
            <p>Available: {selectedSector.sector_id === currentSector?.sector_id ? 'Current' : 'Connected'}</p>
          </div>
        </Html>
      )}
    </group>
  );
}

// Main Galaxy 3D Renderer component
export default function Galaxy3DRenderer({ className, onSectorSelect }: Galaxy3DRendererProps) {
  const [performanceMode, setPerformanceMode] = useState<'high' | 'balanced' | 'low'>('balanced');
  
  // Performance monitoring
  useEffect(() => {
    let frameCount = 0;
    let lastTime = performance.now();
    
    const checkPerformance = () => {
      frameCount++;
      const currentTime = performance.now();
      
      if (currentTime - lastTime >= 1000) { // Check every second
        const fps = frameCount;
        frameCount = 0;
        lastTime = currentTime;
        
        // Adjust performance mode based on FPS
        if (fps < 20) {
          setPerformanceMode('low');
        } else if (fps < 45) {
          setPerformanceMode('balanced');
        } else {
          setPerformanceMode('high');
        }
      }
      
      requestAnimationFrame(checkPerformance);
    };
    
    checkPerformance();
  }, []);

  const canvasSettings = useMemo(() => {
    const settings = {
      high: { 
        antialias: true, 
        shadows: true, 
        pixelRatio: Math.min(window.devicePixelRatio, 2),
        powerPreference: 'high-performance' as const
      },
      balanced: { 
        antialias: true, 
        shadows: false, 
        pixelRatio: Math.min(window.devicePixelRatio, 1.5),
        powerPreference: 'default' as const
      },
      low: { 
        antialias: false, 
        shadows: false, 
        pixelRatio: 1,
        powerPreference: 'low-power' as const
      }
    };
    
    return settings[performanceMode];
  }, [performanceMode]);

  return (
    <div className={`galaxy-3d-container ${className || ''}`}>
      {/* Performance indicator */}
      <div className="performance-indicator">
        <span className={`performance-badge ${performanceMode}`}>
          {performanceMode.toUpperCase()}
        </span>
      </div>
      
      {/* 3D Canvas */}
      <Canvas
        camera={{ 
          position: [50, 50, 100], 
          fov: 60,
          near: 0.1,
          far: 2000
        }}
        gl={{
          antialias: canvasSettings.antialias,
          powerPreference: canvasSettings.powerPreference,
          preserveDrawingBuffer: false, // Better performance
          alpha: false // Better performance
        }}
        shadows={canvasSettings.shadows}
        dpr={canvasSettings.pixelRatio}
        style={{ background: '#000010' }}
      >
        {/* Lighting */}
        <ambientLight intensity={0.2} />
        <pointLight position={[100, 100, 100]} intensity={0.8} />
        <pointLight position={[-100, -100, -100]} intensity={0.3} color="#4444ff" />
        
        {/* Controls */}
        <OrbitControls
          enablePan={true}
          enableZoom={true}
          enableRotate={true}
          zoomSpeed={0.6}
          panSpeed={0.8}
          rotateSpeed={0.4}
          minDistance={10}
          maxDistance={500}
          maxPolarAngle={Math.PI} // Allow full rotation
        />
        
        {/* Galaxy scene */}
        <Suspense fallback={null}>
          <GalaxyScene onSectorSelect={onSectorSelect} />
        </Suspense>
      </Canvas>
      
      {/* UI Overlay */}
      <div className="galaxy-ui-overlay">
        <div className="galaxy-controls">
          <button 
            className="control-button"
            onClick={() => setPerformanceMode(performanceMode === 'high' ? 'low' : 'high')}
            title="Toggle Performance Mode"
          >
            ⚡ {performanceMode}
          </button>
        </div>
      </div>
    </div>
  );
}