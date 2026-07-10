// @vitest-environment jsdom
/**
 * GalaxyMap — NAV CHART on real data (WO-PUX-NAVCHART).
 *
 * GalaxyMap.tsx used to derive its map from a synthetic adjacency ring
 * around the current sector (":43 -- 'Simulated data'"). This pins the
 * replacement: the map now renders the player's real known graph from
 * GET /api/v1/nav/chart, frontier stubs render dimmed/id-only, and
 * selecting a sector previews a course via AutopilotContext.plotCourse
 * (never re-derived client-side) -- with the existing single-hop Travel
 * button preserved for sectors directly adjacent to the player's current
 * position, and the new "Lay in course" flow used for non-adjacent known
 * sectors.
 *
 * Mirrors GatewrightPanel.beaconCancel.test.tsx's seam: jsdom + react-dom/
 * client createRoot + act(), no RTL, no new deps. GameLayout and
 * Galaxy3DRenderer are stubbed -- GalaxyMap always mounts GameLayout as
 * page chrome (out of scope here, mirrors Dashboard.icons.test.tsx's
 * UserProfile stub) and the 3D renderer is never actually rendered in the
 * default 2D view mode this file exercises.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const mockGetChart = vi.fn();
const mockGetPlanets = vi.fn();
const mockGetStations = vi.fn();

vi.mock('../../../services/api', () => ({
  navAPI: { getChart: (...a: unknown[]) => mockGetChart(...a) },
  sectorAPI: {
    getPlanets: (...a: unknown[]) => mockGetPlanets(...a),
    getStations: (...a: unknown[]) => mockGetStations(...a),
  },
}));

vi.mock('../../layouts/GameLayout', () => ({
  default: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock('../../galaxy/Galaxy3DRenderer', () => ({
  default: () => <div data-testid="galaxy-3d-stub" />,
}));

const mockGetAvailableMoves = vi.fn();
const mockMoveToSector = vi.fn();
const mockScanForLatentTunnels = vi.fn();

const CURRENT_SECTOR = {
  id: 10, sector_id: 10, name: 'Sol', type: 'STANDARD',
  hazard_level: 0, radiation_level: 0, resources: {}, players_present: [],
};

let availableMoves: { warps: unknown[]; tunnels: unknown[] } = { warps: [], tunnels: [] };

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: { credits: 1000, turns: 500, current_sector_id: 10 },
    currentSector: CURRENT_SECTOR,
    availableMoves,
    getAvailableMoves: mockGetAvailableMoves,
    moveToSector: mockMoveToSector,
    scanForLatentTunnels: mockScanForLatentTunnels,
  }),
}));

// Mutable mock-hook state (mirrors TurnEconomyPage.lowTurns.test.tsx /
// "Mutable mock hook state across rerenders"): plotCourse mutates this
// object synchronously so the very next render (triggered by GalaxyMap's
// own setSelectedSector state update in the same click handler) reads the
// fresh course/lastPlot without needing a second forced re-render.
let autopilotState: {
  course: unknown;
  lastPlot: unknown;
  status: string;
  plotCourse: (id: number) => Promise<void>;
  engage: () => void;
};
const mockEngage = vi.fn();
let mockPlotCourse: ReturnType<typeof vi.fn>;

vi.mock('../../../contexts/AutopilotContext', () => ({
  useAutopilot: () => autopilotState,
}));

const CHART = {
  sectors: [
    { sector_id: 10, name: 'Sol', type: 'STANDARD', x: 0, y: 0, z: 0, visited: true, current: true },
    { sector_id: 11, name: 'Adjacent Reach', type: 'STANDARD', x: 10, y: 0, z: 0, visited: true, current: false },
    { sector_id: 12, name: 'Distant Nebula', type: 'NEBULA', x: 40, y: 25, z: 0, visited: false, current: false },
  ],
  edges: [
    { from: 10, to: 11, kind: 'warp' },
    { from: 11, to: 10, kind: 'warp' },
  ],
  frontier: [999],
};

import GalaxyMap from '../GalaxyMap';

describe('GalaxyMap — NAV CHART on real data', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGetChart.mockReset();
    mockGetPlanets.mockReset();
    mockGetStations.mockReset();
    mockGetAvailableMoves.mockReset();
    mockMoveToSector.mockReset();
    mockScanForLatentTunnels.mockReset();
    mockEngage.mockReset();

    mockGetChart.mockResolvedValue(CHART);
    mockGetPlanets.mockResolvedValue({ planets: [] });
    mockGetStations.mockResolvedValue({ stations: [] });
    availableMoves = {
      warps: [{ sector_id: 11, name: 'Adjacent Reach', type: 'STANDARD', turn_cost: 1, can_afford: true }],
      tunnels: [],
    };

    mockPlotCourse = vi.fn((targetSectorId: number) => {
      autopilotState.lastPlot = {
        success: true,
        reachable: true,
        target_sector_id: targetSectorId,
        hops: [
          { sector_id: 13, name: 'Waypoint', turn_cost: 3, visited: false, safety_rating: null, via_tunnel: false },
          { sector_id: targetSectorId, name: 'Distant Nebula', turn_cost: 4, visited: false, safety_rating: null, via_tunnel: false },
        ],
        total_turns: 7,
      };
      return Promise.resolve();
    });
    autopilotState = {
      course: null,
      lastPlot: null,
      status: 'idle',
      plotCourse: mockPlotCourse,
      engage: mockEngage,
    };

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  const flush = async () => {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  const click = async (el: Element) => {
    await act(async () => {
      el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
  };

  const mount = async () => {
    await act(async () => {
      root.render(<GalaxyMap />);
    });
    await flush();
  };

  it('renders every sector from the real known-graph chart, not a synthetic ring', async () => {
    await mount();

    expect(mockGetChart).toHaveBeenCalled();
    for (const s of CHART.sectors) {
      expect(container.querySelector(`[data-testid="sector-node-${s.sector_id}"]`)).toBeTruthy();
    }
    // The frontier id must never render as a full sector node.
    expect(container.querySelector('[data-testid="sector-node-999"]')).toBeNull();
  });

  it('renders frontier stubs dimmed and id-only, with no leakage of name/type', async () => {
    await mount();

    const chip = container.querySelector('[data-testid="frontier-chip-999"]');
    expect(chip).toBeTruthy();
    expect(chip!.textContent).toBe('999');
    expect(chip!.className).toContain('frontier-chip');
    // No frontier contents ever fetched or rendered -- id only.
    expect(mockGetPlanets).not.toHaveBeenCalledWith(999);
    expect(mockGetStations).not.toHaveBeenCalledWith(999);
  });

  it('selecting a non-adjacent known sector previews the course via plotCourse and never re-derives cost locally', async () => {
    await mount();

    await click(container.querySelector('[data-testid="sector-node-12"]')!);

    expect(mockPlotCourse).toHaveBeenCalledWith(12);
    const preview = container.querySelector('[data-testid="course-preview"]');
    expect(preview).toBeTruthy();
    expect(preview!.textContent).toContain('Hops: 2');
    expect(preview!.textContent).toContain('Turn cost: 7');
    // Non-adjacent -- the old single-hop Travel button must NOT appear.
    expect(container.querySelector('[data-testid="travel-to-sector"]')).toBeNull();
  });

  it('"Lay in course" invokes plotCourse with the selected sector id', async () => {
    await mount();
    await click(container.querySelector('[data-testid="sector-node-12"]')!);
    mockPlotCourse.mockClear();

    const layInCourseBtn = container.querySelector('[data-testid="lay-in-course"]') as HTMLButtonElement;
    expect(layInCourseBtn).toBeTruthy();
    await click(layInCourseBtn);

    expect(mockPlotCourse).toHaveBeenCalledWith(12);
    expect(mockMoveToSector).not.toHaveBeenCalled();
  });

  it('selecting a directly-adjacent known sector keeps the existing single-hop Travel button (no plotCourse)', async () => {
    await mount();

    await click(container.querySelector('[data-testid="sector-node-11"]')!);

    expect(mockPlotCourse).not.toHaveBeenCalled();
    const travelBtn = container.querySelector('[data-testid="travel-to-sector"]') as HTMLButtonElement;
    expect(travelBtn).toBeTruthy();
    expect(container.querySelector('[data-testid="lay-in-course"]')).toBeNull();

    await click(travelBtn);
    expect(mockMoveToSector).toHaveBeenCalledWith(11);
  });
});
