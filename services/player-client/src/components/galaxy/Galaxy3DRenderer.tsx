import { Suspense, useRef, useState, useEffect, useMemo, useCallback } from 'react';
import type { ReactElement } from 'react';
import { Canvas, useThree } from '@react-three/fiber';
import { OrbitControls, Html, Line } from '@react-three/drei';
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib';
import { Vector3 } from 'three';
import * as THREE from 'three';

import { useGame } from '../../contexts/GameContext';
import { useWebSocket } from '../../contexts/WebSocketContext';
import { Sector } from '../../contexts/GameContext';
import {
  useAutopilot,
  type CourseReachable,
} from '../../contexts/AutopilotContext';
import {
  navAPI,
  shipUpgradeAPI,
  type NavChartResponse,
  type NavChartSector,
} from '../../services/api';
import { ariaFeed } from '../mfd/ariaFeedStore';
import SectorNode3D, { type SectorKnowledge } from './SectorNode3D';
import PlayerMarker3D from './PlayerMarker3D';
import ConnectionPath3D from './ConnectionPath3D';
import StarField from './StarField';
import CourseConfirmPopup, { ariaEngageCommand } from './CourseConfirmPopup';
import {
  LOCAL_CLUSTER_RADIUS,
  filterNav3DNodes,
  galaxyToSceneScale,
} from './nav3dFog';

interface Galaxy3DRendererProps {
  className?: string;
  onSectorSelect?: (sector: Sector) => void;
  /**
   * Optional preloaded unbounded known chart. When omitted, this view
   * fetches GET /nav/chart (unbounded) so the visited fog-of-war trail
   * stays complete even when the 2D chart uses ?bounded=true.
   */
  chart?: NavChartResponse | null;
}

interface LODLevel {
  detail: 'high' | 'medium' | 'low';
  showLabels: boolean;
  showEffects: boolean;
}

const LOCAL_LOD: LODLevel = {
  detail: 'high',
  showLabels: true,
  showEffects: true,
};

/** Legacy alias — camera home helpers still key off a local fit radius. */
const LOCAL_FIT_RADIUS = LOCAL_CLUSTER_RADIUS;

/** Offset distance for scanner-revealed frontier stubs (no real coords). */
const FRONTIER_STANDOFF = 14;

type ChartNode = {
  sector_id: number;
  name: string;
  type: string;
  x: number;
  y: number;
  z: number;
  knowledge: SectorKnowledge;
  clickable: boolean;
  special_formations?: Sector['special_formations'];
};

const hasCoords = (
  s: { x_coord?: number | null; y_coord?: number | null; z_coord?: number | null } | null | undefined,
): s is { x_coord: number; y_coord: number; z_coord: number } =>
  s != null
  && typeof s.x_coord === 'number'
  && typeof s.y_coord === 'number'
  && typeof s.z_coord === 'number';

function cameraHomeForExtent(extent: number): Vector3 {
  const e = Math.max(extent, 22);
  return new Vector3(0, e * 0.5, e * 1.15);
}

/** Stable unit vector from a sector id — frontier stub placement. */
function frontierOffset(sectorId: number): Vector3 {
  const a = ((sectorId * 12.9898) % 1 + 1) % 1;
  const b = ((sectorId * 78.233) % 1 + 1) % 1;
  const theta = a * Math.PI * 2;
  const phi = Math.acos(b * 2 - 1);
  return new Vector3(
    Math.sin(phi) * Math.cos(theta),
    Math.sin(phi) * Math.sin(theta),
    Math.cos(phi),
  );
}

/**
 * Snap camera home when the player changes sector OR when recenterToken
 * bumps (Recenter button). Telemetry poll identity churn must NOT yank zoom.
 * During autopilot, `courseFocus` steers the look-at toward the active hop window.
 */
