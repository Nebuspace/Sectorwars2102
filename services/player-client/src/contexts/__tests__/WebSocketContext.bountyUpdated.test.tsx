// @vitest-environment jsdom
/**
 * WebSocketContext — bounty_updated WS consumer (WO-BOUNTY-REALTIME-EVENTS).
 *
 * Server already emits typed bounty_updated frames post-commit (ranking.py
 * place/cancel + combat_service._emit_bounty_collected). This pins the
 * player-client generalHandler branch: pure data plumbing (signal bump +
 * payload stash). GameContext.onBountyUpdated refreshes playerState when
 * the current player is involved — that half is not covered here.
 *
 * Mirrors WebSocketContext.npcCombatInitiated.test.tsx's real-provider
 * technique.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

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

describe('WebSocketContext bounty_updated WS consumer', () => {
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

  it('is inert on mount — signal 0, no payload yet', () => {
    expect(captured!.bountyEventSignal).toBe(0);
    expect(captured!.lastBountyUpdated).toBeNull();
  });

  it('stashes a placed frame and bumps the signal', async () => {
    act(() => {
      svc.notifyHandlers({
        type: 'bounty_updated',
        action: 'placed',
        bounty_id: 'b-1',
        target_id: 'target-9',
        amount: 5000,
        placed_by: 'placer-1',
        placed_by_name: 'Hunter',
        timestamp: '2026-08-03T01:00:00Z',
      });
    });
    await flush();

    expect(captured!.bountyEventSignal).toBe(1);
    expect(captured!.lastBountyUpdated).toEqual({
      action: 'placed',
      bounty_id: 'b-1',
      target_id: 'target-9',
      amount: 5000,
      placed_by: 'placer-1',
      collected_by: null,
      total_collected: null,
      refund: null,
      timestamp: '2026-08-03T01:00:00Z',
    });
  });

  it('stashes a collected frame and bumps again', async () => {
    act(() => {
      svc.notifyHandlers({
        type: 'bounty_updated',
        action: 'collected',
        target_id: 'target-9',
        collected_by: 'hunter-2',
        total_collected: 12000,
        player_bounties_collected: 5000,
        system_bounties_collected: 7000,
      });
    });
    await flush();

    expect(captured!.bountyEventSignal).toBe(1);
    expect(captured!.lastBountyUpdated!.action).toBe('collected');
    expect(captured!.lastBountyUpdated!.collected_by).toBe('hunter-2');
    expect(captured!.lastBountyUpdated!.total_collected).toBe(12000);
  });

  it('stashes a cancelled frame with refund', async () => {
    act(() => {
      svc.notifyHandlers({
        type: 'bounty_updated',
        action: 'cancelled',
        bounty_id: 'b-2',
        target_id: 'target-9',
        refund: 4500,
        placed_by: 'placer-1',
      });
    });
    await flush();

    expect(captured!.lastBountyUpdated!.action).toBe('cancelled');
    expect(captured!.lastBountyUpdated!.refund).toBe(4500);
  });
});
