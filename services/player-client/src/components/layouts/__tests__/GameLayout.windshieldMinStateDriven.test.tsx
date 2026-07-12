// @vitest-environment jsdom
/**
 * GameLayout — windshield-minimize mount-state fix (WO-UI-WINDSHIELD-MIN-
 * STATE-DRIVEN).
 *
 * Bug: `windshieldMin` initialized to a hardcoded `false` and only flipped
 * on the dock/undock EDGE (`prevGroundedRef` transition). A page reload
 * while ALREADY docked/landed mounts with `grounded===true` on the very
 * first render — no edge ever fires, so the windshield stayed expanded
 * instead of starting minimized.
 *
 * Fix: seed `windshieldMin` from `grounded` at mount (`useState(grounded)`
 * instead of `useState(false)`), matching `prevGroundedRef`'s existing
 * mount-time seed — so a mount-while-grounded starts minimized, while the
 * edge-effect (later transitions) and the manual toggle are untouched.
 *
 * REJECTED alternative (do not resurrect): making `windshieldMin` fully
 * derived (`= grounded`, no state) forces the value every render and
 * breaks the manual expand-while-docked toggle — the edge-effect exists
 * specifically so "the manual toggle isn't fought while the player stays
 * grounded" (see GameLayout.tsx's own comment above the effect). This
 * suite's second case is the regression guard proving the toggle still
 * works post-fix.
 *
 * Signal used: the `.windshield-expand` ("Expand Viewport") button renders
 * ONLY while grounded && windshieldMin; `.windshield-minimize` ("Minimize
 * Viewport") renders ONLY while grounded && !windshieldMin (GameLayout.tsx
 * ~L376-395) — mutually exclusive, so asserting which one is present is a
 * direct, unambiguous read of the `windshieldMin` state. Also asserted:
 * the `.game-container.windshield-min` class GameLayout derives from the
 * same state (~L319), which game-layout.css's grid math keys off.
 *
 * Mock harness mirrors GameLayout.statusBarIntegration.test.tsx exactly
 * (GameLayout itself is the SUT, unmocked; RouteRail/MFDScreen/toast-
 * banner children stubbed as irrelevant chrome) — proven zero-console-
 * error real-GameLayout mount. `playerState` is a mutable module-level
 * `let gameState`, mirroring GameDashboard.overlapChipsRetired.test.tsx's
 * pattern, so each test controls is_docked at mount time (and across a
 * rerender, for the transition regression case) without needing distinct
 * mock modules per test.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// jsdom does not implement scrollIntoView; the real (unmocked) Teleprinter
// calls it on mount — see GameLayout.statusBarIntegration.test.tsx.
Element.prototype.scrollIntoView = vi.fn();

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'player-1', username: 'commander' }, logout: vi.fn() }),
}));

function makePlayerState(overrides: Record<string, unknown> = {}) {
  return {
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
    ...overrides,
  };
}

// Mutable per-test — reassigned in each `it()` before the first render (and
// again mid-test for the transition case), read lazily by the mocked hook
// below so a rerender picks up the change.
let gameState: ReturnType<typeof buildGameState>;
function buildGameState(playerStateOverrides: Record<string, unknown> = {}) {
  return {
    playerState: makePlayerState(playerStateOverrides),
    isLoading: false,
    isRefreshing: false,
    refreshPlayerState: vi.fn(),
    unreadMessageCount: 0,
    currentSector: { name: 'Sol', sector_id: 1, sector_number: 1, region_name: null, region_id: null, type: 'STANDARD' },
    stationsInSector: [],
    planetsInSector: [],
  };
}

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => gameState,
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ ariaMessages: [], notifications: [], linkStatus: 'up' }),
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

import GameLayout from '../GameLayout';

describe('GameLayout — windshield-minimize is state-driven at mount (WO-UI-WINDSHIELD-MIN-STATE-DRIVEN)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let errorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
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

  it('mount-while-already-docked starts minimized (the reload-while-docked bug)', async () => {
    gameState = buildGameState({ is_docked: true });

    await act(async () => {
      root.render(
        <MemoryRouter>
          <GameLayout>
            <div />
          </GameLayout>
        </MemoryRouter>
      );
    });

    expect(container.querySelector('.game-container.windshield-min')).not.toBeNull();
    expect(container.querySelector('.windshield-expand')).not.toBeNull();
    expect(container.querySelector('.windshield-minimize')).toBeNull();
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('manual toggle still works post-fix: expand-while-docked, then minimize again', async () => {
    gameState = buildGameState({ is_docked: true });

    await act(async () => {
      root.render(
        <MemoryRouter>
          <GameLayout>
            <div />
          </GameLayout>
        </MemoryRouter>
      );
    });

    // Starts minimized (mount-while-docked).
    const expandBtn = container.querySelector('.windshield-expand') as HTMLButtonElement;
    expect(expandBtn).not.toBeNull();

    // Manual expand — the regression guard the derived-value fix would have
    // broken (a derived `windshieldMin = grounded` would re-force `true`
    // every render and the button below would never appear).
    await act(async () => {
      expandBtn.click();
    });
    expect(container.querySelector('.game-container.windshield-min')).toBeNull();
    const minimizeBtn = container.querySelector('.windshield-minimize') as HTMLButtonElement;
    expect(minimizeBtn).not.toBeNull();
    expect(container.querySelector('.windshield-expand')).toBeNull();

    // Manual minimize back — toggle is fully bidirectional while grounded.
    await act(async () => {
      minimizeBtn.click();
    });
    expect(container.querySelector('.game-container.windshield-min')).not.toBeNull();
    expect(container.querySelector('.windshield-expand')).not.toBeNull();
    expect(container.querySelector('.windshield-minimize')).toBeNull();

    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('enter-dock TRANSITION (mounted undocked, becomes docked) still auto-minimizes — no regression', async () => {
    gameState = buildGameState({ is_docked: false, is_landed: false });

    await act(async () => {
      root.render(
        <MemoryRouter>
          <GameLayout>
            <div />
          </GameLayout>
        </MemoryRouter>
      );
    });

    // Not grounded — neither windshield button renders, no windshield-min class.
    expect(container.querySelector('.game-container.windshield-min')).toBeNull();
    expect(container.querySelector('.windshield-expand')).toBeNull();
    expect(container.querySelector('.windshield-minimize')).toBeNull();

    // Dock — the edge-effect (unchanged by this fix) should still fire.
    gameState = buildGameState({ is_docked: true });
    await act(async () => {
      root.render(
        <MemoryRouter>
          <GameLayout>
            <div />
          </GameLayout>
        </MemoryRouter>
      );
    });

    expect(container.querySelector('.game-container.windshield-min')).not.toBeNull();
    expect(container.querySelector('.windshield-expand')).not.toBeNull();
    expect(container.querySelector('.windshield-minimize')).toBeNull();

    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('undock/lift-off restores the windshield (windshieldMin false) — no regression', async () => {
    gameState = buildGameState({ is_docked: true });

    await act(async () => {
      root.render(
        <MemoryRouter>
          <GameLayout>
            <div />
          </GameLayout>
        </MemoryRouter>
      );
    });

    expect(container.querySelector('.windshield-expand')).not.toBeNull();

    gameState = buildGameState({ is_docked: false, is_landed: false });
    await act(async () => {
      root.render(
        <MemoryRouter>
          <GameLayout>
            <div />
          </GameLayout>
        </MemoryRouter>
      );
    });

    expect(container.querySelector('.game-container.windshield-min')).toBeNull();
    expect(container.querySelector('.windshield-expand')).toBeNull();
    expect(container.querySelector('.windshield-minimize')).toBeNull();

    expect(errorSpy).not.toHaveBeenCalled();
  });
});
