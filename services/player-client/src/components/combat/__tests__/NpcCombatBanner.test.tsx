// @vitest-environment jsdom
/**
 * NpcCombatBanner (WO-CMB-NPC-INITIATED-1 lane D) — the npc_combat_initiated
 * consumer that branches into the defender banner or the spectator toast.
 * WebSocketContext and GameContext are both mocked to mutable, reassignable
 * objects (mirrors PriorityHailConsumer.uplinkToast.test.tsx's seam) so the
 * signal/payload/playerState can be driven directly without exercising the
 * real WebSocketProvider -- that ingestion is covered separately in
 * WebSocketContext.npcCombatInitiated.test.tsx. This file pins only the
 * CONSUMER's defender/spectator branch, archetype flavoring, dismiss
 * behavior, and combat_id passthrough.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

interface MockNpcCombat {
  npc_id: string;
  npc_display_name: string;
  npc_archetype: 'LAW_ENFORCEMENT' | 'HOSTILE_RAIDER';
  npc_ship_name: string | null;
  npc_ship_type: string | null;
  defender_id: string;
  defender_name: string | null;
  sector_id: number | null;
  trigger: string | null;
  combat_id: string;
  timestamp: string | null;
}

const addNotification = vi.fn();
let mockWsState: {
  npcCombatSignal: number;
  lastNpcCombatInitiated: MockNpcCombat | null;
  addNotification: typeof addNotification;
};
let mockGameState: { playerState: { id: string } | null };

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => mockWsState,
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => mockGameState,
}));

import NpcCombatBanner from '../NpcCombatBanner';

const raiderFrame: MockNpcCombat = {
  npc_id: 'npc-1',
  npc_display_name: 'Blackfang',
  npc_archetype: 'HOSTILE_RAIDER',
  npc_ship_name: 'The Reaver',
  npc_ship_type: 'raider-corvette',
  defender_id: 'player-1',
  defender_name: 'Ratbone',
  sector_id: 17,
  trigger: 'proximity',
  combat_id: 'combat-abc',
  timestamp: '2026-07-10T00:00:00Z',
};

const patrolFrame: MockNpcCombat = {
  ...raiderFrame,
  npc_id: 'npc-2',
  npc_display_name: 'Marshal Vex',
  npc_archetype: 'LAW_ENFORCEMENT',
  combat_id: 'combat-def',
};

describe('NpcCombatBanner', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  const render = () => {
    act(() => {
      root.render(<NpcCombatBanner />);
    });
  };

  beforeEach(() => {
    addNotification.mockClear();
    mockWsState = { npcCombatSignal: 0, lastNpcCombatInitiated: null, addNotification };
    mockGameState = { playerState: { id: 'player-1' } };
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
  });

  it('is inert on mount -- signal 0 is the baseline', () => {
    expect(container.querySelector('.npc-combat-banner')).toBeNull();
    expect(addNotification).not.toHaveBeenCalled();
  });

  it('shows the defender banner (not a toast) when defender_id matches the current player, raider-flavored', () => {
    mockWsState = { ...mockWsState, npcCombatSignal: 1, lastNpcCombatInitiated: raiderFrame };
    render();

    const banner = container.querySelector('.npc-combat-banner');
    expect(banner).not.toBeNull();
    expect(banner!.classList.contains('npc-combat-banner--raider')).toBe(true);
    expect(banner!.textContent).toContain('Blackfang has opened fire on your vessel!');
    expect(banner!.textContent).toContain('HOSTILE CONTACT');
    expect(banner!.textContent).toContain('Contact: The Reaver (raider-corvette)');
    expect(banner!.textContent).toContain('Sector 17');
    expect(addNotification).not.toHaveBeenCalled();
  });

  it('shows the defender banner, patrol-flavored, for LAW_ENFORCEMENT', () => {
    mockWsState = { ...mockWsState, npcCombatSignal: 1, lastNpcCombatInitiated: patrolFrame };
    render();

    const banner = container.querySelector('.npc-combat-banner');
    expect(banner).not.toBeNull();
    expect(banner!.classList.contains('npc-combat-banner--patrol')).toBe(true);
    expect(banner!.textContent).toContain('Marshal Vex is moving to interdict your vessel.');
    expect(banner!.textContent).toContain('LAWFUL INTERDICTION');
    expect(addNotification).not.toHaveBeenCalled();
  });

  it('carries the combat_id on the banner root for hand-off correlation', () => {
    mockWsState = { ...mockWsState, npcCombatSignal: 1, lastNpcCombatInitiated: raiderFrame };
    render();

    const banner = container.querySelector('.npc-combat-banner');
    expect(banner!.getAttribute('data-combat-id')).toBe('combat-abc');
  });

  it('does NOT show the banner for a spectator (defender_id does not match); toasts instead, raider-flavored', () => {
    mockGameState = { playerState: { id: 'someone-else' } };
    mockWsState = { ...mockWsState, npcCombatSignal: 1, lastNpcCombatInitiated: raiderFrame };
    render();

    expect(container.querySelector('.npc-combat-banner')).toBeNull();
    expect(addNotification).toHaveBeenCalledTimes(1);
    expect(addNotification).toHaveBeenCalledWith({
      title: 'Pirate Raid Detected',
      content: 'Blackfang is attacking Ratbone in sector 17',
      level: 'warning',
    });
  });

  it('spectator toast reads as a lawful interdiction for LAW_ENFORCEMENT', () => {
    mockGameState = { playerState: { id: 'someone-else' } };
    mockWsState = { ...mockWsState, npcCombatSignal: 1, lastNpcCombatInitiated: patrolFrame };
    render();

    expect(addNotification).toHaveBeenCalledWith({
      title: 'Patrol Interdiction',
      content: 'Marshal Vex is attacking Ratbone in sector 17',
      level: 'warning',
    });
  });

  it('spectator toast falls back to generic names/omits sector when missing', () => {
    mockGameState = { playerState: { id: 'someone-else' } };
    mockWsState = {
      ...mockWsState,
      npcCombatSignal: 1,
      lastNpcCombatInitiated: { ...raiderFrame, defender_name: null, sector_id: null },
    };
    render();

    expect(addNotification).toHaveBeenCalledWith({
      title: 'Pirate Raid Detected',
      content: 'Blackfang is attacking a pilot',
      level: 'warning',
    });
  });

  it('raises neither surface when playerState has not loaded yet (defensive)', () => {
    mockGameState = { playerState: null };
    mockWsState = { ...mockWsState, npcCombatSignal: 1, lastNpcCombatInitiated: raiderFrame };
    render();

    expect(container.querySelector('.npc-combat-banner')).toBeNull();
    expect(addNotification).not.toHaveBeenCalled();
  });

  it('the dismiss button hides the banner', () => {
    mockWsState = { ...mockWsState, npcCombatSignal: 1, lastNpcCombatInitiated: raiderFrame };
    render();

    const dismissBtn = container.querySelector('.npc-combat-banner-dismiss') as HTMLButtonElement;
    expect(dismissBtn).not.toBeNull();
    act(() => {
      dismissBtn.click();
    });

    expect(container.querySelector('.npc-combat-banner')).toBeNull();
  });

  it('a second distinct signal re-shows the banner even after a prior dismissal', () => {
    mockWsState = { ...mockWsState, npcCombatSignal: 1, lastNpcCombatInitiated: raiderFrame };
    render();
    const dismissBtn = container.querySelector('.npc-combat-banner-dismiss') as HTMLButtonElement;
    act(() => {
      dismissBtn.click();
    });
    expect(container.querySelector('.npc-combat-banner')).toBeNull();

    mockWsState = { ...mockWsState, npcCombatSignal: 2, lastNpcCombatInitiated: patrolFrame };
    render();

    const banner = container.querySelector('.npc-combat-banner');
    expect(banner).not.toBeNull();
    expect(banner!.getAttribute('data-combat-id')).toBe('combat-def');
  });

  it('does not re-fire on a re-render with the same signal', () => {
    mockWsState = { ...mockWsState, npcCombatSignal: 1, lastNpcCombatInitiated: raiderFrame };
    render();
    render(); // identical signal, second render pass

    expect(container.querySelectorAll('.npc-combat-banner')).toHaveLength(1);
  });
});
