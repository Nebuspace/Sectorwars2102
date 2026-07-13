// @vitest-environment jsdom
/**
 * GameShellRoute + GameLayout — persistent-shell state survives navigation
 * (WO-UI0-PERSISTENT-SHELL lane A).
 *
 * Before this lane, every /game/* route was a flat, independent <Route>
 * rendering its own page (which self-wraps <GameLayout>) — a navigation
 * between two pages unmounted and remounted the ENTIRE shell (sidebar,
 * HUD, toasts, MFDs), resetting every piece of GameLayout-local React
 * state. Lane A nests all 11 pages under ONE
 * <Route path="/game" element={<GameShellRoute />}> so GameLayout mounts
 * once and only the Outlet's matched child swaps on navigation.
 *
 * This pins that TRUE resetting set across a real react-router navigation
 * (/game -> /game/map, via a real data router — createMemoryRouter +
 * RouterProvider, not a mocked useNavigate): the "INITIALIZING SYSTEMS"
 * loading latch, a manual teleprinter display-mode change (GameLayout-owned
 * `teleprinterDisplayMode` state -- the sidebar-collapse toggle this proof
 * used to drive was retired, WO-UI5-RETIREMENT+GLASS; teleprinterDisplayMode
 * is now the only remaining piece of GameLayout-local, user-triggerable
 * state), and a mount-only effect on a shell-persistent child all survive
 * the nav, while the Outlet's own content genuinely swaps.
 *
 * GameLayout itself is the SUT and is NOT mocked. Its context deps
 * (useAuth/useGame/useWebSocket/useAutopilot) and GameShellRoute's own
 * useFirstLogin are mocked at the module boundary. useGame is backed by a
 * REAL React Context + Provider local to this test (GameTestProvider
 * below), not a static mutable-`let` read inside the factory: react-router's
 * data router memoizes the matched-route element subtree on its OWN state
 * (matches), so re-`root.render()`-ing with an unrelated mutated mock value
 * is silently swallowed (the memoized subtree bails out, exactly the
 * memo-bailout trap in [[mutable-mock-hook-state-across-rerenders]]) --
 * confirmed by a live repro before landing this version. A genuine
 * Context-value change (via the provider's own `setState`) propagates to
 * every consuming fiber regardless of that memoization, exactly like the
 * real GameProvider does in production. MFDScreen and the 5
 * always-mounted toast/banner children (MedalToast, PriorityHailConsumer,
 * WelcomeBackToast, NpcCombatBanner, FirstSessionObjectives) are stubbed --
 * irrelevant chrome for this proof. MedalToast's stub doubles as the
 * mount-counter probe: it sits inside GameLayout's persistent JSX (not the
 * Outlet/children slot), so if GameLayout ever remounted across the nav, it
 * would remount too.
 */
import React, { act, createContext, useContext, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// jsdom does not implement scrollIntoView; GameLayout now ALSO mounts the
// real Teleprinter (WO-UI1-TELEPRINTER stitch), which calls it on mount
// (see Teleprinter.smoke.test.tsx). Without this polyfill Teleprinter
// throws in jsdom, which React Router's route error boundary catches --
// replacing this whole file's expected render tree with an error fallback
// (surfaces as unrelated-looking "expected null not to be null" failures
// on .viewport-loading-overlay/.game-layout-wrapper). Orthogonal to what
// this file actually tests (persistent-shell state across navigation).
Element.prototype.scrollIntoView = vi.fn();

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'player-1', username: 'commander' }, logout: vi.fn() }),
}));

let mockRequiresFirstLogin = false;
vi.mock('../../../contexts/FirstLoginContext', () => ({
  useFirstLogin: () => ({ requiresFirstLogin: mockRequiresFirstLogin }),
}));

// Real Context (not a static mocked return) -- see the file doc-comment for
// why: it's the one piece of state this test mutates WITHOUT a real
// navigation, and only a genuine Context propagation survives react-router's
// internal matched-route memoization.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const GameTestContext = createContext<any>(null);
vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => useContext(GameTestContext),
}));

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let mockWebSocketValue: any = {};
vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => mockWebSocketValue,
}));

vi.mock('../../../contexts/AutopilotContext', () => ({
  useAutopilot: () => ({ status: 'idle', course: null, pauseReason: null }),
}));

