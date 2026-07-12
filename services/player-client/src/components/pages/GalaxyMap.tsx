import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useGame } from '../../contexts/GameContext';
import { useAutopilot, CoursePlot } from '../../contexts/AutopilotContext';
import { navAPI, sectorAPI, NavChartResponse } from '../../services/api';
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
  // Directly adjacent (1-hop) to the player's current sector, per
  // availableMoves -- the existing single-hop Travel button stays wired to
  // these; anything else known but non-adjacent routes through the new
  // plotCourse/"Lay in course" flow instead.
  isConnected: boolean;
  // The player's OWN visit -- course-plotting.md's `visited` semantics
  // (corp-shared knowledge makes a sector known/plottable but never marks
  // it visited).
  isDiscovered: boolean;
  isCurrent: boolean;
}

interface MapConnection {
  from: number;
  to: number;
  isTunnel: boolean;
  isOneWay: boolean;
}

// Fits the known-graph's real server coordinates into a fixed-size layout
// square, preserving aspect ratio and centering the bounding box at the
// origin -- server x/y units are arbitrary per-galaxy grid coordinates, not
// pixels, so this normalizes them into a renderable local space (mirrors the
// map-content/posX = centerX + sector.x rendering convention below).
const LAYOUT_SPAN_PX = 500;

