// @vitest-environment jsdom
/**
 * WebSocketContext — limpet_signal WS consumer.
 *
 * Server already emits limpet_signal frames on every move of a ship carrying
 * a limpet mine tracker (movement_service._dispatch_limpet_signals), sent
 * personally to the mine's owner reporting the tracked ship's new sector.
 * This pins the player-client generalHandler branch: pure data plumbing
 * (signal bump + payload stash) — no toast, since the frame fires on every
 * tracked move rather than a one-off event. A future tracker-panel UI can
 * consume the signal/payload directly.
 *
 * Mirrors WebSocketContext.bountyUpdated.test.tsx's real-provider technique.
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

describe('WebSocketContext limpet_signal WS consumer', () => {
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
    expect(captured!.limpetSignalEventSignal).toBe(0);
    expect(captured!.lastLimpetSignal).toBeNull();
  });

  it('stashes a limpet_signal frame and bumps the signal', async () => {
    act(() => {
      svc.notifyHandlers({
        type: 'limpet_signal',
        tracked_player_id: 'player-9',
        tracked_ship_id: 'ship-9',
        sector_id: 42,
      });
    });
    await flush();

    expect(captured!.limpetSignalEventSignal).toBe(1);
    expect(captured!.lastLimpetSignal).toEqual({
      tracked_player_id: 'player-9',
      tracked_ship_id: 'ship-9',
      sector_id: 42,
    });
  });

  it('bumps again and overwrites the payload on a second frame (later move)', async () => {
    act(() => {
      svc.notifyHandlers({
        type: 'limpet_signal',
        tracked_player_id: 'player-9',
        tracked_ship_id: 'ship-9',
        sector_id: 42,
      });
    });
    await flush();

    act(() => {
      svc.notifyHandlers({
        type: 'limpet_signal',
        tracked_player_id: 'player-9',
        tracked_ship_id: 'ship-9',
        sector_id: 55,
      });
    });
    await flush();

    expect(captured!.limpetSignalEventSignal).toBe(2);
    expect(captured!.lastLimpetSignal!.sector_id).toBe(55);
  });

  it('is inert for unrelated frame types', async () => {
    act(() => {
      svc.notifyHandlers({ type: 'connection_status', connected: true } as WebSocketMessage);
    });
    await flush();

    expect(captured!.limpetSignalEventSignal).toBe(0);
    expect(captured!.lastLimpetSignal).toBeNull();
  });
});
