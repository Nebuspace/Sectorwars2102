// @vitest-environment jsdom
/**
 * WebSocketContext — ship_recovered_impounded WS consumer.
 *
 * Someone surrendered a tractor-locked ship the current player is the
 * registered owner of to station security
 * (station_security_service.surrender_tractor_locked_ship ->
 * _notify_registered_owner -> connection_manager.send_personal_message).
 * The backend already ships a human-readable `message`; this pins
 * WebSocketContext's generalHandler branch: the frame surfaces exactly one
 * warning-level toast built from that message (falling back to a generic
 * message if it's ever absent).
 *
 * Mirrors WebSocketContext.dockingSlipBumped.test.tsx's harness exactly.
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

describe('WebSocketContext ship_recovered_impounded WS consumer', () => {
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

  it('surfaces exactly one warning toast built from the server message', async () => {
    act(() => {
      svc.notifyHandlers({
        type: 'ship_recovered_impounded',
        message: "Your ship 'Sparrow' was surrendered to station security at Gateway Station and is being held for retrieval.",
        ship_id: 'ship-1',
        station_id: 'station-1',
      });
    });
    await flush();

    expect(captured).not.toBeNull();
    const toasts = captured!.notifications.filter((n) => n.title === 'Ship Impounded');
    expect(toasts).toHaveLength(1);
    expect(toasts[0].content).toBe(
      "Your ship 'Sparrow' was surrendered to station security at Gateway Station and is being held for retrieval."
    );
    expect(toasts[0].level).toBe('warning');
  });

  it('falls back to a generic message when message is missing', async () => {
    act(() => {
      svc.notifyHandlers({
        type: 'ship_recovered_impounded',
        ship_id: 'ship-2',
        station_id: 'station-2',
      });
    });
    await flush();

    const toasts = captured!.notifications.filter((n) => n.title === 'Ship Impounded');
    expect(toasts).toHaveLength(1);
    expect(toasts[0].content).toBe(
      'One of your ships was surrendered to station security and is being held for retrieval.'
    );
  });

  it('is inert for unrelated frame types', async () => {
    act(() => {
      svc.notifyHandlers({ type: 'connection_status', connected: true } as WebSocketMessage);
    });
    await flush();

    expect(captured!.notifications.filter((n) => n.title === 'Ship Impounded')).toHaveLength(0);
  });
});
