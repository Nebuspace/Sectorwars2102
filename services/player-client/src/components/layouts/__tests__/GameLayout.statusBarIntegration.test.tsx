// @vitest-environment jsdom
/**
 * GameLayout — StatusBar integration (WO-UI0-STATUSBAR serial integration).
 *
 * Pins the two structural facts the integration step is FOR:
 *   1. `<StatusBar/>` renders as a DIRECT, non-absolute child of
 *      `.game-container` (`.game-container > .status-bar`) — the precondition
 *      for CSS Grid to actually auto-place it into row 1 of the `.stage`
 *      grid (cockpit-shell.css / game-layout.css, WO-UI0-SHELL-TRANSPLANT; a
 *      descendant nested inside `.main-viewport`/`.game-content`, itself
 *      `position:absolute`, would NOT land there — see GameLayout.tsx's own
 *      mount-site comment).
 *   2. `.player-vitals-hud` no longer renders anywhere — PlayerVitalsHud's
 *      mount was retired (superseded by StatusBar), so vitals aren't
 *      duplicated.
 * Plus a zero-console-error mount of the real (unmocked) GameLayout + real
 * StatusBar — the "structural/geometry evidence" a jsdom environment can
 * give without a real layout engine; the Orchestrator's 1440×900 Playwright
 * pass is the pixel-geometry proof.
 *
 * Mock harness mirrors GameShellRoute.persistence.test.tsx's proven seam
 * exactly (GameLayout itself is the SUT, not mocked; MFDScreen/the
 * toast-banner children are stubbed as irrelevant chrome). StatusBar is
 * DELIBERATELY left unmocked — both its dropdowns default closed, so none of
 * the tab content they gate (ReputationPage/ShipSelector/ServiceRecordTab/
 * ColoniesRosterTab/RegionOwnerControls/CitizenshipBadge) ever mounts or
 * fetches in this closed-state proof, so no services/api mocking is needed
 * either.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// jsdom does not implement scrollIntoView; GameLayout now ALSO mounts the
// real Teleprinter (WO-UI1-TELEPRINTER stitch), which calls it on mount
// (see Teleprinter.smoke.test.tsx) -- polyfill so it doesn't throw and
// poison this file's zero-console-error checks. Orthogonal to what this
// file actually tests (StatusBar's mount), but every real-GameLayout mount
// is exposed to it now.
Element.prototype.scrollIntoView = vi.fn();

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'player-1', username: 'commander' }, logout: vi.fn() }),
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

import GameLayout from '../GameLayout';

describe('GameLayout — StatusBar integration', () => {
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

  it('mounts StatusBar as a direct child of .game-container (the CSS Grid statusbar-area precondition), zero console errors', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <GameLayout>
            <div data-testid="route-content">ROUTE CONTENT</div>
          </GameLayout>
        </MemoryRouter>
      );
    });

    const directChild = container.querySelector('.game-container > .status-bar');
    expect(directChild).not.toBeNull();

    // Sibling of the shell's other top-level slots (WO-UI0-SHELL-TRANSPLANT:
    // `.band`/`.lower` supersede the old absolute `.game-sidebar`), not
    // nested inside `.game-content` (position:absolute and thus excluded
    // from grid auto-placement).
    const gameContainer = container.querySelector('.game-container');
    expect(gameContainer?.querySelector(':scope > .band')).not.toBeNull();
    expect(gameContainer?.querySelector(':scope > .lower')).not.toBeNull();
    expect(gameContainer?.querySelector(':scope > .game-content')).not.toBeNull();
    expect(gameContainer?.querySelector(':scope > .status-bar')).not.toBeNull();

    // route content still renders through the shell's children slot
    expect(container.querySelector('[data-testid="route-content"]')).not.toBeNull();

    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('no longer mounts PlayerVitalsHud (superseded by StatusBar — vitals not duplicated)', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <GameLayout>
            <div />
          </GameLayout>
        </MemoryRouter>
      );
    });

    expect(container.querySelector('.player-vitals-hud')).toBeNull();
    // StatusBar's own vitals cluster is present instead.
    expect(container.querySelector('.sb-vitals')).not.toBeNull();

    expect(errorSpy).not.toHaveBeenCalled();
  });
});
