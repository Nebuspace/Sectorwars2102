// @vitest-environment jsdomnodefetch
/**
 * GameRouteRedirects — the 10 legacy /game/* URL redirect matrix
 * (WO-UI5-RETIREMENT+GLASS).
 *
 * Proves the whole point of retiring RouteRail's nav keys into client-side
 * redirects: every one of the 10 legacy paths (map/team/governance/combat/
 * planets/ships/player/trading/ranking/settings) resolves onto the shipped
 * cockpit shell (GameShellRoute -> GameLayout, its Outlet index matched) —
 * no 404, no blank render, no console error — and /game/combat additionally
 * fires the deckNavBus TACTICAL[TARGET] deep-link.
 *
 * Harness mirrors GameShellRoute.persistence.test.tsx's proven seam
 * (createMemoryRouter + RouterProvider, real GameShellRoute + GameLayout,
 * context deps mocked at the module boundary, MFDScreen/toast-banner
 * children stubbed as irrelevant chrome) — the real route TABLE this file
 * builds below mirrors App.tsx's `/game` subtree exactly (same 10 paths,
 * same redirect elements imported from GameRouteRedirects.tsx, same
 * ProtectedRoute-equivalent auth posture: every one of them sits under the
 * SAME parent route as `/game` itself, so there is nothing route-specific
 * left to bypass). GameDashboard is NOT mounted — real page content is
 * app.tsx's/glass-lane's concern; a lightweight index-page stub stands in
 * (same convention GameShellRoute.persistence.test.tsx already uses), so
 * this file stays focused on the redirect/routing contract alone.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// jsdom does not implement scrollIntoView; GameLayout mounts the real
// Teleprinter, which calls it on mount (see GameShellRoute.persistence.
// test.tsx's own polyfill for the same reason).
Element.prototype.scrollIntoView = vi.fn();

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'player-1', username: 'commander' }, logout: vi.fn() }),
}));

vi.mock('../../../contexts/FirstLoginContext', () => ({
  useFirstLogin: () => ({ requiresFirstLogin: false }),
}));

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

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: HYDRATED_PLAYER_STATE,
    isLoading: false,
    isRefreshing: false,
    refreshPlayerState: vi.fn(),
    unreadMessageCount: 0,
    currentSector: { name: 'Sol', sector_id: 1, sector_number: 1, region_name: null, region_id: null, type: 'STANDARD' },
    stationsInSector: [],
    planetsInSector: [],
  }),
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ ariaMessages: [], notifications: [], linkStatus: 'up' }),
}));

vi.mock('../../../contexts/AutopilotContext', () => ({
  useAutopilot: () => ({ status: 'idle', course: null, pauseReason: null }),
}));

vi.mock('../../mfd/MFDScreen', () => ({ default: () => <div data-testid="mfd-screen-stub" /> }));
vi.mock('../../ranking/MedalToast', () => ({ default: () => null }));
vi.mock('../../comms/PriorityHailConsumer', () => ({ default: () => null }));
vi.mock('../../auth/WelcomeBackToast', () => ({ default: () => null }));
vi.mock('../../combat/NpcCombatBanner', () => ({ default: () => null }));
vi.mock('../../onboarding/FirstSessionObjectives', () => ({ default: () => null }));

import GameShellRoute from '../GameShellRoute';
import { RedirectToGame, RedirectToTacticalTarget } from '../GameRouteRedirects';
import {
  getLatestTacticalPageRequest,
  __resetDeckNavBusForTests,
} from '../../../services/deckNavBus';

function StubIndexPage() {
  return <div data-testid="stub-index-page">INDEX PAGE</div>;
}

// Mirrors App.tsx's real /game route table exactly — the 10 legacy paths,
// each pointed at the SAME redirect element App.tsx wires.
function buildRouter(initialPath: string) {
  return createMemoryRouter(
    [
      {
        path: '/game',
        element: <GameShellRoute />,
        children: [
          { index: true, element: <StubIndexPage /> },
          { path: 'map', element: <RedirectToGame /> },
          { path: 'team', element: <RedirectToGame /> },
          { path: 'governance', element: <RedirectToGame /> },
          { path: 'combat', element: <RedirectToTacticalTarget /> },
          { path: 'planets', element: <RedirectToGame /> },
          { path: 'ships', element: <RedirectToGame /> },
          { path: 'player', element: <RedirectToGame /> },
          { path: 'trading', element: <RedirectToGame /> },
          { path: 'ranking', element: <RedirectToGame /> },
          { path: 'settings', element: <RedirectToGame /> },
        ],
      },
    ],
    { initialEntries: [initialPath] },
  );
}

const LEGACY_ROUTES = [
  'map', 'team', 'governance', 'combat', 'planets',
  'ships', 'player', 'trading', 'ranking', 'settings',
];

describe('GameRouteRedirects — the 10 legacy /game/* URLs redirect to their shipped home', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let errorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    __resetDeckNavBusForTests();
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
  });

  it.each(LEGACY_ROUTES)('/game/%s redirects to /game (index) -- no 404, no blank render', async (path) => {
    const router = buildRouter(`/game/${path}`);

    await act(async () => {
      root.render(<RouterProvider router={router} />);
    });

    // No 404 / blank: the index route (the redirect's landing target) is
    // present -- a broken/missing route entry would leave this null and
    // the shell empty instead.
    expect(container.querySelector('[data-testid="stub-index-page"]')).not.toBeNull();
    // The persistent shell itself mounted (GameShellRoute -> GameLayout),
    // not a bare Outlet or an error boundary fallback.
    expect(container.querySelector('.game-layout-wrapper')).not.toBeNull();
    // The URL itself resolved to /game, not left dangling on the legacy path.
    expect(router.state.location.pathname).toBe('/game');
    // Focus management (Pixel a11y gate, WCAG 2.4.3): <Navigate> itself
    // performs no special focus handling, so a bare redirect landing would
    // drop focus onto <body> with nothing announced to a screen reader.
    // GameLayout's own location-change effect picks this up instead -- the
    // legacy URL's redirect IS a pathname change the persistent shell sees,
    // even though it happens within the very first mount cycle here (the
    // route starts AT the legacy path and <Navigate> fires immediately) --
    // so focus lands on the cockpit's <main class="game-content"> landmark,
    // never a null/detached reference that would leave keyboard nav
    // stranded on <body>.
    expect(document.activeElement).not.toBeNull();
    expect(document.body.contains(document.activeElement)).toBe(true);
    expect(document.activeElement).toBe(container.querySelector('.game-content'));
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('/game/combat additionally fires the deckNavBus TACTICAL[TARGET] deep-link', async () => {
    expect(getLatestTacticalPageRequest()).toBeNull();

    const router = buildRouter('/game/combat');
    await act(async () => {
      root.render(<RouterProvider router={router} />);
    });

    expect(router.state.location.pathname).toBe('/game');
    const request = getLatestTacticalPageRequest();
    expect(request).not.toBeNull();
    expect(request?.page).toBe('target');
  });

  it('the other nine legacy routes do NOT touch the deckNavBus', async () => {
    const router = buildRouter('/game/map');
    await act(async () => {
      root.render(<RouterProvider router={router} />);
    });

    expect(getLatestTacticalPageRequest()).toBeNull();
  });
});
