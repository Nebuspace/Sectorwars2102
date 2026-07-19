// @vitest-environment jsdom
/**
 * GameLayout — teleprinter PANEL toggle → MFD-B fold stitch
 * (WO-UI1-CHROME-COMPLETE item 2/3; REVISED by WO-UI-MAX-BATCH-1 REVISE,
 * Max #22-24, for the two-independent-toggle rebuild).
 *
 * Pins the structural fact the fold is FOR: GameLayout renders EXACTLY the
 * unfolded MFDScreen pair (`sidebar-a` + `sidebar-b`) while the teleprinter
 * is in TICKER form, and swaps to the SINGLE folded config
 * (`sidebar-a-folded`, 5 pageIds == the MFD-A + MFD-B union) the instant
 * PANEL opens — driven by a REAL click on the REAL (unmocked) Teleprinter's
 * own persistent `.tp-panel-toggle` button, exactly as a player would. Also
 * pins `.game-container.tp-panel` tracking the same boolean — that class
 * still exists purely for the fold hook (no live CSS rule reads it), and
 * the band-shrink it never drove is confirmed still absent (game-layout.
 * css's own PANEL BAND CORRECTION comment, Max's ruling — `.band` never
 * reacts to PANEL at all). LOG (`transcriptOpen`, the independent toggle)
 * does NOT fold MFD-B — proven separately below.
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

describe('GameLayout — teleprinter PANEL toggle drives the MFD-B→MFD-A fold', () => {
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

  it('default (ticker): renders the unfolded MFDScreen pair, no fold, no tp-panel class', async () => {
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
    expect(container.querySelector('.game-container')?.className).not.toContain('tp-panel');
    expect(container.querySelector('.teleprinter')?.className).toContain('tp-ticker');

    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('opening PANEL (via its own persistent toggle) swaps to the SINGLE folded MFD-A config at the 5-key cap; closing PANEL restores the pair', async () => {
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

    const panelToggle = container.querySelector('.tp-panel-toggle') as HTMLButtonElement;
    expect(panelToggle).not.toBeNull();
    expect(panelToggle.textContent).toBe('PANEL');
    await act(async () => {
      panelToggle.click(); // ticker -> panel
    });
    await flush();

    expect(container.querySelector('.teleprinter')?.className).toContain('tp-panel');
    expect(container.querySelector('.game-container')?.className).toContain('tp-panel');

    const folded = container.querySelector('[data-testid="mfd-screen-sidebar-a-folded"]');
    expect(folded).not.toBeNull();
    expect(folded?.getAttribute('data-page-ids')).toBe(
      'vessel-status,cargo,quantum-drive,nav-position,comms-crew'
    );
    // MFD-B doesn't mount at all while folded -- POS/COMM live in the
    // folded MFD-A rail now.
    expect(container.querySelector('[data-testid="mfd-screen-sidebar-a"]')).toBeNull();
    expect(container.querySelector('[data-testid="mfd-screen-sidebar-b"]')).toBeNull();

    // Same toggle (now labeled TICKER) restores the pair.
    expect(panelToggle.textContent).toBe('TICKER');
    await act(async () => {
      panelToggle.click(); // panel -> ticker
    });
    await flush();

    expect(container.querySelector('.teleprinter')?.className).toContain('tp-ticker');
    expect(container.querySelector('[data-testid="mfd-screen-sidebar-a"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="mfd-screen-sidebar-b"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="mfd-screen-sidebar-a-folded"]')).toBeNull();
    expect(container.querySelector('.game-container')?.className).not.toContain('tp-panel');

    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('LOG (opened via its own independent toggle, PANEL untouched) does NOT fold MFD-B -- the fold is bodyPanel-only', async () => {
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

    const logToggle = container.querySelector('.tp-log-toggle') as HTMLButtonElement;
    await act(async () => {
      logToggle.click(); // LOG open, bodyPanel still false
    });
    await flush();

    expect(container.querySelector('.teleprinter')?.className).toContain('tp-log-open');
    expect(container.querySelector('.teleprinter')?.className).toContain('tp-ticker');
    expect(container.querySelector('[data-testid="mfd-screen-sidebar-a"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="mfd-screen-sidebar-b"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="mfd-screen-sidebar-a-folded"]')).toBeNull();
    expect(container.querySelector('.game-container')?.className).not.toContain('tp-panel');

    expect(errorSpy).not.toHaveBeenCalled();
  });
});
