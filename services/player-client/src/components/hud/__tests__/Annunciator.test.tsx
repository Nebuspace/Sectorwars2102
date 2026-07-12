// @vitest-environment jsdom
/**
 * Annunciator (WO-UI1-ANNUNCIATOR sub-part a) — the windshield HUD overlay.
 * WebSocketContext and GameContext are mocked to mutable, reassignable
 * objects (mirrors NpcCombatBanner.test.tsx / PriorityHailConsumer
 * .uplinkToast.test.tsx's seam) so signals/payload/playerState/currentSector
 * can be driven directly without exercising the real providers. window
 * .matchMedia is stubbed per-test to drive the reduced-motion path.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Silences the React 18 "current testing environment is not configured to
// support act(...)" warning -- a harness-level quirk baseline-wide in this
// repo's jsdom+createRoot+act tests (no setupFiles/IS_REACT_ACT_ENVIRONMENT
// in vitest.config.ts), unrelated to this component. Mirrors
// StatusBar.smoke.test.tsx / GameLayout.statusBarIntegration.test.tsx.
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

interface MockNpcCombat {
  defender_id: string;
}
interface MockNewMessage {
  message_id: string;
  delivery: string[];
}

const markMessageRead = vi.fn().mockResolvedValue(undefined);

let mockWsState: {
  npcCombatSignal: number;
  lastNpcCombatInitiated: MockNpcCombat | null;
  newMessageSignal: number;
  lastNewMessage: MockNewMessage | null;
};
let mockGameState: {
  playerState: { id: string; turns: number } | null;
  currentSector: { hazard_level: number } | null;
  markMessageRead: typeof markMessageRead;
};

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => mockWsState,
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => mockGameState,
}));

import Annunciator from '../Annunciator';

const setMatchMedia = (reducedMotion: boolean) => {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: reducedMotion,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })) as unknown as typeof window.matchMedia;
};

describe('Annunciator', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  const render = () => {
    act(() => {
      root.render(<Annunciator />);
    });
  };

  beforeEach(() => {
    vi.useFakeTimers();
    markMessageRead.mockClear();
    setMatchMedia(false);
    mockWsState = {
      npcCombatSignal: 0,
      lastNpcCombatInitiated: null,
      newMessageSignal: 0,
      lastNewMessage: null,
    };
    mockGameState = {
      playerState: { id: 'player-1', turns: 500 },
      currentSector: { hazard_level: 0 },
      markMessageRead,
    };
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    render();
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.useRealTimers();
  });

  it('is inert on mount -- no lamps, non-interactive overlay', () => {
    expect(container.querySelectorAll('.annunciator-lamp')).toHaveLength(0);
    const overlay = container.querySelector('[data-testid="annunciator-overlay"]') as HTMLElement;
    expect(overlay).not.toBeNull();
    expect(overlay.style.pointerEvents).toBe('none');
  });

  // ---- COMBAT (WARN) ----------------------------------------------------

  it('COMBAT: raises a flashing WARN lamp when the defender matches the current player', () => {
    mockWsState = {
      ...mockWsState,
      npcCombatSignal: 1,
      lastNpcCombatInitiated: { defender_id: 'player-1' },
    };
    render();

    const lamp = container.querySelector('.annunciator-lamp--warn') as HTMLElement;
    expect(lamp).not.toBeNull();
    expect(lamp.getAttribute('role')).toBe('alert');
    expect(lamp.getAttribute('aria-live')).toBe('assertive');
    expect(lamp.classList.contains('is-flashing')).toBe(true);
    expect(lamp.textContent).toContain('WARN');
    expect(lamp.textContent).toContain('COMBAT');
    expect(lamp.style.pointerEvents).toBe('auto');
  });

  it('COMBAT: does NOT raise a lamp for a spectator (defender_id mismatch)', () => {
    mockWsState = {
      ...mockWsState,
      npcCombatSignal: 1,
      lastNpcCombatInitiated: { defender_id: 'someone-else' },
    };
    render();

    expect(container.querySelectorAll('.annunciator-lamp')).toHaveLength(0);
  });

  it('COMBAT: tap-acknowledge stops the flash but stays visible; auto-clears after the dwell', () => {
    mockWsState = {
      ...mockWsState,
      npcCombatSignal: 1,
      lastNpcCombatInitiated: { defender_id: 'player-1' },
    };
    render();

    const ackBtn = container.querySelector('.annunciator-lamp-ack') as HTMLButtonElement;
    act(() => {
      ackBtn.click();
    });
    const lamp = container.querySelector('.annunciator-lamp--warn') as HTMLElement;
    expect(lamp.classList.contains('is-flashing')).toBe(false);
    expect(lamp.classList.contains('is-steady')).toBe(true);

    act(() => {
      vi.advanceTimersByTime(15000);
    });
    expect(container.querySelectorAll('.annunciator-lamp')).toHaveLength(0);
  });

  it('COMBAT: auto-clears after the dwell without any tap', () => {
    mockWsState = {
      ...mockWsState,
      npcCombatSignal: 1,
      lastNpcCombatInitiated: { defender_id: 'player-1' },
    };
    render();
    expect(container.querySelectorAll('.annunciator-lamp')).toHaveLength(1);

    act(() => {
      vi.advanceTimersByTime(15000);
    });
    expect(container.querySelectorAll('.annunciator-lamp')).toHaveLength(0);
  });

  it('COMBAT: a second distinct signal re-raises the lamp even after a prior dwell-clear', () => {
    mockWsState = {
      ...mockWsState,
      npcCombatSignal: 1,
      lastNpcCombatInitiated: { defender_id: 'player-1' },
    };
    render();
    act(() => {
      vi.advanceTimersByTime(15000);
    });
    expect(container.querySelectorAll('.annunciator-lamp')).toHaveLength(0);

    mockWsState = { ...mockWsState, npcCombatSignal: 2 };
    render();
    expect(container.querySelectorAll('.annunciator-lamp--warn')).toHaveLength(1);
  });

  // ---- HAZARD (CAUTION) --------------------------------------------------

  it('HAZARD: raises a flashing CAUTION lamp when sector hazard_level > 0, role=status', () => {
    mockGameState = { ...mockGameState, currentSector: { hazard_level: 3 } };
    render();

    const lamp = container.querySelector('.annunciator-lamp--caution') as HTMLElement;
    expect(lamp).not.toBeNull();
    expect(lamp.getAttribute('role')).toBe('status');
    expect(lamp.getAttribute('aria-live')).toBe('polite');
    expect(lamp.classList.contains('is-flashing')).toBe(true);
    expect(lamp.textContent).toContain('CAUTION');
    expect(lamp.textContent).toContain('HAZARD');
  });

  it('HAZARD: clears the instant hazard_level resolves back to 0 (level-driven auto-clear)', () => {
    mockGameState = { ...mockGameState, currentSector: { hazard_level: 5 } };
    render();
    expect(container.querySelector('.annunciator-lamp--caution')).not.toBeNull();

    mockGameState = { ...mockGameState, currentSector: { hazard_level: 0 } };
    render();
    expect(container.querySelectorAll('.annunciator-lamp')).toHaveLength(0);
  });

  it('HAZARD: tap-acknowledge goes steady, and a fresh false->true edge re-flashes even after ack', () => {
    mockGameState = { ...mockGameState, currentSector: { hazard_level: 4 } };
    render();
    const ackBtn = container.querySelector('.annunciator-lamp-ack') as HTMLButtonElement;
    act(() => {
      ackBtn.click();
    });
    expect(container.querySelector('.annunciator-lamp--caution.is-steady')).not.toBeNull();

    // resolve, then a fresh occurrence
    mockGameState = { ...mockGameState, currentSector: { hazard_level: 0 } };
    render();
    mockGameState = { ...mockGameState, currentSector: { hazard_level: 6 } };
    render();
    expect(container.querySelector('.annunciator-lamp--caution.is-flashing')).not.toBeNull();
  });

  // ---- TURNS (CAUTION, "low-fuel") --------------------------------------

  it('TURNS: raises CAUTION when turns < 50', () => {
    mockGameState = { ...mockGameState, playerState: { id: 'player-1', turns: 12 } };
    render();

    const lamp = container.querySelector('.annunciator-lamp--caution') as HTMLElement;
    expect(lamp).not.toBeNull();
    expect(lamp.textContent).toContain('TURNS');
  });

  it('TURNS: no lamp at or above the 50-turn threshold', () => {
    mockGameState = { ...mockGameState, playerState: { id: 'player-1', turns: 50 } };
    render();
    expect(container.querySelectorAll('.annunciator-lamp')).toHaveLength(0);
  });

  // ---- COMM (INFO, "hail") ------------------------------------------------

  it('COMM: raises an INFO lamp for a toast-eligible non-urgent hail, role=status', () => {
    mockWsState = {
      ...mockWsState,
      newMessageSignal: 1,
      lastNewMessage: { message_id: 'msg-1', delivery: ['inbox', 'toast'] },
    };
    render();

    const lamp = container.querySelector('.annunciator-lamp--info') as HTMLElement;
    expect(lamp).not.toBeNull();
    expect(lamp.getAttribute('role')).toBe('status');
    expect(lamp.getAttribute('aria-live')).toBe('polite');
    expect(lamp.textContent).toContain('COMM');
  });

  it('COMM: does NOT raise a lamp for a low-priority inbox-only message (no toast surface)', () => {
    mockWsState = {
      ...mockWsState,
      newMessageSignal: 1,
      lastNewMessage: { message_id: 'msg-2', delivery: ['inbox'] },
    };
    render();
    expect(container.querySelectorAll('.annunciator-lamp')).toHaveLength(0);
  });

  it('COMM: does NOT raise a lamp for an urgent hail (its own modal owns that surface)', () => {
    mockWsState = {
      ...mockWsState,
      newMessageSignal: 1,
      lastNewMessage: { message_id: 'msg-3', delivery: ['inbox', 'toast', 'modal'] },
    };
    render();
    expect(container.querySelectorAll('.annunciator-lamp')).toHaveLength(0);
  });

  it('COMM: tap-acknowledge marks the hail read (clears unread)', () => {
    mockWsState = {
      ...mockWsState,
      newMessageSignal: 1,
      lastNewMessage: { message_id: 'msg-4', delivery: ['inbox', 'toast'] },
    };
    render();
    const ackBtn = container.querySelector('.annunciator-lamp-ack') as HTMLButtonElement;
    act(() => {
      ackBtn.click();
    });
    expect(markMessageRead).toHaveBeenCalledWith('msg-4');
  });

  // ---- reduced motion (Accept #3) ----------------------------------------

  it('reduced motion: a lamp still renders (fully visible) but never gets the flashing class', () => {
    // useReducedMotion's initial useState reads matchMedia synchronously on
    // FIRST mount only (mirrors real production behavior -- the system
    // preference is read once at page load) -- beforeEach already mounted
    // the shared root with reducedMotion=false, so this case needs its own
    // fresh mount, not a re-render of that root.
    act(() => {
      root.unmount();
    });
    setMatchMedia(true);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    mockGameState = { ...mockGameState, currentSector: { hazard_level: 7 } };
    render();

    const lamp = container.querySelector('.annunciator-lamp--caution') as HTMLElement;
    expect(lamp).not.toBeNull();
    expect(lamp.classList.contains('is-reduced-motion')).toBe(true);
    expect(lamp.classList.contains('is-flashing')).toBe(false);
  });

  // ---- multi-lamp + non-color severity marking ---------------------------

  it('multiple simultaneous states each raise their own lamp, independently', () => {
    mockGameState = {
      playerState: { id: 'player-1', turns: 10 },
      currentSector: { hazard_level: 8 },
      markMessageRead,
    };
    mockWsState = {
      npcCombatSignal: 1,
      lastNpcCombatInitiated: { defender_id: 'player-1' },
      newMessageSignal: 1,
      lastNewMessage: { message_id: 'msg-5', delivery: ['inbox', 'toast'] },
    };
    render();

    expect(container.querySelectorAll('.annunciator-lamp')).toHaveLength(4);
    expect(container.querySelector('.annunciator-lamp--warn')).not.toBeNull();
    expect(container.querySelectorAll('.annunciator-lamp--caution')).toHaveLength(2);
    expect(container.querySelector('.annunciator-lamp--info')).not.toBeNull();
  });

  it('every lamp carries an uppercase text severity label (not color-alone)', () => {
    mockGameState = { ...mockGameState, currentSector: { hazard_level: 2 } };
    render();
    const lamp = container.querySelector('.annunciator-lamp--caution') as HTMLElement;
    const severityEl = lamp.querySelector('.annunciator-lamp-severity');
    expect(severityEl?.textContent).toBe('CAUTION');
  });
});