vi.mock('../../mfd/MFDScreen', () => ({
  default: () => <div data-testid="mfd-screen-stub" />,
}));

let medalToastMountCount = 0;
vi.mock('../../ranking/MedalToast', () => ({
  default: () => {
    React.useEffect(() => {
      medalToastMountCount++;
    }, []);
    return null;
  },
}));

vi.mock('../../comms/PriorityHailConsumer', () => ({ default: () => null }));
vi.mock('../../auth/WelcomeBackToast', () => ({ default: () => null }));
vi.mock('../../combat/NpcCombatBanner', () => ({ default: () => null }));
vi.mock('../../onboarding/FirstSessionObjectives', () => ({ default: () => null }));

import GameShellRoute from '../GameShellRoute';

function StubIndexPage() {
  return <div data-testid="stub-index-page">INDEX PAGE</div>;
}
function StubMapPage() {
  return <div data-testid="stub-map-page">MAP PAGE</div>;
}

const HYDRATED_PLAYER_STATE = {
  id: 'player-1',
  username: 'commander',
  credits: 10_000,
  turns: 400,
  max_turns: 500,
  current_sector_id: 1,
  is_docked: false,
  is_landed: false,
  defense_drones: 0,
  attack_drones: 0,
  mines: 0,
  personal_reputation: 0,
  reputation_tier: 'Neutral',
  name_color: '#00D9FF',
  military_rank: 'Cadet',
};

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type GameTestValue = Record<string, any>;

/** Real Context.Provider wrapper -- exposes its own setState to the test via `onReady`. */
function GameTestProvider({
  initial,
  onReady,
  children,
}: {
  initial: GameTestValue;
  onReady: (setState: React.Dispatch<React.SetStateAction<GameTestValue>>) => void;
  children: React.ReactNode;
}) {
  const [value, setValue] = useState<GameTestValue>(initial);
  onReady(setValue);
  return <GameTestContext.Provider value={value}>{children}</GameTestContext.Provider>;
}

function buildGameRouter() {
  return createMemoryRouter(
    [
      {
        path: '/game',
        element: <GameShellRoute />,
        children: [
          { index: true, element: <StubIndexPage /> },
          { path: 'map', element: <StubMapPage /> },
        ],
      },
    ],
    { initialEntries: ['/game'] },
  );
}

