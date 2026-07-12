// @vitest-environment jsdom
/**
 * GameLayout — Teleprinter + Annunciator P1 stitch (WO-UI1-TELEPRINTER /
 * WO-UI1-ANNUNCIATOR serial mount).
 *
 * Pins the three structural facts the stitch is FOR:
 *   1. `<Teleprinter/>` renders as a DIRECT, non-absolute child of
 *      `.game-container` (`.game-container > .teleprinter`) — same
 *      precondition as StatusBar (game-layout.css:100-104's reserved
 *      `teleprinter` grid-area only fills for a real, non-absolute child).
 *   2. `<Annunciator/>` renders INSIDE the new `.windshield-hud-anchor`
 *      wrapper (a real, non-absolute grid child assigned `grid-area:
 *      windshield`) — NOT inside `.game-content` (which spans the FULL
 *      container height, out-of-grid-flow, for the inverted-L scene; an
 *      Annunciator mounted there would technically extend behind the
 *      statusbar/teleprinter rows too). Confirming Annunciator is absent
 *      from `.game-content`'s subtree entirely is the SCENE-NARROWING
 *      structural check: jsdom has no real layout engine (getBoundingClientRect
 *      always reads zeros), so "the overlay does not change `.game-content`'s
 *      box" is proven by the overlay not being IN that box's subtree at all
 *      — a stronger guarantee than a zeroed layout diff would be. The
 *      Orchestrator's 1440×900 Playwright pass is the pixel-geometry proof.
 *   3. Both mount EXACTLY ONCE across an unrelated GameLayout re-render (a
 *      children-slot swap) — no remount/re-flash. Proven via `vi.mock`'s
 *      `importOriginal` partial-mock: the REAL Teleprinter/Annunciator still
 *      render (per the WO — these are the shipped, a11y-gated components,
 *      not stubs), wrapped in a thin mount-counting effect at the test
 *      boundary only (their own source files are untouched).
 *
 * Mock harness mirrors GameLayout.statusBarIntegration.test.tsx's proven
 * seam (GameLayout itself is the SUT, not mocked; RouteRail/MFDScreen/the
 * toast-banner children stubbed as irrelevant chrome), extended with the
 * WebSocketContext/GameContext fields Annunciator/Teleprinter need (per
 * their own Annunciator.test.tsx / Teleprinter.smoke.test.tsx seams) — all
 * driven inert/quiet (zero signals, zero hazard/low-turns) so neither
 * component renders an active lamp or fetches anything during this passive
 * structural-mount proof.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// jsdom does not implement scrollIntoView — Teleprinter calls it on mount/
// update (see Teleprinter.smoke.test.tsx); polyfill so it doesn't log a
// "Not implemented" console error and poison the zero-console-error checks.
Element.prototype.scrollIntoView = vi.fn();

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'player-1', username: 'commander' }, logout: vi.fn() }),
}));

const HYDRATED_PLAYER_STATE = {
  id: 'player-1',
  username: 'commander',
  credits: 10_000,
  turns: 400, // well above the <50 low-turns threshold — TURNS lamp inert
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
    // hazard_level: 0 -- well below Annunciator's >0 HAZARD trigger, lamp inert
    currentSector: { name: 'Sol', sector_id: 1, sector_number: 1, region_name: null, region_id: null, type: 'STANDARD', hazard_level: 0 },
    stationsInSector: [],
    planetsInSector: [],
    markMessageRead: vi.fn().mockResolvedValue(undefined),
  }),
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({
    ariaMessages: [],
    notifications: [],
    linkStatus: 'up',
    sendARIAMessage: vi.fn(),
    isConnected: true,
    // All zero/null -- both EVENT lamps (COMBAT/COMM) stay inert.
    npcCombatSignal: 0,
    lastNpcCombatInitiated: null,
    newMessageSignal: 0,
    lastNewMessage: null,
  }),
}));

vi.mock('../../../contexts/AutopilotContext', () => ({
  useAutopilot: () => ({ status: 'idle', course: null, pauseReason: null }),
}));

vi.mock('../../mfd/RouteRail', () => ({ default: () => <div data-testid="route-rail-stub" /> }));
vi.mock('../../mfd/MFDScreen', () => ({ default: () => <div data-testid="mfd-screen-stub" /> }));
vi.mock('../../ranking/MedalToast', () => ({ default: () => null }));
vi.mock('../../comms/PriorityHailConsumer', () => ({ default: () => null }));
vi.mock('../../auth/WelcomeBackToast', () => ({ default: () => null }));
vi.mock('../../combat/NpcCombatBanner', () => ({ default: () => null }));
vi.mock('../../onboarding/FirstSessionObjectives', () => ({ default: () => null }));

// ── Mount-counting spies around the REAL components (importOriginal partial
// mock — neither Teleprinter.tsx nor Annunciator.tsx is touched or replaced,
// only wrapped with a thin mount-effect at this test's module boundary). ──
let teleprinterMountCount = 0;
vi.mock('../../aria/Teleprinter', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../aria/Teleprinter')>();
  const Spy: React.FC = () => {
    React.useEffect(() => {
      teleprinterMountCount++;
    }, []);
    const Real = actual.default;
    return <Real />;
  };
  return { ...actual, default: Spy };
});

let annunciatorMountCount = 0;
vi.mock('../../hud/Annunciator', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../hud/Annunciator')>();
  const Spy: React.FC = () => {
    React.useEffect(() => {
      annunciatorMountCount++;
    }, []);
    const Real = actual.default;
    return <Real />;
  };
  return { ...actual, default: Spy };
});

import GameLayout from '../GameLayout';

describe('GameLayout — Teleprinter + Annunciator stitch', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let errorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    teleprinterMountCount = 0;
    annunciatorMountCount = 0;
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

  it('mounts Teleprinter as a direct child of .game-container (the reserved teleprinter grid-area precondition), zero console errors', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <GameLayout>
            <div data-testid="route-content">ROUTE CONTENT</div>
          </GameLayout>
        </MemoryRouter>
      );
    });

    const teleprinterEl = container.querySelector('.game-container > .teleprinter');
    expect(teleprinterEl).not.toBeNull();
    // Same element (Teleprinter.tsx's root carries both the class and the
    // test id), not a descendant -- confirms it's the real component, not
    // an accidental empty `.teleprinter`-classed wrapper.
    expect(teleprinterEl?.getAttribute('data-testid')).toBe('teleprinter');

    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('mounts Annunciator inside .windshield-hud-anchor (a direct .game-container grid child), never inside .game-content', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <GameLayout>
            <div />
          </GameLayout>
        </MemoryRouter>
      );
    });

    const gameContainer = container.querySelector('.game-container');
    expect(gameContainer?.querySelector(':scope > .windshield-hud-anchor')).not.toBeNull();
    expect(
      container.querySelector('.windshield-hud-anchor [data-testid="annunciator-overlay"]')
    ).not.toBeNull();

    // SCENE-NARROWING structural check: Annunciator is entirely ABSENT from
    // .game-content's subtree -- the full-bleed scene layer's own box is
    // therefore untouched by this addition (nothing was added inside it).
    expect(
      container.querySelector('.game-content [data-testid="annunciator-overlay"]')
    ).toBeNull();

    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('mounts both exactly once across an unrelated re-render (children-slot swap) -- no remount/re-flash', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <GameLayout>
            <div data-testid="route-a">A</div>
          </GameLayout>
        </MemoryRouter>
      );
    });
    expect(teleprinterMountCount).toBe(1);
    expect(annunciatorMountCount).toBe(1);

    // Re-render with DIFFERENT children -- a real update, not a fresh mount
    // (same root, same top-level element identity).
    await act(async () => {
      root.render(
        <MemoryRouter>
          <GameLayout>
            <div data-testid="route-b">B</div>
          </GameLayout>
        </MemoryRouter>
      );
    });

    expect(container.querySelector('[data-testid="route-b"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="route-a"]')).toBeNull();
    // The persistent shell children never remounted across the swap.
    expect(teleprinterMountCount).toBe(1);
    expect(annunciatorMountCount).toBe(1);

    expect(errorSpy).not.toHaveBeenCalled();
  });
});
