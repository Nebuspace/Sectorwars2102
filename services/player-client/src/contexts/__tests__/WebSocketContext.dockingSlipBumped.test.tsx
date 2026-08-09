// @vitest-environment jsdom
/**
 * WebSocketContext — docking_slip_bumped WS consumer.
 *
 * Another player pays the 5x bump fee to evict the current player from a
 * station docking slip (docking_service.bump -> docking_service._notify_bumped
 * -> connection_manager.send_personal_message). The backend already ships a
 * human-readable `message`; this pins WebSocketContext's generalHandler
 * branch: the frame surfaces exactly one warning-level toast built from that
 * message (falling back to a generic message if it's ever absent).
 *
 * Mirrors WebSocketContext.hostileDetected.test.tsx's harness exactly.
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

describe('WebSocketContext docking_slip_bumped WS consumer', () => {
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
        type: 'docking_slip_bumped',
        message: 'Your ship has been bumped from its docking slip at Alpha Station. You have been undocked.',
        station_name: 'Alpha Station',
      });
    });
    await flush();

    expect(captured).not.toBeNull();
    const toasts = captured!.notifications.filter((n) => n.title === 'Docking Slip Bumped');
    expect(toasts).toHaveLength(1);
    expect(toasts[0].content).toBe(
      'Your ship has been bumped from its docking slip at Alpha Station. You have been undocked.'
    );
    expect(toasts[0].level).toBe('warning');
  });

  it('falls back to a generic message when message is missing', async () => {
    act(() => {
      svc.notifyHandlers({
        type: 'docking_slip_bumped',
        station_name: 'Beta Station',
      });
    });
    await flush();

    const toasts = captured!.notifications.filter((n) => n.title === 'Docking Slip Bumped');
    expect(toasts).toHaveLength(1);
    expect(toasts[0].content).toBe('Your ship has been bumped from its docking slip.');
  });

  it('is inert for unrelated frame types', async () => {
    act(() => {
      svc.notifyHandlers({ type: 'connection_status', connected: true } as WebSocketMessage);
    });
    await flush();

    expect(captured!.notifications.filter((n) => n.title === 'Docking Slip Bumped')).toHaveLength(0);
  });
});