function CameraFocus({
  focus,
  home,
  controlsRef,
  sectorKey,
  recenterToken,
  courseFocus,
}: {
  focus: Vector3;
  home: Vector3;
  controlsRef: React.RefObject<OrbitControlsImpl | null>;
  sectorKey: number | string | null | undefined;
  recenterToken: number;
  /** When set (autopilot engaged), pan look-at along the course window. */
  courseFocus?: Vector3 | null;
}): null {
  const { camera } = useThree();
  const homeRef = useRef(home);
  const focusRef = useRef(focus);
  homeRef.current = home;
  focusRef.current = focus;

  useEffect(() => {
    const h = homeRef.current;
    const f = focusRef.current;
    camera.position.copy(h);
    camera.lookAt(f);
    camera.updateProjectionMatrix();
    const controls = controlsRef.current;
    if (controls) {
      controls.target.copy(f);
      controls.update();
    }
  }, [camera, controlsRef, sectorKey, recenterToken]);

  // Soft pan while autopilot advances — don't yank zoom, just slide target.
  useEffect(() => {
    if (!courseFocus) return;
    const controls = controlsRef.current;
    if (!controls) return;
    controls.target.copy(courseFocus);
    controls.update();
  }, [controlsRef, courseFocus]);

  return null;
}

/** Max hop segments kept in the 3D course camera window at once. */
const COURSE_VIEW_HOPS = 4;

