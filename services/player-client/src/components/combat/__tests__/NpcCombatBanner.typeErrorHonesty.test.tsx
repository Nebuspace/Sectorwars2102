// @vitest-environment jsdom
/**
 * LEG-3753 Soft-ORDER — NpcCombatBanner TypeError/network densify.
 *
 * NpcCombatBanner is WS-driven (no REST load). This file pins the exported
 * formatNpcCombatBannerError helper and proves defender/spectator surfaces
 * never leak transport-collapse strings into user-facing chrome.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const addNotification = vi.fn();
let mockWsState: {
  npcCombatSignal: number;
  lastNpcCombatInitiated: {
    npc_display_name: string;
    npc_archetype: 'LAW_ENFORCEMENT' | 'HOSTILE_RAIDER';
    npc_ship_name: string | null;
    npc_ship_type: string | null;
    defender_id: string;
    defender_name: string | null;
    sector_id: number | null;
    combat_id: string;
  } | null;
  addNotification: typeof addNotification;
};
let mockGameState: { playerState: { id: string } | null };

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => mockWsState,
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => mockGameState,
}));

import NpcCombatBanner, {
  formatNpcCombatBannerError,
  NPC_COMBAT_BANNER_LOAD_FALLBACK,
} from '../NpcCombatBanner';

describe('formatNpcCombatBannerError (LEG-3753)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatNpcCombatBannerError(new TypeError('Failed to fetch'));
    expect(text).toBe(NPC_COMBAT_BANNER_LOAD_FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatNpcCombatBannerError(new Error('Network Error'))).toBe(NPC_COMBAT_BANNER_LOAD_FALLBACK);
    expect(formatNpcCombatBannerError(new Error('Failed to fetch'))).toBe(NPC_COMBAT_BANNER_LOAD_FALLBACK);
  });

  it('preserves non-generic server detail when not transport collapse', () => {
    expect(formatNpcCombatBannerError(new Error('combat_feed_offline'))).toBe('combat_feed_offline');
  });
});

describe('NpcCombatBanner WS surfaces transport honesty (LEG-3753)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  const frame = {
    npc_display_name: 'Blackfang',
    npc_archetype: 'HOSTILE_RAIDER' as const,
    npc_ship_name: 'The Reaver',
    npc_ship_type: 'raider-corvette',
    defender_id: 'player-1',
    defender_name: 'Pilot',
    sector_id: 17,
    combat_id: 'combat-abc',
  };

  beforeEach(() => {
    addNotification.mockClear();
    mockWsState = { npcCombatSignal: 0, lastNpcCombatInitiated: null, addNotification };
    mockGameState = { playerState: { id: 'player-1' } };
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

  it('defender banner copy never includes transport-collapse strings', () => {
    mockWsState = { ...mockWsState, npcCombatSignal: 1, lastNpcCombatInitiated: frame };
    act(() => {
      root.render(<NpcCombatBanner />);
    });

    const banner = container.querySelector('.npc-combat-banner');
    expect(banner).not.toBeNull();
    expect(banner!.textContent).not.toMatch(/Network Error/i);
    expect(banner!.textContent).not.toMatch(/Failed to fetch/i);
    expect(banner!.textContent).not.toMatch(/TypeError/i);
  });

  it('spectator notification copy never includes transport-collapse strings', () => {
    mockGameState = { playerState: { id: 'other-player' } };
    mockWsState = { ...mockWsState, npcCombatSignal: 1, lastNpcCombatInitiated: frame };
    act(() => {
      root.render(<NpcCombatBanner />);
    });

    expect(addNotification).toHaveBeenCalledTimes(1);
    const payload = addNotification.mock.calls[0][0];
    expect(payload.content).not.toMatch(/Network Error/i);
    expect(payload.content).not.toMatch(/Failed to fetch/i);
    expect(payload.title).not.toMatch(/TypeError/i);
  });
});