describe('GameShellRoute + GameLayout — persistent shell across navigation', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let setGameState: React.Dispatch<React.SetStateAction<GameTestValue>>;

  beforeEach(() => {
    mockRequiresFirstLogin = false;
    medalToastMountCount = 0;
    mockWebSocketValue = { ariaMessages: [], notifications: [], linkStatus: 'up' };
    setGameState = () => {
      throw new Error('setGameState read before GameTestProvider mounted');
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
  });

  it('keeps GameLayout (and its internal state) mounted exactly once across a /game -> /game/map nav', async () => {
    const router = buildGameRouter();

    // ── Phase 0: initial hydration in flight (playerState still null) ──
    await act(async () => {
      root.render(
        <GameTestProvider
          initial={{
            playerState: null,
            isLoading: true,
            isRefreshing: false,
            refreshPlayerState: vi.fn(),
            unreadMessageCount: 0,
            currentSector: null,
            stationsInSector: [],
          }}
          onReady={(setter) => {
            setGameState = setter;
          }}
        >
          <RouterProvider router={router} />
        </GameTestProvider>,
      );
    });
    expect(container.querySelector('.viewport-loading-overlay')).not.toBeNull();
    expect(container.querySelector('[data-testid="stub-index-page"]')).not.toBeNull();
    expect(medalToastMountCount).toBe(1);

    // ── Phase 1: hydration completes -- hasLoadedOnce latches true ──
    await act(async () => {
      setGameState((prev) => ({ ...prev, playerState: HYDRATED_PLAYER_STATE, isLoading: false }));
    });
    expect(container.querySelector('.viewport-loading-overlay')).toBeNull();
    expect(medalToastMountCount).toBe(1); // a Context-value change, not a remount

    // ── Phase 2: manually change the teleprinter display mode ──
    // (the sidebar-collapse toggle this proof used to drive is retired,
    // WO-UI5-RETIREMENT+GLASS -- teleprinterDisplayMode is the only
    // remaining piece of GameLayout-owned, user-triggerable local state to
    // prove survives the nav). The ticker row's "▲ LOG" key
    // (`.tp-ticker-log`) is always in the DOM and calls
    // `onDisplayModeChange('full-overlay')`, which GameLayout mirrors onto
    // the Teleprinter root as `tp-full-overlay`.
    const overlayBtn = container.querySelector('.tp-ticker-log') as HTMLButtonElement;
    expect(overlayBtn).not.toBeNull();
    await act(async () => {
      overlayBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="teleprinter"].tp-full-overlay')).not.toBeNull();

    // Pre-nav baseline: nothing has focused the main landmark yet (a fresh
    // mount must NOT steal focus -- see the redirect-focus-management
    // doc-comment on GameLayout's location-change effect).
    const mainLandmark = container.querySelector('.game-content');
    expect(document.activeElement).not.toBe(mainLandmark);

    // ── Phase 3: navigate to the sibling route ──
    await act(async () => {
      await router.navigate('/game/map');
    });

    // (4) the deck slot (Outlet) swapped -- new page in, old page out --
    // while the shell itself never unmounted across the swap.
    expect(container.querySelector('[data-testid="stub-map-page"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="stub-index-page"]')).toBeNull();
    expect(container.querySelector('.game-layout-wrapper')).not.toBeNull();

    // (5) Pixel a11y gate (WCAG 2.4.3): a real /game/* navigation moves
    // focus onto the cockpit's own <main class="game-content"> landmark
    // instead of dropping it on <body> -- proves GameRouteRedirects.tsx's
    // legacy-URL <Navigate> landings are announced, not silent.
    expect(document.activeElement).toBe(mainLandmark);

    // (3) exactly one mount of the persistent shell across the whole nav.
    expect(medalToastMountCount).toBe(1);

    // (2) the manual teleprinter display-mode change survived the navigation.
    expect(container.querySelector('[data-testid="teleprinter"].tp-full-overlay')).not.toBeNull();

    // (1) the loading latch survived the navigation: even if isLoading
    // flips true again post-nav, hasLoadedOnce (GameLayout-internal state
    // that a remount would have reset to false) keeps the overlay from
    // reappearing.
    await act(async () => {
      setGameState((prev) => ({ ...prev, isLoading: true }));
    });
    expect(container.querySelector('.viewport-loading-overlay')).toBeNull();
    expect(medalToastMountCount).toBe(1);
  });
});

describe('GameShellRoute — first-login gate', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockRequiresFirstLogin = false;
    medalToastMountCount = 0;
    mockWebSocketValue = { ariaMessages: [], notifications: [], linkStatus: 'up' };

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  const HYDRATED_GAME_VALUE: GameTestValue = {
    playerState: HYDRATED_PLAYER_STATE,
    isLoading: false,
    isRefreshing: false,
    refreshPlayerState: vi.fn(),
    unreadMessageCount: 0,
    currentSector: null,
    stationsInSector: [],
  };

  it('renders a bare Outlet with NO shell chrome while requiresFirstLogin is true', async () => {
    mockRequiresFirstLogin = true;
    const router = buildGameRouter();

    await act(async () => {
      root.render(
        <GameTestProvider initial={HYDRATED_GAME_VALUE} onReady={() => {}}>
          <RouterProvider router={router} />
        </GameTestProvider>,
      );
    });

    expect(container.querySelector('.game-layout-wrapper')).toBeNull();
    expect(container.querySelector('[data-testid="stub-index-page"]')).not.toBeNull();
  });

  it('renders GameLayout wrapping the Outlet once requiresFirstLogin is false', async () => {
    mockRequiresFirstLogin = false;
    const router = buildGameRouter();

    await act(async () => {
      root.render(
        <GameTestProvider initial={HYDRATED_GAME_VALUE} onReady={() => {}}>
          <RouterProvider router={router} />
        </GameTestProvider>,
      );
    });

    expect(container.querySelector('.game-layout-wrapper')).not.toBeNull();
    expect(container.querySelector('[data-testid="stub-index-page"]')).not.toBeNull();
  });
});
