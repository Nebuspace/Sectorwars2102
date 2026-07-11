import React, { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { TurnsIcon } from '../icons/TurnsIcon';
import './navigation-map.css';

interface Sector {
  id: number;
  name: string;
  type?: string;
  connected_sectors?: number[];
}

/** Minimal shape NavigationMap needs from a plotted course hop — a
 * structural subset of AutopilotContext's CourseHop so this tactical
 * component never imports from contexts/. */
export interface CourseHopPoint {
  sector_id: number;
  name: string;
}

interface NavigationMapProps {
  currentSectorId: number;
  sectors: Sector[];
  availableMoves: number[];
  /** Turn cost per destination sector id — shown in the hover warp prompt */
  moveCosts?: Record<number, number>;
  onNavigate: (sectorId: number) => void;
  width?: number;
  height?: number;
  /**
   * Plotted autopilot course (ADR-0072) — when present, draws a polyline
   * across every hop in order plus a ship marker riding at
   * `currentHopIndex`. Hops beyond the 1-hop neighborhood already covered
   * by `sectors` are chain-injected into the node graph (WO-NAV-COURSE-
   * OVERLAY) so the whole route positions, not just the visible portion.
   */
  course?: CourseHopPoint[] | null;
  /** Index into `course` of the hop currently in progress. */
  currentHopIndex?: number;
}

interface Node {
  id: number;
  x: number;
  y: number;
  vx: number;
  vy: number;
  sector: Sector;
}

// Truncate long sector/region labels so they don't overlap neighboring
// nodes and edges. The full name stays available via the SVG <title>
// tooltip on the node circles.
const MAX_LABEL_CHARS = 14;
const truncateLabel = (name: string): string =>
  name.length > MAX_LABEL_CHARS ? `${name.slice(0, MAX_LABEL_CHARS - 1)}…` : name;

const NavigationMap: React.FC<NavigationMapProps> = ({
  currentSectorId,
  sectors,
  availableMoves,
  moveCosts,
  onNavigate,
  width = 600,
  height = 600,
  course = null,
  currentHopIndex = 0
}) => {
  const svgRef = useRef<SVGSVGElement>(null);
  const [nodes, setNodes] = useState<Node[]>([]);
  const [hoveredNode, setHoveredNode] = useState<number | null>(null);
  const [isSimulating, setIsSimulating] = useState(false);
  const [isMoving, setIsMoving] = useState(false);
  const animationRef = useRef<number | undefined>(undefined);

  // Stable reference to onNavigate to avoid stale closures
  const onNavigateRef = useRef(onNavigate);
  onNavigateRef.current = onNavigate;

  // Stable reference to availableMoves
  const availableMovesRef = useRef(availableMoves);
  availableMovesRef.current = availableMoves;

  // Course-chain injection (WO-NAV-COURSE-OVERLAY, seam ruling): `sectors`
  // is the 1-hop neighborhood only, so a multi-hop plotted course reaches
  // beyond it. Splice the course's OWN hops in as a connected chain —
  // hop[i]→hop[i+1] is an edge by construction (nav_service's Dijkstra
  // path), and hop[0] chains back to the current sector — so the force
  // layout can position every hop without pulling in the broader known
  // graph. Identity-preserving no-op (returns `sectors` itself) when no
  // course is plotted, so untouched nav rendering stays byte-identical.
  const mergedSectors = useMemo(() => {
    if (!course || course.length === 0) return sectors;

    const byId = new Map<number, Sector>();
    (sectors || []).forEach(s => byId.set(s.id, { ...s, connected_sectors: [...(s.connected_sectors || [])] }));

    const linkEdge = (fromId: number, toId: number) => {
      const node = byId.get(fromId);
      if (node && !(node.connected_sectors || []).includes(toId)) {
        node.connected_sectors = [...(node.connected_sectors || []), toId];
      }
    };

    let prevId = currentSectorId;
    course.forEach(hop => {
      if (!byId.has(hop.sector_id)) {
        byId.set(hop.sector_id, {
          id: hop.sector_id,
          name: hop.name,
          connected_sectors: [prevId]
        });
      }
      linkEdge(prevId, hop.sector_id);
      linkEdge(hop.sector_id, prevId);
      prevId = hop.sector_id;
    });

    return Array.from(byId.values());
  }, [sectors, course, currentSectorId]);

  // Topology signature: the layout depends only on WHICH sectors exist, the
  // current sector, the available moves, and the canvas size — NOT on the
  // sector objects' identity. The dashboard re-fetches currentSector every 5s
  // for live ship presence, which hands NavigationMap brand-new `sectors`/
  // `availableMoves` array refs with identical content. Keying the layout
  // effect on this stable STRING (instead of the array refs) stops the force
  // simulation from re-initializing — and the nodes from bobbing/resizing —
  // on every poll. It re-runs only when the actual graph changes (including
  // when a course is plotted/replotted, since mergedSectors folds course
  // hop ids into the signature too).
  const topoSig = useMemo(() => {
    const ids = (mergedSectors || []).map(s => s.id).sort((a, b) => a - b).join(',');
    const moves = [...(availableMoves || [])].sort((a, b) => a - b).join(',');
    return `${ids}|${currentSectorId}|${moves}|${width}x${height}`;
  }, [mergedSectors, availableMoves, currentSectorId, width, height]);

  // Initialize nodes with force-directed layout (only when topology changes)
  useEffect(() => {
    if (!mergedSectors || mergedSectors.length === 0) return;

    // Create nodes centered around current sector
    const currentSector = mergedSectors.find(s => s.id === currentSectorId);
    if (!currentSector) return;

    const centerX = width / 2;
    const centerY = height / 2;

    // Defensive: one node per sector id. Duplicate entries from a caller
    // (e.g. a destination listed via both warp and tunnel) would otherwise
    // render overlapping phantom nodes with duplicate React keys.
    const seenIds = new Set<number>();
    const uniqueSectors = mergedSectors.filter(sector => {
      if (seenIds.has(sector.id)) return false;
      seenIds.add(sector.id);
      return true;
    });

    const newNodes: Node[] = uniqueSectors.map((sector, index) => {
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
      const angle = (index / uniqueSectors.length) * Math.PI * 2;

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
    // Keyed on the stable topology signature so a 5s presence poll (which only
    // changes array identity, not graph content) does NOT restart the layout.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topoSig]);

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
    <div className="navigation-map-wrapper">
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
                  >
                    <title>{node.sector.name}</title>
                  </circle>
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
                >
                  <title>{node.sector.name}</title>
                </circle>

                {/* Node label - truncated with full name in tooltip */}
                {(isCurrent || isHovered || isAvailable) && (
                  <text
                    x={node.x}
                    y={node.y + nodeSize + 14}
                    textAnchor="middle"
                    className="node-label"
                    fill={nodeColor}
                    fontSize="9"
                    fontWeight="bold"
                    style={{ pointerEvents: 'none' }}
                  >
                    {truncateLabel(node.sector.name)}
                  </text>
                )}

                {/* Warp prompt on hover for available sectors */}
                {isAvailable && isHovered && !isCurrent && (
                  <g style={{ pointerEvents: 'none' }}>
                    {isMoving ? (
                      <text
                        x={node.x}
                        y={node.y - nodeSize - 8}
                        textAnchor="middle"
                        className="warp-prompt"
                        fill="#00d9ff"
                        fontSize="9"
                        fontWeight="bold"
                      >
                        ⟐ WARPING...
                      </text>
                    ) : moveCosts?.[node.id] != null ? (
                      <>
                        <text
                          x={node.x - 15}
                          y={node.y - nodeSize - 8}
                          textAnchor="end"
                          className="warp-prompt"
                          fill="#00d9ff"
                          fontSize="9"
                          fontWeight="bold"
                        >
                          ▶ CLICK TO WARP —
                        </text>
                        <TurnsIcon
                          x={node.x - 13}
                          y={node.y - nodeSize - 24}
                          size={26}
                          color="#00d9ff"
                        />
                        <text
                          x={node.x + 15}
                          y={node.y - nodeSize - 8}
                          textAnchor="start"
                          className="warp-prompt"
                          fill="#00d9ff"
                          fontSize="9"
                          fontWeight="bold"
                        >
                          {moveCosts[node.id]}
                        </text>
                      </>
                    ) : (
                      <text
                        x={node.x}
                        y={node.y - nodeSize - 8}
                        textAnchor="middle"
                        className="warp-prompt"
                        fill="#00d9ff"
                        fontSize="9"
                        fontWeight="bold"
                      >
                        ▶ CLICK TO WARP
                      </text>
                    )}
                  </g>
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

        {/* Plotted course overlay (ADR-0072 autopilot) — a bright polyline
            across every hop in order plus per-hop waypoint markers and a
            ship marker riding at currentHopIndex. Independent of the dim
            default connection lines above; renders nothing when no course
            is plotted (WO-NAV-COURSE-OVERLAY). */}
        {course && course.length > 0 && (() => {
          const originNode = nodes.find(n => n.id === currentSectorId);
          if (!originNode) return null;

          // Ordered waypoint nodes: origin first, then every hop that has
          // settled into the node graph. A chain-injected hop not yet laid
          // out on the very first render frame (topoSig effect hasn't run
          // yet) is skipped defensively rather than crashing.
          const waypoints: { node: Node; hopIndex: number }[] = [];
          course.forEach((hop, i) => {
            const n = nodes.find(nd => nd.id === hop.sector_id);
            if (n) waypoints.push({ node: n, hopIndex: i });
          });
          if (waypoints.length === 0) return null;

          const pathD = [originNode, ...waypoints.map(wp => wp.node)]
            .map((n, i) => `${i === 0 ? 'M' : 'L'} ${n.x} ${n.y}`)
            .join(' ');

          // Ship marker sits ON currentHopIndex (the hop currently in
          // progress); once arrived (index runs past the last hop) it
          // clamps to the destination rather than vanishing.
          const clampedShipHop = Math.min(Math.max(currentHopIndex, 0), course.length - 1);

          return (
            <g
              className="course-overlay"
              style={{ pointerEvents: 'none' }}
              role="img"
              aria-label={`Plotted autopilot course, ${course.length} hops`}
            >
              <path
                d={pathD}
                data-testid="course-polyline"
                className="course-polyline"
                fill="none"
              />
              {waypoints.map(({ node: n, hopIndex }) => {
                const hop = course[hopIndex];
                const isDone = hopIndex < clampedShipHop;
                const isShipHere = hopIndex === clampedShipHop;
                return (
                  <g key={`course-hop-${hop.sector_id}`}>
                    <circle
                      cx={n.x}
                      cy={n.y}
                      r={6}
                      data-testid={`course-hop-marker-${hop.sector_id}`}
                      className={`course-hop-marker${isDone ? ' done' : ''}${isShipHere ? ' current' : ''}`}
                    >
                      <title>{`Hop ${hopIndex + 1}: ${hop.name}`}</title>
                    </circle>
                    {isShipHere && (
                      <text
                        x={n.x}
                        y={n.y - 16}
                        textAnchor="middle"
                        data-testid="course-ship-marker"
                        className="course-ship-marker"
                      >
                        <title>{`Ship — current leg ${clampedShipHop + 1} of ${course.length}`}</title>
                        ▲
                      </text>
                    )}
                  </g>
                );
              })}
            </g>
          );
        })()}
      </svg>
    </div>

      {/* Navigation legend — lives OUTSIDE the SVG container so it
          never overlaps clickable warp nodes regardless of graph layout */}
      <div className="navigation-instructions">
        <div className="instruction-item">
          <span className="instruction-icon" style={{ color: '#00ff41' }}>●</span>
          <span>Here</span>
        </div>
        <div className="instruction-item">
          <span className="instruction-icon" style={{ color: '#00d9ff' }}>●</span>
          <span>In Range</span>
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
