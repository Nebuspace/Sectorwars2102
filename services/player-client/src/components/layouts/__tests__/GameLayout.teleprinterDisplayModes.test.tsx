// @vitest-environment jsdom
/**
 * GameLayout — teleprinter display-mode → MFD-B fold stitch
 * (WO-UI1-CHROME-COMPLETE item 2/3).
 *
 * Pins the structural fact the fold is FOR: GameLayout renders EXACTLY the
 * unfolded MFDScreen pair (`sidebar-a` + `sidebar-b`) while the teleprinter
 * is ticker/full-overlay, and swaps to the SINGLE folded config
 * (`sidebar-a-folded`, 5 pageIds == the MFD-A + MFD-B union) the instant
 * the teleprinter enters mid-panel — driven by REAL clicks on the REAL
 * (unmocked) Teleprinter's own single mode-toggle control (WO-UI-MAX-
 * BATCH-1's `.tp-mode-toggle`, cycling ticker→mid-panel→full-overlay→
 * ticker; two DOM instances, `.tkey.tp-mode-toggle` in the ticker row and
 * `.tp-display-btn.tp-mode-toggle` in `#tp-body`), exactly as a player
 * would. Also pins `.game-container.tp-mid-panel` tracking the same
 * state — that class still exists purely for the fold hook now; the
 * band-shrink it used to also drive is REVERTED (game-layout.css's own
 * MID-PANEL BAND CORRECTION comment, Max's ruling — `.band` no longer
 * reacts to it at all).
 *
 * MFDScreen is mocked to reveal its `config` prop (screenId + pageIds)
 * rather than rendering the real MFD console tree (MFDProvider/registry
 * predicates/lazy pages are irrelevant to this structural proof and would
 * just add noise) -- mirrors GameLayout.statusBarIntegration.test.tsx's
 * proven seam (GameLayout itself is the SUT, unmocked).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// jsdom does not implement scrollIntoView -- the real (unmocked) Teleprinter
// calls it on mount/update.
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
    currentSector: { name: 'Sol', sector_id: 1, sector_number: 1, region_name: null, region_id: null, type: 'STANDARD', hazard_level: 0 },
    stationsInSector: [],
    planetsInSector: [],
    dockAtStation: vi.fn(),
    undockFromStation: vi.fn(),
    landOnPlanet: vi.fn(),
    leavePlanet: vi.fn(),
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
    npcCombatSignal: 0,
    lastNpcCombatInitiated: null,
    newMessageSignal: 0,
    lastNewMessage: null,
  }),
}));

vi.mock('../../../contexts/AutopilotContext', () => ({
  useAutopilot: () => ({
    status: 'idle',
    course: null,
    pauseReason: null,
    lastPlot: null,
    plotCourse: vi.fn(),
    engage: vi.fn(),
    abort: vi.fn(),
  }),
}));

vi.mock('../../mfd/MFDScreen', () => ({
  default: ({ config }: { config: { screenId: string; pageIds: string[] } }) => (
    <div data-testid={`mfd-screen-${config.screenId}`} data-page-ids={config.pageIds.join(',')} />
  ),
}));
vi.mock('../../ranking/MedalToast', () => ({ default: () => null }));
vi.mock('../../comms/PriorityHailConsumer', () => ({ default: () => null }));
vi.mock('../../auth/WelcomeBackToast', () => ({ default: () => null }));
vi.mock('../../combat/NpcCombatBanner', () => ({ default: () => null }));
vi.mock('../../onboarding/FirstSessionObjectives', () => ({ default: () => null }));

import GameLayout from '../GameLayout';

describe('GameLayout — teleprinter display mode drives the MFD-B→MFD-A fold', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let errorSpy: ReturnType<typeof vi.spyOn>;

  const flush = async () => {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  };

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

  it('default (ticker): renders the unfolded MFDScreen pair, no fold, no tp-mid-panel class', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <GameLayout>
            <div />
          </GameLayout>
        </MemoryRouter>
      );
    });
    await flush();

    expect(container.querySelector('[data-testid="mfd-screen-sidebar-a"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="mfd-screen-sidebar-b"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="mfd-screen-sidebar-a-folded"]')).toBeNull();
    expect(container.querySelector('.game-container')?.className).not.toContain('tp-mid-panel');
    expect(container.querySelector('.teleprinter')?.className).toContain('tp-ticker');

    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('entering mid-panel (via the single mode toggle) swaps to the SINGLE folded MFD-A config at the 5-key cap; cycling on through full-overlay and back to ticker restores the pair', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <GameLayout>
            <div />
          </GameLayout>
        </MemoryRouter>
      );
    });
    await flush();

    const toggleBtn = container.querySelector('.tkey.tp-mode-toggle') as HTMLButtonElement;
    expect(toggleBtn).not.toBeNull();
    expect(toggleBtn.textContent).toBe('TICKER');
    await act(async () => {
      toggleBtn.click(); // ticker -> mid-panel
    });
    await flush();

    expect(container.querySelector('.teleprinter')?.className).toContain('tp-mid-panel');
    expect(container.querySelector('.game-container')?.className).toContain('tp-mid-panel');

    const folded = container.querySelector('[data-testid="mfd-screen-sidebar-a-folded"]');
    expect(folded).not.toBeNull();
    expect(folded?.getAttribute('data-page-ids')).toBe(
      'vessel-status,cargo,quantum-drive,nav-position,comms-crew'
    );
    // MFD-B doesn't mount at all while folded -- POS/COMM live in the
    // folded MFD-A rail now.
    expect(container.querySelector('[data-testid="mfd-screen-sidebar-a"]')).toBeNull();
    expect(container.querySelector('[data-testid="mfd-screen-sidebar-b"]')).toBeNull();

    // Continue the strict 3-state cycle (WO-UI-MAX-BATCH-1 -- no direct
    // mid-panel->ticker jump any more): mid-panel -> full-overlay unfolds
    // immediately (fold is mid-panel-only, see the sibling test below).
    const bodyToggle = container.querySelector('.tp-display-btn.tp-mode-toggle') as HTMLButtonElement;
    expect(bodyToggle.textContent).toBe('PANEL');
    await act(async () => {
      bodyToggle.click(); // mid-panel -> full-overlay
    });
    await flush();
    expect(container.querySelector('.teleprinter')?.className).toContain('tp-full-overlay');
    expect(container.querySelector('[data-testid="mfd-screen-sidebar-a"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="mfd-screen-sidebar-b"]')).not.toBeNull();
    expect(container.querySelector('.game-container')?.className).not.toContain('tp-mid-panel');

    // full-overlay -> ticker, the pair stays restored.
    const bodyToggle2 = container.querySelector('.tp-display-btn.tp-mode-toggle') as HTMLButtonElement;
    expect(bodyToggle2.textContent).toBe('LOG');
    await act(async () => {
      bodyToggle2.click(); // full-overlay -> ticker
    });
    await flush();

    expect(container.querySelector('.teleprinter')?.className).toContain('tp-ticker');
    expect(container.querySelector('[data-testid="mfd-screen-sidebar-a"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="mfd-screen-sidebar-b"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="mfd-screen-sidebar-a-folded"]')).toBeNull();
    expect(container.querySelector('.game-container')?.className).not.toContain('tp-mid-panel');

    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('full-overlay (reached by cycling the toggle through mid-panel) does NOT fold MFD-B -- fold is mid-panel-only', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <GameLayout>
            <div />
          </GameLayout>
        </MemoryRouter>
      );
    });
    await flush();

    const toggleBtn = container.querySelector('.tkey.tp-mode-toggle') as HTMLButtonElement;
    await act(async () => {
      toggleBtn.click(); // ticker -> mid-panel
    });
    await flush();
    const bodyToggle = container.querySelector('.tp-display-btn.tp-mode-toggle') as HTMLButtonElement;
    await act(async () => {
      bodyToggle.click(); // mid-panel -> full-overlay
    });
    await flush();

    expect(container.querySelector('.teleprinter')?.className).toContain('tp-full-overlay');
    expect(container.querySelector('[data-testid="mfd-screen-sidebar-a"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="mfd-screen-sidebar-b"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="mfd-screen-sidebar-a-folded"]')).toBeNull();
    expect(container.querySelector('.game-container')?.className).not.toContain('tp-mid-panel');

    expect(errorSpy).not.toHaveBeenCalled();
  });
});
