import React, { useEffect, useRef, useState, useCallback } from 'react';
import './navigation-map.css';

interface Sector {
  id: number;
  name: string;
  type?: string;
  connected_sectors?: number[];
}

interface NavigationMapProps {
  currentSectorId: number;
  sectors: Sector[];
  availableMoves: number[];
  onNavigate: (sectorId: number) => void;
  width?: number;
  height?: number;
}

interface Node {
  id: number;
  x: number;
  y: number;
  vx: number;
  vy: number;
  sector: Sector;
}

const NavigationMap: React.FC<NavigationMapProps> = ({
  currentSectorId,
  sectors,
  availableMoves,
  onNavigate,
  width = 600,
  height = 600
}) => {
  const svgRef = useRef<SVGSVGElement>(null);
  const [nodes, setNodes] = useState<Node[]>([]);
  const [hoveredNode, setHoveredNode] = useState<number | null>(null);
  const [isSimulating, setIsSimulating] = useState(false);
  const [isMoving, setIsMoving] = useState(false);
  const animationRef = useRef<number>();

  // Stable reference to onNavigate to avoid stale closures
  const onNavigateRef = useRef(onNavigate);
  onNavigateRef.current = onNavigate;

  // Stable reference to availableMoves
  const availableMovesRef = useRef(availableMoves);
  availableMovesRef.current = availableMoves;

  // Initialize nodes with force-directed layout
  useEffect(() => {
    if (!sectors || sectors.length === 0) return;

    // Create nodes centered around current sector
    const currentSector = sectors.find(s => s.id === currentSectorId);
    if (!currentSector) return;

    const centerX = width / 2;
    const centerY = height / 2;

    const newNodes: Node[] = sectors.map((sector, index) => {
      const isCurrent = sector.id === currentSectorId;

      // Place current sector in center
      if (isCurrent) {
        return {
          id: sector.id,
          x: centerX,
          y: centerY,
          vx: 0,
          vy: 0,
          sector
        };
      }

      // Place connected sectors in a circle around current (maximum spacing)
      const isConnected = availableMoves.includes(sector.id);
      const radius = isConnected ? 350 : 450;  // Very wide spacing for clear readability
      const angle = (index / sectors.length) * Math.PI * 2;

      return {
        id: sector.id,
        x: centerX + Math.cos(angle) * radius + (Math.random() - 0.5) * 20,
        y: centerY + Math.sin(angle) * radius + (Math.random() - 0.5) * 20,
        vx: 0,
        vy: 0,
        sector
      };
    });

    setNodes(newNodes);
    setIsSimulating(true);
  }, [sectors, currentSectorId, availableMoves, width, height]);

  // Force-directed graph simulation - stops when settled
  useEffect(() => {
    if (nodes.length === 0 || !isSimulating) return;

    let frameCount = 0;
    const maxFrames = 120; // Stop after ~2 seconds even if not fully settled
    let settled = false;

    const simulate = () => {
      if (settled) return;
      frameCount++;

      setNodes(prevNodes => {
        const updatedNodes = [...prevNodes];
        const alpha = 0.1; // Damping factor
        let totalVelocity = 0;

        updatedNodes.forEach((node, i) => {
          // Skip current sector (always centered)
          if (node.id === currentSectorId) {
            node.x = width / 2;
            node.y = height / 2;
            node.vx = 0;
            node.vy = 0;
            return;
          }

          let fx = 0;
          let fy = 0;

          // Repulsion from other nodes (strong force to keep nodes far apart)
          updatedNodes.forEach((other, j) => {
            if (i === j) return;
            const dx = node.x - other.x;
            const dy = node.y - other.y;
            const dist = Math.sqrt(dx * dx + dy * dy) || 1;
            const force = 8000 / (dist * dist);  // Strong repulsion for maximum spacing
            fx += (dx / dist) * force;
            fy += (dy / dist) * force;
          });

          // Attraction to connected nodes
          const connections = node.sector.connected_sectors || [];
          connections.forEach(connectedId => {
            const connectedNode = updatedNodes.find(n => n.id === connectedId);
            if (!connectedNode) return;
            const dx = connectedNode.x - node.x;
            const dy = connectedNode.y - node.y;
            const dist = Math.sqrt(dx * dx + dy * dy) || 1;
            const force = dist * 0.01; // Spring force
            fx += (dx / dist) * force;
            fy += (dy / dist) * force;
          });

          // Apply forces
          node.vx += fx * alpha;
          node.vy += fy * alpha;

          // Damping
          node.vx *= 0.85;
          node.vy *= 0.85;

          // Track total velocity to detect settling
          totalVelocity += Math.abs(node.vx) + Math.abs(node.vy);

          // Update position
          node.x += node.vx;
          node.y += node.vy;

          // Boundary constraints
          const margin = 50;
          if (node.x < margin) node.x = margin;
          if (node.x > width - margin) node.x = width - margin;
          if (node.y < margin) node.y = margin;
          if (node.y > height - margin) node.y = height - margin;
        });

        // Stop simulation when nodes have settled or max frames reached
        if (totalVelocity < 0.5 || frameCount >= maxFrames) {
          // Zero out all velocities for a clean stop
          updatedNodes.forEach(node => {
            node.vx = 0;
            node.vy = 0;
          });
          settled = true;
          // Schedule simulation stop (can't call setState during state updater)
          setTimeout(() => setIsSimulating(false), 0);
        }

        return updatedNodes;
      });

      if (!settled) {
        animationRef.current = requestAnimationFrame(simulate);
      }
    };

    simulate();

    return () => {
      settled = true;
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current);
      }
    };
  }, [nodes.length, isSimulating, currentSectorId, width, height]);

  const handleNodeClick = useCallback((sectorId: number) => {
    if (isMoving) return;
    if (availableMovesRef.current.includes(sectorId)) {
      setIsMoving(true);
      onNavigateRef.current(sectorId);
      // Reset moving state after a delay (in case navigation fails silently)
      setTimeout(() => setIsMoving(false), 3000);
    }
  }, [isMoving]);

  // Get node color based on sector type
  const getNodeColor = (sector: Sector, isCurrent: boolean, isAvailable: boolean) => {
    if (isCurrent) return '#00ff41';
    if (!isAvailable) return '#6b7280';

    const typeColors: { [key: string]: string } = {
      'normal': '#00d9ff',
      'nebula': '#c961de',
      'asteroid_field': '#8b4513',
      'ice_field': '#88ddff',
      'radiation_zone': '#00ff41',
      'void': '#1a1a2e'
    };

    return typeColors[sector.type || 'normal'] || '#00d9ff';
  };

  // Get node size
  const getNodeSize = (isCurrent: boolean, isAvailable: boolean) => {
    if (isCurrent) return 16;
    if (isAvailable) return 12;
    return 8;
  };

  return (
    <div className="navigation-map-container">
      <svg
        ref={svgRef}
        className="navigation-map-svg"
        viewBox={`0 0 ${width} ${height}`}
        /* Anchor the scaled viewBox content to the TOP-center of the
           SVG element. The default `xMidYMid meet` centers the content
           vertically — so when the parent container is taller than
           viewBox aspect, a visible band of empty space appears ABOVE
           the graph. Anchoring to `xMidYMin` puts any remaining empty
           space below the graph instead. */
        preserveAspectRatio="xMidYMin meet"
        width="100%"
        height="100%"
      >
        {/* Connection lines */}
        <g className="connections">
          {nodes.map(node => {
            const connections = node.sector.connected_sectors || [];
            return (
              <React.Fragment key={`connections-${node.id}`}>
                {connections.map(connectedId => {
                  const connectedNode = nodes.find(n => n.id === connectedId);
                  if (!connectedNode) return null;

                  const isCurrentConnection = node.id === currentSectorId || connectedId === currentSectorId;
                  const isAvailableConnection = availableMoves.includes(node.id) && availableMoves.includes(connectedId);

                  return (
                    <line
                      key={`${node.id}-${connectedId}`}
                      x1={node.x}
                      y1={node.y}
                      x2={connectedNode.x}
                      y2={connectedNode.y}
                      className={`connection-line ${isCurrentConnection ? 'current' : ''} ${isAvailableConnection ? 'available' : ''}`}
                      stroke={isCurrentConnection ? '#00ff41' : isAvailableConnection ? '#00d9ff' : '#444'}
                      strokeWidth={isCurrentConnection ? 2 : 1}
                      opacity={isCurrentConnection ? 0.8 : isAvailableConnection ? 0.5 : 0.2}
                    />
                  );
                })}
              </React.Fragment>
            );
          })}
        </g>

        {/* Sector nodes */}
        <g className="nodes">
          {nodes.map(node => {
            const isCurrent = node.id === currentSectorId;
            const isAvailable = availableMoves.includes(node.id);
            const isHovered = hoveredNode === node.id;
            const nodeColor = getNodeColor(node.sector, isCurrent, isAvailable);
            const nodeSize = getNodeSize(isCurrent, isAvailable);

            return (
              <g key={node.id} className="node-group">
                {/* Glow effect */}
                <circle
                  cx={node.x}
                  cy={node.y}
                  r={nodeSize + 4}
                  fill={nodeColor}
                  opacity={isCurrent ? 0.3 : isHovered ? 0.2 : 0}
                  className="node-glow"
                  style={{ pointerEvents: 'none' }}
                />

                {/* Invisible larger hit target for easier clicking */}
                {isAvailable && (
                  <circle
                    cx={node.x}
                    cy={node.y}
                    r={nodeSize + 10}
                    fill="transparent"
                    stroke="none"
                    style={{ cursor: 'pointer' }}
                    onPointerDown={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      handleNodeClick(node.id);
                    }}
                    onMouseEnter={() => setHoveredNode(node.id)}
                    onMouseLeave={() => setHoveredNode(null)}
                  />
                )}

                {/* Node circle */}
                <circle
                  cx={node.x}
                  cy={node.y}
                  r={nodeSize}
                  fill={nodeColor}
                  stroke={isCurrent ? '#00ff41' : isAvailable ? '#00d9ff' : '#444'}
                  strokeWidth={isCurrent ? 3 : 2}
                  className={`node-circle ${isAvailable ? 'available' : ''} ${isCurrent ? 'current' : ''} ${isMoving ? 'moving' : ''}`}
                  onMouseEnter={() => setHoveredNode(node.id)}
                  onMouseLeave={() => setHoveredNode(null)}
                  onPointerDown={(e) => {
                    if (isAvailable) {
                      e.preventDefault();
                      e.stopPropagation();
                      handleNodeClick(node.id);
                    }
                  }}
                  style={{ cursor: isAvailable ? 'pointer' : 'default' }}
                />

                {/* Node label */}
                {(isCurrent || isHovered || isAvailable) && (
                  <text
                    x={node.x}
                    y={node.y + nodeSize + 16}
                    textAnchor="middle"
                    className="node-label"
                    fill={nodeColor}
                    fontSize="11"
                    fontWeight="bold"
                    style={{ pointerEvents: 'none' }}
                  >
                    {node.sector.name}
                  </text>
                )}

                {/* Warp prompt on hover for available sectors */}
                {isAvailable && isHovered && !isCurrent && (
                  <text
                    x={node.x}
                    y={node.y - nodeSize - 8}
                    textAnchor="middle"
                    className="warp-prompt"
                    fill="#00d9ff"
                    fontSize="9"
                    fontWeight="bold"
                    style={{ pointerEvents: 'none' }}
                  >
                    {isMoving ? '⟐ WARPING...' : '▶ CLICK TO WARP'}
                  </text>
                )}

                {/* Current sector indicator */}
                {isCurrent && (
                  <text
                    x={node.x}
                    y={node.y - nodeSize - 8}
                    textAnchor="middle"
                    className="current-indicator"
                    fill="#00ff41"
                    fontSize="10"
                    fontWeight="bold"
                    style={{ pointerEvents: 'none' }}
                  >
                    ◆ YOU ARE HERE
                  </text>
                )}
              </g>
            );
          })}
        </g>
      </svg>

      {/* Navigation instructions */}
      <div className="navigation-instructions">
        <div className="instruction-item">
          <span className="instruction-icon" style={{ color: '#00ff41' }}>●</span>
          <span>Current Location</span>
        </div>
        <div className="instruction-item">
          <span className="instruction-icon" style={{ color: '#00d9ff' }}>●</span>
          <span>Available (Click to Warp)</span>
        </div>
        <div className="instruction-item">
          <span className="instruction-icon" style={{ color: '#6b7280' }}>●</span>
          <span>Out of Range</span>
        </div>
      </div>
    </div>
  );
};

export default NavigationMap;
