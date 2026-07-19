// @vitest-environment jsdom
/**
 * FirstSessionObjectives / useFirstSession (WO-PUX-ONBOARD). GameContext and
 * WebSocketContext are both mocked to mutable, reassignable objects (mirrors
 * NpcCombatBanner.test.tsx's seam) so playerState/notifications can be driven
 * directly without exercising the real providers. Raw createRoot/act --
 * NO RTL (per the WO override; new deps are Max-gated).
 *
 * The ARIA "orientation started/objective cleared/complete" narration lines
 * live in GameLayout.tsx's MFDAlertWiring (a single ref-diffed effect fed by
 * this same hook's armed/progress/allComplete outputs -- see that file).
 * GameLayout has zero existing test coverage and the WO explicitly asked to
 * keep its diff minimal; this file pins the hook's OWN contract (armed /
 * progress / allComplete transition correctly) that narration effect
 * consumes, verified against the real GameLayout.tsx logic by inspection
 * rather than a mounted GameLayout test.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

interface MockPlayerState {
  id: string;
  is_docked: boolean;
  current_sector_id: number;
  turns: number;
}

let mockGameState: { playerState: MockPlayerState | null };
let mockWsState: { notifications: Array<{ title: string; content?: string; level?: string; timestamp: string }> };

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => mockGameState,
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => mockWsState,
}));

import FirstSessionObjectives from '../FirstSessionObjectives';

const PLAYER_ID = 'player-1';
const ARM_KEY = 'sw:onboarding:armed';
const retiredKey = (id: string) => `sw:onboarding:retired:${id}`;
const progressKey = (id: string) => `sw:onboarding:progress:${id}`;

function makePlayerState(overrides: Partial<MockPlayerState> = {}): MockPlayerState {
  return {
    id: PLAYER_ID,
    is_docked: false,
    current_sector_id: 100,
    turns: 500,
    ...overrides,
  };
}

function findItem(container: HTMLElement, label: string): Element | undefined {
  return Array.from(container.querySelectorAll('.first-session-chip-item')).find((el) =>
    el.textContent?.includes(label)
  );
}

describe('FirstSessionObjectives / useFirstSession', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  const render = () => {
    act(() => {
      root.render(<FirstSessionObjectives />);
    });
  };

  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
    mockGameState = { playerState: null };
    mockWsState = { notifications: [] };
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it('never renders for a non-first-session player (no session-arm flag set)', () => {
    mockGameState = { playerState: makePlayerState() };
    render();
    expect(container.querySelector('.first-session-chip')).toBeNull();
  });

  it('never renders while playerState has not loaded yet, even if armed', () => {
    sessionStorage.setItem(ARM_KEY, '1');
    mockGameState = { playerState: null };
    render();
    expect(container.querySelector('.first-session-chip')).toBeNull();
  });

  it('renders when armed (session flag set + a known player), showing all three objectives', () => {
    sessionStorage.setItem(ARM_KEY, '1');
    mockGameState = { playerState: makePlayerState() };
    render();

    expect(container.querySelector('.first-session-chip')).not.toBeNull();
    expect(container.textContent).toContain('Dock at the station');
    expect(container.textContent).toContain('Make a trade');
    expect(container.textContent).toContain('Travel to a new sector');
    expect(findItem(container, 'Dock at the station')?.classList.contains('first-session-chip-item--done')).toBe(false);
  });

  it('ticks the dock objective when playerState.is_docked becomes true', () => {
    sessionStorage.setItem(ARM_KEY, '1');
    mockGameState = { playerState: makePlayerState({ is_docked: false }) };
    render();

    mockGameState = { playerState: makePlayerState({ is_docked: true }) };
    render();

    expect(findItem(container, 'Dock at the station')?.classList.contains('first-session-chip-item--done')).toBe(true);
    expect(JSON.parse(localStorage.getItem(progressKey(PLAYER_ID)) || '{}').dock).toBe(true);
  });

  it('ticks the trade objective on a "Trade Successful" notification', () => {
    sessionStorage.setItem(ARM_KEY, '1');
    mockGameState = { playerState: makePlayerState() };
    render();

    mockWsState = {
      notifications: [{ title: 'Trade Successful', content: 'Bought 5 ore', level: 'success', timestamp: 't1' }],
    };
    render();

    expect(findItem(container, 'Make a trade')?.classList.contains('first-session-chip-item--done')).toBe(true);
  });

  it('does not tick trade on an unrelated notification', () => {
    sessionStorage.setItem(ARM_KEY, '1');
    mockGameState = { playerState: makePlayerState() };
    render();

    mockWsState = { notifications: [{ title: 'Teammate Under Attack', timestamp: 't1' }] };
    render();

    expect(findItem(container, 'Make a trade')?.classList.contains('first-session-chip-item--done')).toBe(false);
  });

  it('ticks the travel objective when current_sector_id changes from the armed-session baseline', () => {
    sessionStorage.setItem(ARM_KEY, '1');
    mockGameState = { playerState: makePlayerState({ current_sector_id: 100 }) };
    render(); // baseline sector (100) captured here

    mockGameState = { playerState: makePlayerState({ current_sector_id: 101 }) };
    render();

    expect(findItem(container, 'Travel to a new sector')?.classList.contains('first-session-chip-item--done')).toBe(true);
  });

  it('does not tick travel while the sector is unchanged', () => {
    sessionStorage.setItem(ARM_KEY, '1');
    mockGameState = { playerState: makePlayerState({ current_sector_id: 100 }) };
    render();
    render(); // re-render with the same sector

    expect(findItem(container, 'Travel to a new sector')?.classList.contains('first-session-chip-item--done')).toBe(false);
  });

  it('dismiss hides the chip immediately and persists permanently in localStorage', () => {
    sessionStorage.setItem(ARM_KEY, '1');
    mockGameState = { playerState: makePlayerState() };
    render();
    expect(container.querySelector('.first-session-chip')).not.toBeNull();

    const dismissBtn = container.querySelector('.first-session-chip-dismiss') as HTMLButtonElement;
    act(() => {
      dismissBtn.click();
    });

    expect(container.querySelector('.first-session-chip')).toBeNull();
    expect(localStorage.getItem(retiredKey(PLAYER_ID))).toBe('1');
  });

  it('dismiss persists across a full remount -- the chip never comes back for this player', () => {
    sessionStorage.setItem(ARM_KEY, '1');
    localStorage.setItem(retiredKey(PLAYER_ID), '1'); // simulates a PRIOR session's dismiss
    mockGameState = { playerState: makePlayerState() };
    render();

    expect(container.querySelector('.first-session-chip')).toBeNull();
  });

  it('progress survives a remount via localStorage -- an already-ticked objective is not re-required', () => {
    sessionStorage.setItem(ARM_KEY, '1');
    localStorage.setItem(
      progressKey(PLAYER_ID),
      JSON.stringify({ dock: true, trade: false, travel: false })
    );
    mockGameState = { playerState: makePlayerState({ is_docked: false }) };
    render();

    expect(findItem(container, 'Dock at the station')?.classList.contains('first-session-chip-item--done')).toBe(true);
    expect(findItem(container, 'Make a trade')?.classList.contains('first-session-chip-item--done')).toBe(false);
  });

  it('auto-retires (permanently) the instant all three objectives tick', () => {
    sessionStorage.setItem(ARM_KEY, '1');
    mockGameState = { playerState: makePlayerState({ is_docked: false, current_sector_id: 100 }) };
    render();

    mockGameState = { playerState: makePlayerState({ is_docked: true, current_sector_id: 100 }) };
    render();
    mockWsState = { notifications: [{ title: 'Trade Successful', timestamp: 't1' }] };
    render();
    mockGameState = { playerState: makePlayerState({ is_docked: true, current_sector_id: 101 }) };
    render();

    expect(container.querySelector('.first-session-chip')).toBeNull();
    expect(localStorage.getItem(retiredKey(PLAYER_ID))).toBe('1');
  });

  it('a different player id gets independent progress/dismissal state', () => {
    sessionStorage.setItem(ARM_KEY, '1');
    localStorage.setItem(retiredKey('some-other-player'), '1');
    mockGameState = { playerState: makePlayerState({ id: PLAYER_ID }) };
    render();

    // PLAYER_ID was never dismissed -- unaffected by the other player's key.
    expect(container.querySelector('.first-session-chip')).not.toBeNull();
  });
});