function layoutKnownSectors(
  chart: NavChartResponse,
  adjacentIds: Set<number>
): { sectors: MapSector[]; connections: MapConnection[] } {
  // Defensive: NavChartResponse's shape is enforced by the TS type, not at
  // runtime -- a malformed/incomplete 200 response (WO-UIPC-COCKPITINSTRUMENT-OCCLUSION
  // follow-up hardening) must not crash the map. Empty arrays render the same
  // "nothing known yet" state the loading/error branches already show.
  const chartSectors = chart?.sectors ?? [];
  const chartEdges = chart?.edges ?? [];
  if (!chartSectors.length) return { sectors: [], connections: [] };

  const xs = chartSectors.map((s) => s.x);
  const ys = chartSectors.map((s) => s.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const span = Math.max(maxX - minX, maxY - minY, 1);
  const scale = LAYOUT_SPAN_PX / span;
  const midX = (minX + maxX) / 2;
  const midY = (minY + maxY) / 2;

  const sectors: MapSector[] = chartSectors.map((s) => ({
    id: s.sector_id,
    name: s.name,
    type: s.type,
    x: (s.x - midX) * scale,
    y: (s.y - midY) * scale,
    isConnected: s.current || adjacentIds.has(s.sector_id),
    isDiscovered: s.visited,
    isCurrent: s.current,
  }));

  // chart.edges already lists both directions of a bidirectional edge --
  // dedupe to one rendered line per undirected pair+kind, and use whether a
  // reverse entry exists to decide the one-way arrow.
  const edgeKeys = new Set(chartEdges.map((e) => `${e.from}>${e.to}:${e.kind}`));
  const seenPairs = new Set<string>();
  const connections: MapConnection[] = [];
  for (const e of chartEdges) {
    const canonical = e.from < e.to ? `${e.from}-${e.to}:${e.kind}` : `${e.to}-${e.from}:${e.kind}`;
    if (seenPairs.has(canonical)) continue;
    seenPairs.add(canonical);
    connections.push({
      from: e.from,
      to: e.to,
      isTunnel: e.kind === 'tunnel',
      isOneWay: !edgeKeys.has(`${e.to}>${e.from}:${e.kind}`),
    });
  }

  return { sectors, connections };
}

interface SectorContents {
  planets: Array<{ id: string; name: string }>;
  stations: Array<{ id: string; name: string }>;
}

// Plain-function narrowing (rather than an inline JSX ternary) so the
// reachable/unreachable discriminant narrows reliably.
function renderCoursePreview(preview: CoursePlot) {
  if ('hops' in preview) {
    return (
      <>
        <div>Hops: {preview.hops.length}</div>
        <div>Turn cost: {preview.total_turns}</div>
      </>
    );
  }
  return (
    <div className="course-preview-unreachable">
      Unreachable
      {preview.nearest_known ? ` — nearest known: ${preview.nearest_known.name}` : ''}
    </div>
  );
}

const GalaxyMap: React.FC = () => {
  const { playerState, currentSector, availableMoves, getAvailableMoves, moveToSector, scanForLatentTunnels } = useGame();
  const { course, lastPlot, status: autopilotStatus, plotCourse, engage } = useAutopilot();
  const [chart, setChart] = useState<NavChartResponse | null>(null);
  const [chartError, setChartError] = useState<string | null>(null);
  const [selectedSector, setSelectedSector] = useState<MapSector | null>(null);
  const [contents, setContents] = useState<SectorContents | null>(null);
  const [contentsLoading, setContentsLoading] = useState(false);
  const [mapOffset, setMapOffset] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(1);
  const [viewMode, setViewMode] = useState<'2d' | '3d'>('2d');
  // WO-LW — latent-warp scan: in-app feedback (no native alert — freeze-trap).
  const [isScanning, setIsScanning] = useState(false);
  const [scanResult, setScanResult] = useState<{ ok: boolean; message: string } | null>(null);
  const mapRef = useRef<HTMLDivElement>(null);

  // The player's real KNOWN navigation surface (WO-PUX-NAVCHART) -- visited ∪
  // corp-shared ∪ current, per course-plotting.md. Re-fetched whenever the
  // player's sector changes so a fresh move's newly-known sectors appear
  // without leaving the page.
  useEffect(() => {
    let cancelled = false;
    navAPI.getChart()
      .then((data) => {
        if (!cancelled) {
          setChart(data);
          setChartError(null);
        }
      })
      .catch((error: any) => {
        if (!cancelled) {
          setChart(null);
          setChartError(error?.message || 'Failed to load nav chart');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [currentSector?.sector_id]);

  useEffect(() => {
    if (playerState) {
      // Get current location's exits when map loads
      getAvailableMoves();
    }
  }, [playerState]);

  const adjacentIds = useMemo(() => {
    const ids = new Set<number>();
    (availableMoves.warps || []).forEach((w) => ids.add(w.sector_id));
    (availableMoves.tunnels || []).forEach((t) => ids.add(t.sector_id));
    return ids;
  }, [availableMoves]);

  const { sectors: localSectors, connections } = useMemo(
    () => (chart ? layoutKnownSectors(chart, adjacentIds) : { sectors: [], connections: [] }),
    [chart, adjacentIds]
  );

  const frontier = chart?.frontier ?? [];

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
    setContents(null);

    if (!sector.isCurrent && !sector.isConnected) {
      // Preview: hop distance / turn cost come straight from AutopilotContext's
      // plot response -- never re-derived client-side. Only fetched for
      // non-adjacent known sectors -- an adjacent sector's single-hop cost is
      // already carried by availableMoves and the existing Travel button
      // doesn't need a plot at all, so skip the redundant network call.
      plotCourse(sector.id);
    }

    // Contents where known -- existing read-only sector endpoints
    // (services/gameserver/src/api/routes/sectors.py), previously unconsumed
    // by the client. Best-effort: a known sector outside the player's
    // current region 404s (pre-existing, unrelated constraint) -- treated as
    // "contents unknown" rather than a hard failure.
    setContentsLoading(true);
    Promise.all([sectorAPI.getPlanets(sector.id), sectorAPI.getStations(sector.id)])
      .then(([planetsRes, stationsRes]) => {
        setContents({
          planets: planetsRes?.planets || [],
          stations: stationsRes?.stations || [],
        });
      })
      .catch(() => setContents(null))
      .finally(() => setContentsLoading(false));
  };

  const handleTravelClick = () => {
    if (selectedSector && selectedSector.id !== currentSector?.sector_id) {
      moveToSector(selectedSector.id);
      setSelectedSector(null);
    }
  };

  const handleLayInCourse = () => {
    if (selectedSector) {
      plotCourse(selectedSector.id);
    }
  };

  const handleEngage = () => {
    engage();
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

  const coursePreview = selectedSector && lastPlot && lastPlot.target_sector_id === selectedSector.id
    ? lastPlot
    : null;
  const canEngage = !!(
    selectedSector &&
    course &&
    course.target_sector_id === selectedSector.id &&
    autopilotStatus === 'idle'
  );

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
                  const known = chart?.sectors.find((s) => s.sector_id === sector.sector_id);
                  const mapSector: MapSector = {
                    id: sector.sector_id,
                    name: sector.name,
                    type: (sector as any).sector_type || 'normal',
                    x: 0, // Position handled by 3D renderer
                    y: 0,
                    isConnected: known ? adjacentIds.has(sector.sector_id) || known.current : adjacentIds.has(sector.sector_id),
                    isDiscovered: known ? known.visited : true,
                    isCurrent: sector.sector_id === currentSector?.sector_id
                  };
                  handleSectorClick(mapSector);
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
                    data-testid={`sector-node-${sector.id}`}
                    className={`sector-node ${sector.isCurrent ? 'current' : ''} ${
                      selectedSector?.id === sector.id ? 'selected' : ''
                    } ${sector.isConnected ? 'connected' : ''} ${sector.isDiscovered ? 'visited' : 'unvisited'} ${sector.type.toLowerCase()}`}
                    style={{
                      left: `${posX}px`,
                      top: `${posY}px`
                    }}
                    onClick={() => handleSectorClick(sector)}
                    title={sector.isDiscovered ? sector.name : `${sector.name} (known, not yet visited)`}
                  >
                    <div className="sector-id">{sector.id}</div>
                  </div>
                );
              })}
            </div>
          </div>
          </div>
        )}

        {frontier.length > 0 && (
          <div className="frontier-strip" data-testid="frontier-strip">
            <span className="frontier-strip-label">FRONTIER</span>
            {frontier.map((stub) => (
              <span
                key={`frontier-${stub.id}`}
                data-testid={`frontier-chip-${stub.id}`}
                className="frontier-chip"
                title="Detected beyond known space — fly there to learn more"
              >
                {stub.id}
              </span>
            ))}
          </div>
        )}

        {chartError && (
          <div className="chart-error" role="status">{chartError}</div>
        )}

        {selectedSector && (
          <div className="sector-details-panel">
            <h3>{selectedSector.isCurrent ? 'Current Location' : 'Selected Sector'}</h3>
            <div className="sector-info">
              <div className="sector-name">
                Sector {selectedSector.id}: {selectedSector.name}
              </div>
              <div className="sector-type">
                {selectedSector.type}
              </div>
              {!selectedSector.isCurrent && !selectedSector.isDiscovered && (
                <div className="sector-known-badge">Known via corp-share — not personally visited</div>
              )}

              {contentsLoading && <div className="sector-contents-loading">Scanning contents…</div>}
              {contents && (contents.planets.length > 0 || contents.stations.length > 0) && (
                <div className="sector-contents">
                  {contents.planets.map((p) => (
                    <div key={p.id} className="sector-content-row">🪐 {p.name}</div>
                  ))}
                  {contents.stations.map((s) => (
                    <div key={s.id} className="sector-content-row">🛰️ {s.name}</div>
                  ))}
                </div>
              )}

              {selectedSector.isCurrent ? (
                <div className="current-sector-badge">
                  Current Sector
                </div>
              ) : selectedSector.isConnected ? (
                <button
                  className="travel-button"
                  data-testid="travel-to-sector"
                  onClick={handleTravelClick}
                >
                  Travel to Sector
                </button>
              ) : (
                <>
                  {coursePreview && (
                    <div className="course-preview" data-testid="course-preview">
                      {renderCoursePreview(coursePreview)}
                    </div>
                  )}
                  <button
                    className="travel-button"
                    data-testid="lay-in-course"
                    onClick={handleLayInCourse}
                  >
                    Lay in course
                  </button>
                  {canEngage && (
                    <button
                      className="travel-button engage-button"
                      data-testid="engage-autopilot"
                      onClick={handleEngage}
                    >
                      Engage Autopilot
                    </button>
                  )}
                </>
              )}
            </div>
          </div>
        )}
      </div>
      </CockpitInstrument>
    </GameLayout>
  );
};

export default GalaxyMap;
