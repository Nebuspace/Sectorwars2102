// @vitest-environment jsdom
/**
 * GameDashboard — deck content mounts EXACTLY ONCE under a REAL (unmocked)
 * GameLayout (regression guard, adversarial-review catch WO-UI0-SHELL-
 * TRANSPLANT / Mack).
 *
 * Every other GameDashboard.*.test.tsx mocks GameLayout out entirely
 * (`vi.mock('../../layouts/GameLayout', () => ({ default: ({children}) =>
 * <div>{children}</div> }))`), which made them blind to a real bug: the
 * original `bandEl ? createPortal(node, bandEl) : node` fallback pattern
 * (GameDashboard.tsx) flips the element TYPE at that JSX position across
 * GameLayout's own null->non-null `bandEl`/`deckEl` transition (a plain
 * host element vs. a ReactPortal are different types to React's
 * reconciler) — React does not preserve identity across a type change, so
 * it unmounted the whole inline-rendered subtree and mounted a fresh one
 * through the portal, on every real session start. For the deck side this
 * meant NavigationMap and TacticalMonitor — both carry real mount-effects
 * (data fetches / subscriptions) — silently double-fired.
 *
 * The fix (GameLayout.tsx) makes `bandEl`/`deckEl` non-null from
 * GameLayout's own FIRST render (`useState(() => document.createElement
 * ('div'))`, not a callback-ref-driven `useState(null)`), so the type at
 * that JSX position never changes in production. This test proves the
 * FULL, REAL path end to end: a genuine <GameLayout><GameDashboard/>
 * </GameLayout> mount, counting NavigationMap + TacticalMonitor's own
 * mount effects.
 *
 * NavigationMap is stubbed with a mount-counting wrapper (real NavigationMap
 * runs a D3 force-layout simulation over many frames — GameDashboard.
 * navMultihop.test.tsx already owns proving ITS behavior; this file only
 * needs to know how many times React committed it). TacticalMonitor is
 * spy-wrapped around the REAL component (importOriginal partial mock, the
 * same pattern GameLayout.teleprinterAnnunciatorIntegration.test.tsx uses
 * for Teleprinter/Annunciator) — GameDashboard.deckReconcile.test.tsx
 * already mounts it unmocked successfully, so it's known-cheap to mount
 * for real here too, and doing so for at least one of the two proves the
 * fix isn't merely stub-deep.
 *
 * Mock harness merges two proven seams: GameDashboard.deckReconcile.
 * test.tsx's (services/api, react-router-dom, sub-component stubs, game
 * state shape) and GameLayout.teleprinterAnnunciatorIntegration.test.tsx's
 * (the context fields GameLayout's own hooks need) — GameLayout itself is
 * NOT mocked (the entire point), but Annunciator/Teleprinter/
 * MFDScreen inside it are stubbed as irrelevant chrome, keeping this file
 * focused on the one thing it exists to prove.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockGetChart = vi.fn();
const mockGetMyRegion = vi.fn();
const mockSectorWrecks = vi.fn();
vi.mock('../../../services/api', () => ({
  navAPI: { getChart: (...a: unknown[]) => mockGetChart(...a) },
  regionOwnerAPI: { getMyRegion: (...a: unknown[]) => mockGetMyRegion(...a) },
  sectorAPI: {
    sectorWrecks: (...a: unknown[]) => mockSectorWrecks(...a),
    salvageWreck: vi.fn(),
  },
  combatAPI: { engage: vi.fn(), getStatus: vi.fn() },
  greyStatusAPI: { getStatus: () => Promise.resolve({ isGrey: false, kind: null, greyUntil: null, remainingSeconds: 0, clearFineCredits: null }) },
  planetaryAPI: { getOwnedPlanets: () => Promise.resolve({ planets: [] }) },
}));

vi.mock('react-router-dom', () => ({
  useNavigate: () => vi.fn(),
  // GameLayout is mounted for REAL here (the whole point of this file) and
  // now calls useLocation() for its redirect-focus-management effect
  // (Pixel a11y gate, WCAG 2.4.3) -- a static pathname is enough since this
  // file never navigates; it only needs the call to not throw.
  useLocation: () => ({ pathname: '/game' }),
}));

// GameLayout is DELIBERATELY NOT mocked — that's the entire point of this file.
vi.mock('../../trading/TradingInterface', () => ({ default: () => <div /> }));
vi.mock('../../spacedock/SpaceDockInterface', () => ({ default: () => <div /> }));
vi.mock('../../spacedock/PortOfficeVenue', () => ({ default: () => <div /> }));
vi.mock('../../spacedock/ContractBoardVenue', () => ({ default: () => <div /> }));
vi.mock('../../planetary/PopulationCenterInterface', () => ({ default: () => <div /> }));
vi.mock('../../tactical/SolarSystemViewscreen', () => ({ default: () => <div /> }));
// WO-UI2-WINDSHIELD-TABLEAU: flight-mode mount is now WindshieldTableau.
vi.mock('../../tactical/WindshieldTableau', () => ({ default: () => <div /> }));
vi.mock('../../tactical/PlanetPortPair', () => ({ default: () => <div /> }));
vi.mock('../../galaxy/Galaxy3DRenderer', () => ({ default: () => <div /> }));
vi.mock('../../quantum/QuantumDriveConsole', () => ({ default: () => <div /> }));
vi.mock('../../gatewright/GatewrightPanel', () => ({ default: () => <div /> }));
vi.mock('../../governance/CitizenshipBadge', () => ({ default: () => <div /> }));
vi.mock('../../governance/RegionInvitePanel', () => ({ default: () => <div /> }));
vi.mock('../../governance/RegionTradeDockPanel', () => ({ default: () => <div /> }));
vi.mock('../../cockpit/CockpitColonyManagement', () => ({ default: () => <div /> }));
vi.mock('../../cockpit/SafeVaultPanel', () => ({ default: () => <div /> }));

vi.mock('../../../hooks/useResourceCatalog', () => ({
  useResourceCatalog: () => ({ catalog: [], loading: false, getLabel: (n: string) => n, getIcon: () => '📦', getColor: () => '#fff' }),
}));

// GameLayout's own chrome — stubbed as irrelevant (this file's SUT is the
// portal boundary + deck content mount count, not the shell's own skin).
vi.mock('../../layouts/StatusBar', () => ({ default: () => <div data-testid="statusbar-stub" /> }));
vi.mock('../../aria/Teleprinter', () => ({ default: () => <div data-testid="teleprinter-stub" /> }));
vi.mock('../../hud/Annunciator', () => ({ default: () => <div data-testid="annunciator-stub" /> }));
vi.mock('../../mfd/MFDScreen', () => ({ default: () => <div data-testid="mfd-screen-stub" /> }));
vi.mock('../../ranking/MedalToast', () => ({ default: () => null }));
vi.mock('../../comms/PriorityHailConsumer', () => ({ default: () => null }));
vi.mock('../../auth/WelcomeBackToast', () => ({ default: () => null }));
vi.mock('../../combat/NpcCombatBanner', () => ({ default: () => null }));
vi.mock('../../onboarding/FirstSessionObjectives', () => ({ default: () => null }));

// ── Mount-counting spies (the whole point of this file) ─────────────────
let navMapMountCount = 0;
vi.mock('../../tactical/NavigationMap', () => ({
  // Named (not anonymous) so react-hooks/rules-of-hooks' component-name
  // heuristic recognizes this as a real component (QUEUE-ESLINT-FLATCONFIG,
  // 2026-07-16) -- see GameShellRoute.persistence.test.tsx's identical fix.
  default: function MockNavigationMap() {
    React.useEffect(() => {
      navMapMountCount++;
    }, []);
    return <div data-testid="navmap-stub" />;
  },
}));

let tacticalMountCount = 0;
vi.mock('../../tactical/TacticalMonitor', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../tactical/TacticalMonitor')>();
  const Real = mod.default;
  type Props = React.ComponentProps<typeof Real>;
  const Spy: React.FC<Props> = (props) => {
    React.useEffect(() => {
      tacticalMountCount++;
    }, []);
    return <Real {...props} />;
  };
  return { ...mod, default: Spy };
});

const SECTOR_100 = {
  id: 100, sector_id: 100, sector_number: 100, name: 'Sol', type: 'STANDARD',
  region_id: null, region_name: null, hazard_level: 0, radiation_level: 0,
  resources: {}, players_present: [], special_features: [],
  special_formations: [],
  description: 'A quiet stretch of charted space.',
};

function makeGameState(overrides: Record<string, unknown> = {}) {
  return {
    playerState: {
      id: 'player-1', username: 'tester', credits: 1000, turns: 50, max_turns: 500,
      current_sector_id: 100, is_docked: false, is_landed: false,
      defense_drones: 0, attack_drones: 0, mines: 0,
      personal_reputation: 0, reputation_tier: 'Neutral', name_color: '#fff',
      military_rank: 'Cadet',
    },
    currentShip: {
      id: 'ship-1', name: 'Tester', type: 'SCOUT', sector_id: 100,
      cargo: {}, cargo_capacity: 100, current_speed: 1, base_speed: 1,
      combat: {}, maintenance: {}, is_flagship: true, purchase_value: 0,
      current_value: 0, genesis_devices: 0, max_genesis_devices: 0,
    },
    currentSector: SECTOR_100,
    planetsInSector: [],
    stationsInSector: [],
    availableMoves: { warps: [], tunnels: [] },
    unreadMessageCount: 0,
    isLoading: false,
    isRefreshing: false,
    moveToSector: vi.fn().mockResolvedValue({}),
    dockAtStation: vi.fn(),
    undockFromStation: vi.fn(),
    claimPlanet: vi.fn(),
    landOnPlanet: vi.fn(),
    leavePlanet: vi.fn(),
    renamePlanet: vi.fn(),
    getPlanetDetails: vi.fn().mockResolvedValue(null),
    transferColonists: vi.fn(),
    updatePlanetAllocation: vi.fn(),
    getCitadelInfo: vi.fn().mockResolvedValue(null),
    upgradeCitadel: vi.fn(),
    cancelCitadelUpgrade: vi.fn(),
    getDefenseBuildings: vi.fn().mockResolvedValue({ buildings: [] }),
    buildDefenseBuilding: vi.fn(),
    depositToSafe: vi.fn(),
    withdrawFromSafe: vi.fn(),
    depositCommodityToSafe: vi.fn(),
    withdrawCommodityFromSafe: vi.fn(),
    setCitadelAutoDeposit: vi.fn(),
    getPlanetDefenseInfo: vi.fn().mockResolvedValue(null),
    upgradeShields: vi.fn(),
    exploreCurrentLocation: vi.fn().mockResolvedValue(undefined),
    getAvailableMoves: vi.fn().mockResolvedValue(undefined),
    refreshPlayerState: vi.fn().mockResolvedValue(undefined),
    sendPlayerMessage: vi.fn(),
    deployMines: vi.fn(),
    updatePlayerCredits: vi.fn(),
    quantumStatus: null,
    refineQuantumCharge: vi.fn(),
    markMessageRead: vi.fn().mockResolvedValue(undefined),
    error: null,
    ...overrides,
  };
}

let gameState: ReturnType<typeof makeGameState>;
vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => gameState,
}));

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'player-1', username: 'tester' }, logout: vi.fn() }),
}));

vi.mock('../../../contexts/AutopilotContext', () => ({
  useAutopilot: () => ({
    course: null, lastPlot: null, status: 'idle', pauseReason: null,
    currentHopIndex: 0, plotCourse: vi.fn(), engage: vi.fn(), abort: vi.fn(),
  }),
}));

vi.mock('../../../contexts/FirstLoginContext', () => ({
  useFirstLogin: () => ({ requiresFirstLogin: false }),
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({
    sectorPlayers: [],
    ariaMessages: [],
    notifications: [],
    linkStatus: 'up',
    sendARIAMessage: vi.fn(),
    isConnected: true,
    npcCombatSignal: 0,
    lastNpcCombatInitiated: null,
    newMessageSignal: 0,
    lastNewMessage: null,
  }),
}));

import GameLayout from '../../layouts/GameLayout';
import GameDashboard from '../GameDashboard';

describe('GameDashboard — deck content mounts exactly once under a real GameLayout', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let errorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    mockGetChart.mockReset();
    mockGetMyRegion.mockReset();
    mockGetMyRegion.mockRejectedValue(new Error('not a region owner'));
    mockGetChart.mockResolvedValue({ sectors: [], edges: [], frontier: [] });
    mockSectorWrecks.mockReset();
    mockSectorWrecks.mockResolvedValue([]);

    navMapMountCount = 0;
    tacticalMountCount = 0;
    gameState = makeGameState();

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    errorSpy.mockRestore();
    vi.restoreAllMocks();
  });

  const flush = async () => {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  it('NavigationMap and TacticalMonitor each mount exactly once (no fiber-type-flip double-mount through the band/deck portals)', async () => {
    await act(async () => {
      root.render(
        <GameLayout>
          <GameDashboard />
        </GameLayout>
      );
    });
    await flush();

    // TACTICAL is always mounted; NAV defaults to its COURSE page
    // (NavigationMap only renders under the CHART tab, WO-UI2-DECK-
    // RECONCILE) — switch to CHART so NavigationMap actually mounts.
    expect(container.querySelector('.deck .mon.tactical-monitor')).not.toBeNull();
    expect(tacticalMountCount).toBe(1);
    expect(navMapMountCount).toBe(0);

    const chartTab = container.querySelector('#nav-tab-chart') as HTMLButtonElement;
    expect(chartTab, 'expected a #nav-tab-chart button').not.toBeNull();
    await act(async () => {
      chartTab.click();
    });
    await flush();

    // The deck content is genuinely portaled into `.deck` (proves the real
    // path ran, not some inert fallback).
    expect(container.querySelector('.deck [data-testid="navmap-stub"]')).not.toBeNull();

    expect(navMapMountCount).toBe(1);
    // TACTICAL is unaffected by the NAV tab switch -- still exactly one mount.
    expect(tacticalMountCount).toBe(1);

    expect(errorSpy).not.toHaveBeenCalled();
  });
});