function GalaxyScene({
  onSectorSelect,
  onRemoteChartedSelect,
  controlsRef,
  chartProp,
  recenterToken,
  exitsOnly,
}: {
  onSectorSelect?: (sector: Sector) => void;
  /** Non-adjacent charted sector — plot + confirm (never frontier). */
  onRemoteChartedSelect?: (sectorId: number) => void;
  controlsRef: React.RefObject<OrbitControlsImpl | null>;
  chartProp?: NavChartResponse | null;
  recenterToken: number;
  /** When true, only current + warp-now exits (hide visited fog trail). */
  exitsOnly: boolean;
}) {
  const { currentSector, availableMoves, currentShip, isLoading } = useGame();
  const { sectorPlayers, isConnected } = useWebSocket();
  const {
    course: autopilotCourse,
    currentHopIndex,
    status: autopilotStatus,
  } = useAutopilot();
  // Show route whenever a course is laid in or being flown.
  const showCourse = autopilotCourse != null && autopilotCourse.hops.length > 0
    && autopilotStatus !== 'plotting';

  const [selectedSector, setSelectedSector] = useState<Sector | null>(null);
  const [fetchedChart, setFetchedChart] = useState<NavChartResponse | null>(null);
  const [sensorLevel, setSensorLevel] = useState(0);
  const groupRef = useRef<THREE.Group>(null);
  const lodLevel = LOCAL_LOD;

  // Unbounded known chart = full visited fog-of-war trail (not scanner-BFS-trimmed).
  useEffect(() => {
    if (chartProp !== undefined) return;
    let cancelled = false;
    navAPI.getChart(false)
      .then((c) => { if (!cancelled) setFetchedChart(c); })
      .catch(() => { /* keep last */ });
    return () => { cancelled = true; };
  }, [chartProp, currentSector?.sector_id]);

  const chart = chartProp !== undefined ? chartProp : fetchedChart;

  // Sensor upgrade gates one-hop fog past visited sectors.
  useEffect(() => {
    if (!currentShip?.id) {
      setSensorLevel(0);
      return;
    }
    let cancelled = false;
    shipUpgradeAPI.getUpgrades(currentShip.id)
      .then((data: {
        upgrades?: Record<string, { current_level?: number; level?: number }>;
      }) => {
        if (cancelled) return;
        const sensor = data?.upgrades?.SENSOR ?? data?.upgrades?.sensor;
        const level = sensor?.current_level ?? sensor?.level ?? 0;
        setSensorLevel(typeof level === 'number' ? level : 0);
      })
      .catch(() => { if (!cancelled) setSensorLevel(0); });
    return () => { cancelled = true; };
  }, [currentShip?.id]);

  const revealFrontier = sensorLevel >= 1;

  const moveIds = useMemo(() => {
    const ids = new Set<number>();
    availableMoves.warps.forEach((w) => ids.add(w.sector_id));
    availableMoves.tunnels.forEach((t) => ids.add(t.sector_id));
    return ids;
  }, [availableMoves]);

  const chartById = useMemo(() => {
    const m = new Map<number, NavChartSector>();
    chart?.sectors.forEach((s) => m.set(s.sector_id, s));
    return m;
  }, [chart]);

  // Known fog (visited/corp) ∪ current travel exits. Frontier only with Sensor ≥ 1.
  const nodes = useMemo((): ChartNode[] => {
    const byId = new Map<number, ChartNode>();

    chart?.sectors.forEach((s) => {
      byId.set(s.sector_id, {
        sector_id: s.sector_id,
        name: s.name,
        type: s.type,
        x: s.x,
        y: s.y,
        z: s.z,
        knowledge: s.current
          ? 'current'
          : s.visited
            ? 'visited'
            : 'known',
        // Charted remote sectors are clickable for multi-hop plot; current
        // is not. Frontier stubs (separate list) stay non-clickable.
        clickable: !s.current,
      });
    });

    const mergeMove = (
      sector_id: number,
      name: string,
      type: string,
      x: number | null | undefined,
      y: number | null | undefined,
      z: number | null | undefined,
      formations?: Sector['special_formations'],
    ) => {
      const existing = byId.get(sector_id);
      if (existing) {
        existing.clickable = true;
        existing.special_formations = formations ?? existing.special_formations;
        // Warpability is the dominant signal: an exit you can jump to RIGHT
        // NOW reads as 'reachable' even if you've also visited it before —
        // that's the distinction the pilot cares about (warp-now vs been-there).
        if (existing.knowledge !== 'current') existing.knowledge = 'reachable';
        return;
      }
      const fromChart = chartById.get(sector_id);
      byId.set(sector_id, {
        sector_id,
        name: fromChart?.name ?? name,
        type: fromChart?.type ?? type,
        x: fromChart?.x ?? (typeof x === 'number' ? x : 0),
        y: fromChart?.y ?? (typeof y === 'number' ? y : 0),
        z: fromChart?.z ?? (typeof z === 'number' ? z : 0),
        knowledge: 'reachable',
        clickable: true,
        special_formations: formations,
      });
    };

    availableMoves.warps.forEach((w) => {
      mergeMove(w.sector_id, w.name, w.type, w.x_coord, w.y_coord, w.z_coord, w.special_formations);
    });
    availableMoves.tunnels.forEach((t) => {
      mergeMove(t.sector_id, t.name, t.type, t.x_coord, t.y_coord, t.z_coord, t.special_formations);
    });

    if (currentSector && !byId.has(currentSector.sector_id)) {
      byId.set(currentSector.sector_id, {
        sector_id: currentSector.sector_id,
        name: currentSector.name,
        type: currentSector.type,
        x: currentSector.x_coord ?? 0,
        y: currentSector.y_coord ?? 0,
        z: currentSector.z_coord ?? 0,
        knowledge: 'current',
        clickable: false,
        special_formations: currentSector.special_formations,
      });
    } else if (currentSector) {
      const cur = byId.get(currentSector.sector_id)!;
      cur.knowledge = 'current';
      cur.name = currentSector.name;
    }

    return Array.from(byId.values());
  }, [chart, availableMoves, chartById, moveIds, currentSector]);

  const { sectorPositions, clusterExtent } = useMemo(() => {
    const positions = new Map<number, Vector3>();
    const empty = { sectorPositions: positions, clusterExtent: LOCAL_FIT_RADIUS };
    if (!nodes.length) return empty;

    const currentId = currentSector?.sector_id;
    const origin = nodes.find((n) => n.sector_id === currentId)
      ?? (hasCoords(currentSector)
        ? {
            x: currentSector.x_coord,
            y: currentSector.y_coord,
            z: currentSector.z_coord,
          }
        : null);

    const ox = origin && 'x' in origin ? origin.x : 0;
    const oy = origin && 'y' in origin ? origin.y : 0;
    const oz = origin && 'z' in origin ? origin.z : 0;

    const scale = galaxyToSceneScale(nodes, { x: ox, y: oy, z: oz });
    let extent = 0;
    for (const n of nodes) {
      const pos = new Vector3(
        (n.x - ox) * scale,
        (n.y - oy) * scale,
        (n.z - oz) * scale,
      );
      positions.set(n.sector_id, pos);
      extent = Math.max(extent, pos.length());
    }

    // Scanner fog: one hop past visited — stubs have no coords; hang them
    // off the known sector that surfaced them.
    if (revealFrontier && chart?.frontier?.length) {
      for (const stub of chart.frontier) {
        if (positions.has(stub.id)) continue;
        const anchor = positions.get(stub.from);
        if (!anchor) continue;
        const pos = anchor.clone().add(
          frontierOffset(stub.id).multiplyScalar(FRONTIER_STANDOFF),
        );
        positions.set(stub.id, pos);
        extent = Math.max(extent, pos.length());
      }
    }

    return {
      sectorPositions: positions,
      clusterExtent: Math.max(extent, LOCAL_FIT_RADIUS * 0.45),
    };
  }, [nodes, currentSector, revealFrontier, chart?.frontier]);

  // Course hops may include ring-1 bridges not yet on the fog chart — invent
  // placeholder positions so the route ribbon stays continuous.
  const coursePositions = useMemo(() => {
    const map = new Map(sectorPositions);
    if (!showCourse || !autopilotCourse) return map;

    const pathIds: number[] = [];
    if (currentSector?.sector_id != null) pathIds.push(currentSector.sector_id);
    for (const h of autopilotCourse.hops) {
      if (pathIds[pathIds.length - 1] !== h.sector_id) pathIds.push(h.sector_id);
    }

    for (let i = 0; i < pathIds.length; i++) {
      const id = pathIds[i];
      if (map.has(id)) continue;
      let prev: Vector3 | null = null;
      let next: Vector3 | null = null;
      for (let j = i - 1; j >= 0; j--) {
        const p = map.get(pathIds[j]);
        if (p) { prev = p; break; }
      }
      for (let j = i + 1; j < pathIds.length; j++) {
        const p = map.get(pathIds[j]);
        if (p) { next = p; break; }
      }
      if (prev && next) {
        map.set(id, prev.clone().lerp(next, 0.5));
      } else if (prev) {
        map.set(id, prev.clone().add(frontierOffset(id).multiplyScalar(10)));
      } else if (next) {
        map.set(id, next.clone().add(frontierOffset(id).multiplyScalar(10)));
      } else {
        map.set(id, frontierOffset(id).multiplyScalar(14));
      }
    }
    return map;
  }, [sectorPositions, showCourse, autopilotCourse, currentSector?.sector_id]);

  const frontierNodes = useMemo((): ChartNode[] => {
    if (!revealFrontier || !chart?.frontier?.length) return [];
    return chart.frontier
      .filter((f) => sectorPositions.has(f.id) && !nodes.some((n) => n.sector_id === f.id))
      .map((f) => ({
        sector_id: f.id,
        name: '???',
        type: 'frontier',
        x: 0,
        y: 0,
        z: 0,
        knowledge: 'frontier' as const,
        clickable: false,
      }));
  }, [revealFrontier, chart?.frontier, sectorPositions, nodes]);

  const courseHopIds = useMemo(() => {
    if (!showCourse || !autopilotCourse) return new Set<number>();
    return new Set(autopilotCourse.hops.map((h) => h.sector_id));
  }, [showCourse, autopilotCourse]);

  const allRenderNodes = useMemo(
    () => filterNav3DNodes([...nodes, ...frontierNodes], exitsOnly, courseHopIds),
    [nodes, frontierNodes, exitsOnly, courseHopIds],
  );

  const visibleIds = useMemo(
    () => new Set(allRenderNodes.map((n) => n.sector_id)),
    [allRenderNodes],
  );

  // Exits-only / course: frame the local (or route) cluster.
  // Show-all: frame the full visited fog trail so explored space is visible.
  const viewExtent = useMemo(() => {
    if (!exitsOnly && !showCourse) {
      return Math.max(clusterExtent, LOCAL_CLUSTER_RADIUS * 0.45);
    }
    let extent = 0;
    for (const id of visibleIds) {
      const p = sectorPositions.get(id);
      if (p) extent = Math.max(extent, p.length());
    }
    return Math.max(extent, LOCAL_CLUSTER_RADIUS * 0.35);
  }, [exitsOnly, showCourse, clusterExtent, visibleIds, sectorPositions]);

  const focusPoint = useMemo(
    () => sectorPositions.get(currentSector?.sector_id ?? -1) ?? new Vector3(0, 0, 0),
    [sectorPositions, currentSector?.sector_id],
  );

  // Sliding window along the plotted course — show what fits, pan as we hop.
  const courseWindow = useMemo(() => {
    if (!showCourse || !autopilotCourse) {
      return { points: [] as Vector3[], focus: null as Vector3 | null, from: 0, to: 0 };
    }
    const hopIds = autopilotCourse.hops.map((h) => h.sector_id);

    const totalHops = autopilotCourse.hops.length;
    const active = Math.min(Math.max(currentHopIndex, 0), Math.max(totalHops - 1, 0));
    const winStart = Math.max(0, active - 1);
    const winEnd = Math.min(totalHops, winStart + COURSE_VIEW_HOPS);
    const pts: Vector3[] = [];
    const origin = coursePositions.get(currentSector?.sector_id ?? -1);
    if (origin) pts.push(origin.clone());
    for (let i = winStart; i < winEnd; i++) {
      const sid = hopIds[i];
      const p = coursePositions.get(sid);
      if (p) pts.push(p.clone());
    }
    const destId = hopIds[hopIds.length - 1];
    const destPos = coursePositions.get(destId);
    if (destPos && winEnd < totalHops && !pts.some((p) => p.distanceTo(destPos) < 0.01)) {
      pts.push(destPos.clone());
    }

    let focus = focusPoint.clone();
    if (pts.length >= 2) {
      focus = pts[0].clone().lerp(pts[Math.min(1, pts.length - 1)], 0.55);
    } else if (pts.length === 1) {
      focus = pts[0].clone();
    }

    return { points: pts, focus, from: winStart, to: winEnd };
  }, [
    showCourse,
    autopilotCourse,
    currentHopIndex,
    currentSector?.sector_id,
    coursePositions,
    focusPoint,
  ]);

  const cameraHome = useMemo(
    () => cameraHomeForExtent(viewExtent),
    [viewExtent],
  );

  const handleSectorClick = (sector: Sector) => {
    const node = nodes.find((n) => n.sector_id === sector.sector_id)
      ?? frontierNodes.find((n) => n.sector_id === sector.sector_id);
    if (!node || node.knowledge === 'frontier' || node.knowledge === 'current') {
      return;
    }
    setSelectedSector(sector);

    // Adjacent warp-now exit → immediate single hop (existing helm path).
    if (node.knowledge === 'reachable' || moveIds.has(sector.sector_id)) {
      onSectorSelect?.(sector);
      return;
    }

    // Charted but remote → plot + confirm via ARIA multi-sector warp.
    onRemoteChartedSelect?.(sector.sector_id);
  };

  if (!allRenderNodes.length) {
    return (
      <Html center>
        <div className="loading-container">
          {isLoading ? (
            <>
              <div className="loading-spinner"></div>
              <div>Loading chart…</div>
            </>
          ) : (
            <div>NO CHART DATA — explore to reveal sectors</div>
          )}
        </div>
      </Html>
    );
  }

  return (
    <group ref={groupRef}>
      <CameraFocus
        focus={focusPoint}
        home={cameraHome}
        controlsRef={controlsRef}
        sectorKey={currentSector?.sector_id}
        recenterToken={recenterToken}
        courseFocus={
          (autopilotStatus === 'engaged' || autopilotStatus === 'paused')
            ? courseWindow.focus
            : null
        }
      />

      <StarField count={90} radius={Math.max(120, viewExtent * 2.4)} shell />

      {/* Plotted course ribbon — remaining window of hops. */}
      {showCourse && courseWindow.points.length >= 2 && (
        <Line
          points={courseWindow.points}
          color={autopilotStatus === 'engaged' ? '#ffc23d' : '#7ec8ff'}
          lineWidth={autopilotStatus === 'engaged' ? 3 : 2}
          transparent
          opacity={0.9}
          dashed={autopilotStatus !== 'engaged'}
          dashScale={8}
          dashSize={0.6}
          gapSize={0.35}
        />
      )}
      {showCourse && autopilotCourse && autopilotCourse.hops.map((hop, i) => {
        const pos = coursePositions.get(hop.sector_id);
        if (!pos) return null;
        const done = i < currentHopIndex;
        const active = i === currentHopIndex && autopilotStatus === 'engaged';
        return (
          <mesh key={`course-hop-${hop.sector_id}`} position={pos}>
            <sphereGeometry args={[active ? 1.35 : 0.85, 12, 12]} />
            <meshBasicMaterial
              color={done ? '#3dff7a' : active ? '#ffc23d' : '#7ec8ff'}
              transparent
              opacity={done ? 0.35 : active ? 0.95 : 0.55}
              depthWrite={false}
            />
          </mesh>
        );
      })}

      {allRenderNodes.map((node) => {
        const position = sectorPositions.get(node.sector_id);
        if (!position) return null;

        const asSector: Sector = {
          id: node.sector_id,
          sector_id: node.sector_id,
          name: node.name,
          type: node.type,
          hazard_level: 0,
          radiation_level: 0,
          resources: {},
          players_present: [],
          special_formations: node.special_formations ?? [],
          x_coord: node.x,
          y_coord: node.y,
          z_coord: node.z,
        };

        return (
          <SectorNode3D
            key={node.sector_id}
            sector={asSector}
            position={position}
            isSelected={selectedSector?.sector_id === node.sector_id}
            isCurrent={node.knowledge === 'current'}
            knowledge={node.knowledge}
            clickable={node.clickable}
            onClick={handleSectorClick}
            lodLevel={lodLevel}
            playerCount={
              node.knowledge === 'current' ? sectorPlayers.length : 0
            }
          />
        );
      })}

      {currentSector && (() => {
        const connections: ReactElement[] = [];
        const drawn = new Set<string>();

        const pushEdge = (
          fromId: number,
          toId: number,
          type: 'warp' | 'tunnel',
          key: string,
        ) => {
          if (!visibleIds.has(fromId) || !visibleIds.has(toId)) return;
          const a = sectorPositions.get(fromId);
          const b = sectorPositions.get(toId);
          if (!a || !b) return;
          const edgeKey = [fromId, toId].sort((x, y) => x - y).join('-') + type;
          if (drawn.has(edgeKey)) return;
          drawn.add(edgeKey);
          connections.push(
            <ConnectionPath3D
              key={key}
              start={a}
              end={b}
              type={type}
              lodLevel={lodLevel}
            />,
          );
        };

        // Full trail edges only when not in exits-only mode.
        if (!exitsOnly) {
          chart?.edges.forEach((e, i) => {
            pushEdge(e.from, e.to, e.kind === 'tunnel' ? 'tunnel' : 'warp', `chart-${i}`);
          });
        }

        availableMoves.warps.forEach((w, i) => {
          pushEdge(currentSector.sector_id, w.sector_id, 'warp', `hop-w-${i}`);
        });
        availableMoves.tunnels.forEach((t, i) => {
          pushEdge(currentSector.sector_id, t.sector_id, 'tunnel', `hop-t-${i}`);
        });

        if (!exitsOnly && revealFrontier && chart?.frontier) {
          chart.frontier.forEach((f, i) => {
            if (!visibleIds.has(f.from) || !visibleIds.has(f.id)) return;
            const a = sectorPositions.get(f.from);
            const b = sectorPositions.get(f.id);
            if (!a || !b) return;
            connections.push(
              <ConnectionPath3D
                key={`fog-${i}`}
                start={a}
                end={b}
                type="tunnel"
                lodLevel={lodLevel}
              />,
            );
          });
        }

        return connections;
      })()}

      {isConnected && currentSector && sectorPlayers.length > 0 && (() => {
        const currentPosition = sectorPositions.get(currentSector.sector_id);
        if (!currentPosition) return null;

        return sectorPlayers.map((player, index) => (
          <PlayerMarker3D
            key={`${player.user_id}-${currentSector.sector_id}`}
            player={player}
            position={currentPosition.clone().add(new Vector3(
              (index - sectorPlayers.length / 2) * 2,
              (index % 2) * 2,
              0,
            ))}
            lodLevel={lodLevel}
          />
        ));
      })()}
    </group>
  );
}

