// @vitest-environment jsdom
/**
 * WebSocketContext — npc_combat_initiated WS consumer (WO-CMB-NPC-INITIATED-1
 * lane D). combat_service pushes this frame TWICE per NPC-initiated
 * engagement (personal-to-defender + sector-broadcast, SAME shape) at combat
 * initiation. This pins WebSocketContext's generalHandler branch: pure data
 * plumbing (signal bump + payload stash), deliberately with NO toast/banner
 * decision here -- that split needs the player's own id (GameContext), which
 * WebSocketContext never imports; NpcCombatBanner.test.tsx pins that half.
 *
 * Mirrors WebSocketContext.teammateUnderAttack.test.tsx's real-provider
 * technique: exercises the REAL websocketService singleton via its private
 * notifyHandlers, with AuthContext mocked unauthenticated so the auto-connect
 * effect never attempts a real connection in jsdom.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../AuthContext', () => ({
  useAuth: () => ({ user: null, isAuthenticated: false }),
}));

import { WebSocketProvider, useWebSocket } from '../WebSocketContext';
import { websocketService, type WebSocketMessage } from '../../services/websocket';

const svc = websocketService as unknown as {
  notifyHandlers: (message: WebSocketMessage) => void;
};

let captured: ReturnType<typeof useWebSocket> | null = null;
function Consumer() {
  captured = useWebSocket();
  return null;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('WebSocketContext npc_combat_initiated WS consumer', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(async () => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    captured = null;

    act(() => {
      root.render(
        React.createElement(WebSocketProvider, null, React.createElement(Consumer))
      );
    });
    await flush();
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it('is inert on mount -- signal 0 is the baseline, no payload yet', () => {
    expect(captured!.npcCombatSignal).toBe(0);
    expect(captured!.lastNpcCombatInitiated).toBeNull();
  });

  it('stashes the full payload and bumps the signal for a hostile-raider frame', async () => {
    act(() => {
      svc.notifyHandlers({
        type: 'npc_combat_initiated',
        npc_id: 'npc-1',
        npc_display_name: 'Blackfang',
        npc_archetype: 'HOSTILE_RAIDER',
        npc_ship_name: 'The Reaver',
        npc_ship_type: 'raider-corvette',
        defender_id: 'player-42',
        defender_name: 'Ratbone',
        sector_id: 17,
        trigger: 'proximity',
        combat_id: 'combat-abc',
        timestamp: '2026-07-10T00:00:00Z',
      });
    });
    await flush();

    expect(captured!.npcCombatSignal).toBe(1);
    expect(captured!.lastNpcCombatInitiated).toEqual({
      npc_id: 'npc-1',
      npc_display_name: 'Blackfang',
      npc_archetype: 'HOSTILE_RAIDER',
      npc_ship_name: 'The Reaver',
      npc_ship_type: 'raider-corvette',
      defender_id: 'player-42',
      defender_name: 'Ratbone',
      sector_id: 17,
      trigger: 'proximity',
      combat_id: 'combat-abc',
      timestamp: '2026-07-10T00:00:00Z',
    });
    // Deliberately no toast/banner surface at this layer -- see the file doc-comment.
    expect(captured!.notifications).toHaveLength(0);
  });

  it('normalizes an unrecognized archetype to HOSTILE_RAIDER (defensive fallback)', async () => {
    act(() => {
      svc.notifyHandlers({
        type: 'npc_combat_initiated',
        npc_id: 'npc-2',
        npc_display_name: 'Marshal Vex',
        npc_archetype: 'SOMETHING_UNEXPECTED',
        defender_id: 'player-1',
        combat_id: 'combat-xyz',
      });
    });
    await flush();

    expect(captured!.lastNpcCombatInitiated!.npc_archetype).toBe('HOSTILE_RAIDER');
  });

  it('preserves LAW_ENFORCEMENT verbatim', async () => {
    act(() => {
      svc.notifyHandlers({
        type: 'npc_combat_initiated',
        npc_id: 'npc-3',
        npc_display_name: 'Marshal Vex',
        npc_archetype: 'LAW_ENFORCEMENT',
        defender_id: 'player-1',
        combat_id: 'combat-def',
      });
    });
    await flush();

    expect(captured!.lastNpcCombatInitiated!.npc_archetype).toBe('LAW_ENFORCEMENT');
  });

  it('defaults optional fields to null and required strings to empty/fallback when missing', async () => {
    act(() => {
      svc.notifyHandlers({
        type: 'npc_combat_initiated',
        defender_id: 'player-1',
        combat_id: 'combat-ghi',
      });
    });
    await flush();

    expect(captured!.lastNpcCombatInitiated).toEqual({
      npc_id: '',
      npc_display_name: 'An NPC vessel',
      npc_archetype: 'HOSTILE_RAIDER',
      npc_ship_name: null,
      npc_ship_type: null,
      defender_id: 'player-1',
      defender_name: null,
      sector_id: null,
      trigger: null,
      combat_id: 'combat-ghi',
      timestamp: null,
    });
  });

  it('bumps the signal again on a second distinct event', async () => {
    act(() => {
      svc.notifyHandlers({
        type: 'npc_combat_initiated',
        defender_id: 'player-1',
        combat_id: 'combat-1',
      });
    });
    await flush();
    act(() => {
      svc.notifyHandlers({
        type: 'npc_combat_initiated',
        defender_id: 'player-1',
        combat_id: 'combat-2',
      });
    });
    await flush();

    expect(captured!.npcCombatSignal).toBe(2);
    expect(captured!.lastNpcCombatInitiated!.combat_id).toBe('combat-2');
  });

  it('is inert for unrelated frame types', async () => {
    act(() => {
      svc.notifyHandlers({ type: 'connection_status', connected: true } as WebSocketMessage);
    });
    await flush();

    expect(captured!.npcCombatSignal).toBe(0);
    expect(captured!.lastNpcCombatInitiated).toBeNull();
  });
});
