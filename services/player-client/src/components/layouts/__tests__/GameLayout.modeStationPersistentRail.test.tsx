// @vitest-environment jsdom
/**
 * GameLayout — `mode-station` real styling + the MFD rail persists docked
 * (WO-UI3-STATION-MODE).
 *
 * `mode-station` (GameLayout.tsx ~L295) used to be a real-but-unstyled hook
 * ("carries no styling of its own yet") — game-layout.css now scopes the
 * station-face rules under `.game-container.mode-station`, so this pins the
 * class itself lands on `.game-container` while docked (and NOT while
 * flying/landed — `mode-flight`/`mode-surface` are mutually exclusive
 * siblings, never co-applied). Also pins the "carried slate" invariant the
 * WO calls out explicitly: the MFD rail (RouteRail + both MFDScreens) is
 * NOT gated by is_docked/is_landed in GameLayout.tsx — it renders
 * unconditionally for all three modes — so this asserts it stays mounted
 * docked exactly as it does flying.
 *
 * Harness mirrors the other GameLayout.*.test.tsx files' proven seam
 * exactly (GameLayout itself is the SUT, unmocked; RouteRail/MFDScreen/
 * toast-banner children stubbed as irrelevant chrome; `<div/>` stands in for
 * GameDashboard — this file is scoped to GameLayout's OWN shell, not what
 * GameDashboard renders inside it, which is covered separately by
 * GameDashboard.dockedStationFace.test.tsx).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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

describe('GameLayout — mode-station real styling + MFD rail persists docked (WO-UI3-STATION-MODE)', () => {
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

  const renderWith = async (playerStateOverrides: Record<string, unknown>) => {
    gameState = buildGameState(playerStateOverrides);
    await act(async () => {
      root.render(
        <MemoryRouter>
          <GameLayout>
            <div />
          </GameLayout>
        </MemoryRouter>
      );
    });
  };

  it('docked: .game-container carries mode-station (never mode-flight/mode-surface)', async () => {
    await renderWith({ is_docked: true });

    expect(container.querySelector('.game-container.mode-station')).not.toBeNull();
    expect(container.querySelector('.game-container.mode-flight')).toBeNull();
    expect(container.querySelector('.game-container.mode-surface')).toBeNull();
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('docked: the MFD rail (RouteRail + both MFDScreens) stays mounted — the "carried slate"', async () => {
    await renderWith({ is_docked: true });

    expect(container.querySelector('[data-testid="route-rail-stub"]')).not.toBeNull();
    expect(container.querySelectorAll('[data-testid="mfd-screen-stub"]').length).toBe(2);
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('flying: mode-flight, not mode-station — invariant holds', async () => {
    await renderWith({ is_docked: false, is_landed: false });

    expect(container.querySelector('.game-container.mode-flight')).not.toBeNull();
    expect(container.querySelector('.game-container.mode-station')).toBeNull();
    // Same rail, same three modes — the persistence invariant flying too.
    expect(container.querySelector('[data-testid="route-rail-stub"]')).not.toBeNull();
    expect(container.querySelectorAll('[data-testid="mfd-screen-stub"]').length).toBe(2);
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('landed: mode-surface, not mode-station — invariant holds', async () => {
    await renderWith({ is_landed: true });

    expect(container.querySelector('.game-container.mode-surface')).not.toBeNull();
    expect(container.querySelector('.game-container.mode-station')).toBeNull();
    expect(container.querySelector('[data-testid="route-rail-stub"]')).not.toBeNull();
    expect(container.querySelectorAll('[data-testid="mfd-screen-stub"]').length).toBe(2);
    expect(errorSpy).not.toHaveBeenCalled();
  });
});