export default function Galaxy3DRenderer({
  className,
  onSectorSelect,
  chart,
}: Galaxy3DRendererProps) {
  const controlsRef = useRef<OrbitControlsImpl | null>(null);
  const defaultHome = cameraHomeForExtent(LOCAL_FIT_RADIUS);
  const [recenterToken, setRecenterToken] = useState(0);
  const [exitsOnly, setExitsOnly] = useState(false);
  const [pendingCourse, setPendingCourse] = useState<CourseReachable | null>(null);
  const [plottingRemote, setPlottingRemote] = useState(false);
  const [plotError, setPlotError] = useState<string | null>(null);
  const { plotCourse, engage } = useAutopilot();

  const handleRemoteChartedSelect = useCallback(async (sectorId: number) => {
    setPlotError(null);
    setPlottingRemote(true);
    setPendingCourse(null);
    try {
      const plot = await plotCourse(sectorId);
      if (!plot) {
        setPlotError('Plot failed — ARIA could not chart that course.');
        return;
      }
      if (plot.reachable === false) {
        // Remote 3D clicks only fire for sectors already on the chart — a
        // refusal here is almost always no directed route (one-way warps),
        // not "uncharted". The old copy ("Beyond charted space") contradicted
        // the map the pilot was looking at.
        const nearest =
          plot.nearest_known
          && plot.nearest_known.sector_id !== sectorId
            ? ` Nearest approach you can reach: ${plot.nearest_known.name} (#${plot.nearest_known.sector_id}).`
            : '';
        if (plot.reason === 'uncharted' || plot.reason === 'unknown_sector' || plot.error === 'unknown sector') {
          setPlotError(`Beyond charted space.${nearest}`.trim());
        } else {
          setPlotError(
            `No known route from here — one-way warps or missing links in charted space.${nearest}`.trim(),
          );
        }
        return;
      }
      if (plot.hops.length === 0) {
        setPlotError('Already at that sector, Commander.');
        return;
      }
      setPendingCourse(plot);
    } finally {
      setPlottingRemote(false);
    }
  }, [plotCourse]);

  const handleConfirmCourse = useCallback(() => {
    if (!pendingCourse) return;
    const cmd = ariaEngageCommand(pendingCourse.target_sector_id);
    ariaFeed.appendUserEcho(cmd);
    ariaFeed.appendNav(
      `Course committed — ${pendingCourse.hops.length} hop${pendingCourse.hops.length === 1 ? '' : 's'}, ${pendingCourse.total_turns} turns. Engaging.`,
    );
    setPendingCourse(null);
    setPlotError(null);
    // Always show the full trail while flying a multi-hop course.
    setExitsOnly(false);
    // Course already in AutopilotContext from plotCourse — engage immediately.
    engage();
  }, [pendingCourse, engage]);

  const handleCancelCourse = useCallback(() => {
    setPendingCourse(null);
    setPlotError(null);
  }, []);

  return (
    <div className={`galaxy-3d-container ${className || ''}`}>
      <Canvas
        camera={{
          position: defaultHome.toArray() as [number, number, number],
          fov: 50,
          near: 0.1,
          far: 2000,
        }}
        gl={{
          antialias: true,
          powerPreference: 'default',
          preserveDrawingBuffer: false,
          alpha: false,
        }}
        shadows={false}
        dpr={Math.min(typeof window !== 'undefined' ? window.devicePixelRatio : 1, 1.5)}
        style={{
          position: 'absolute',
          inset: 0,
          width: '100%',
          height: '100%',
          background: '#000010',
          display: 'block',
        }}
      >
        <ambientLight intensity={0.25} />
        <pointLight position={[80, 80, 80]} intensity={0.7} />
        <pointLight position={[-60, -40, -60]} intensity={0.25} color="#4444ff" />

        <OrbitControls
          ref={controlsRef}
          makeDefault
          enablePan={true}
          enableZoom={true}
          enableRotate={true}
          enableDamping={false}
          autoRotate={false}
          zoomSpeed={0.6}
          panSpeed={0.8}
          rotateSpeed={0.4}
          minDistance={6}
          maxDistance={480}
          maxPolarAngle={Math.PI * 0.85}
          target={[0, 0, 0]}
        />

        <Suspense fallback={null}>
          <GalaxyScene
            onSectorSelect={onSectorSelect}
            onRemoteChartedSelect={handleRemoteChartedSelect}
            controlsRef={controlsRef}
            chartProp={chart}
            recenterToken={recenterToken}
            exitsOnly={exitsOnly}
          />
        </Suspense>
      </Canvas>

      <div className="galaxy-ui-overlay" aria-hidden={false}>
        <div className="galaxy-controls galaxy-controls--nav">
          <button
            type="button"
            className="control-button"
            onClick={() => setRecenterToken((n) => n + 1)}
            title="Re-center on your current sector"
            aria-label="Re-center on current sector"
          >
            Center
          </button>
          <button
            type="button"
            className={`control-button${exitsOnly ? ' control-button--active' : ''}`}
            onClick={() => {
              setExitsOnly((v) => !v);
              setRecenterToken((n) => n + 1);
            }}
            title={exitsOnly
              ? 'Show visited fog-of-war trail and known chart'
              : 'Show only current sector and immediate warp exits'}
            aria-pressed={exitsOnly}
            aria-label={exitsOnly ? 'Show visited fog trail' : 'Show exits only'}
          >
            {exitsOnly ? 'Show all' : 'Exits only'}
          </button>
        </div>
      </div>

      {(pendingCourse || plottingRemote || plotError) && (
        <div className="course-confirm-layer">
          {pendingCourse ? (
            <CourseConfirmPopup
              course={pendingCourse}
              plotting={plottingRemote}
              onConfirm={handleConfirmCourse}
              onCancel={handleCancelCourse}
            />
          ) : (
            <div className="course-confirm course-confirm--status" role="status">
              {plottingRemote ? (
                <p className="course-confirm__status-msg">ARIA plotting course…</p>
              ) : (
                <>
                  <p className="course-confirm__status-msg">{plotError}</p>
                  <button
                    type="button"
                    className="course-confirm__cancel"
                    onClick={handleCancelCourse}
                  >
                    Dismiss
                  </button>
                </>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
