// @vitest-environment jsdom
/**
 * WelcomeBackToast — WS turn_pool_updated{reason:'welcome_back'} is a no-op
 * for the toast layer (WO-PROG-TURN-VISIBILITY superseded-lane resolution).
 *
 * The original spec's lane D ("toast on turn_pool_updated with
 * reason=welcome_back") predates WO-PUX-WBACK-SURFACE (db86698), which
 * already ships a login-response-driven welcome-back toast (see
 * WelcomeBackToast.test.tsx: it fires purely off AuthContext's
 * welcomeBackSignal/lastWelcomeBack, never off any WebSocket message — the
 * component has zero addMessageHandler/useWebSocket-message coupling). This
 * pins that turn_service.welcome_back()'s new server-side emit (which now
 * carries reason:'welcome_back', see turn_service.py) does NOT produce a
 * second toast: exactly one toast fires (the shipped AuthContext-driven one),
 * and the raw WS frame is inert for the toast/ARIA-feed layer.
 *
 * Composes WelcomeBackToast.test.tsx's real-provider AuthContext-mock
 * technique with WebSocketContext.teammateUnderAttack.test.tsx's
 * svc.notifyHandlers technique for injecting a raw WS frame DOM-free.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

interface MockWelcomeBack {
  granted: boolean;
  bonus: number;
  days_inactive: number;
}

let mockAuthState: { welcomeBackSignal: number; lastWelcomeBack: MockWelcomeBack | null };

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => mockAuthState,
}));

import { WebSocketProvider, useWebSocket } from '../../../contexts/WebSocketContext';
import { websocketService, type WebSocketMessage } from '../../../services/websocket';
import { ariaFeed } from '../../mfd/ariaFeedStore';
import WelcomeBackToast from '../WelcomeBackToast';

// websocketService is a module-level singleton; notifyHandlers is private,
// reached the same way WebSocketContext.teammateUnderAttack.test.tsx does.
const svc = websocketService as unknown as {
  notifyHandlers: (message: WebSocketMessage) => void;
};

let captured: ReturnType<typeof useWebSocket> | null = null;
function Consumer() {
  captured = useWebSocket();
  return null;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('WelcomeBackToast — turn_pool_updated WS frame is a no-op', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let appendNavSpy: ReturnType<typeof vi.spyOn>;

  const renderTree = async () => {
    act(() => {
      root.render(
        React.createElement(
          WebSocketProvider,
          null,
          React.createElement(WelcomeBackToast),
          React.createElement(Consumer)
        )
      );
    });
    await flush();
  };

  beforeEach(async () => {
    // Mount baseline (signal 0, mirroring WelcomeBackToast.test.tsx) -- the
    // effect's prevSignal ref initializes off whatever it sees on mount, so
    // mounting directly at a nonzero signal would never fire it. Each test
    // bumps to signal 1 itself, exactly as WelcomeBackToast.test.tsx does.
    mockAuthState = { welcomeBackSignal: 0, lastWelcomeBack: null };
    appendNavSpy = vi.spyOn(ariaFeed, 'appendNav');
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    captured = null;
    await renderTree();
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    appendNavSpy.mockRestore();
  });

  it('a turn_pool_updated{reason:"welcome_back"} WS frame does not add a second toast', async () => {
    // A genuine grant -- the ONE shipped toast fires off this AuthContext
    // signal bump, mirroring WelcomeBackToast.test.tsx's granted case.
    mockAuthState = {
      welcomeBackSignal: 1,
      lastWelcomeBack: { granted: true, bonus: 400, days_inactive: 8 },
    };
    await renderTree();
    expect(captured!.notifications).toHaveLength(1);
    expect(appendNavSpy).toHaveBeenCalledTimes(1);

    act(() => {
      svc.notifyHandlers({
        type: 'turn_pool_updated',
        player_id: 'p1',
        turns: 500,
        max_turns: 1000,
        turns_added: 400,
        bonus_multiplier: 1.0,
        reason: 'welcome_back',
        timestamp: new Date().toISOString(),
      } as unknown as WebSocketMessage);
    });
    await flush();

    // Still exactly one toast, still exactly one ARIA line -- the WS frame
    // is inert for this layer (there is no message-handler branch for
    // turn_pool_updated in WelcomeBackToast or WebSocketContext's toast
    // wiring; the pool figure updates silently through whatever reads
    // playerState instead).
    expect(captured!.notifications).toHaveLength(1);
    expect(appendNavSpy).toHaveBeenCalledTimes(1);
  });

  it('an ordinary (non-welcome_back) turn_pool_updated frame is equally inert for the toast layer', async () => {
    mockAuthState = {
      welcomeBackSignal: 1,
      lastWelcomeBack: { granted: true, bonus: 400, days_inactive: 8 },
    };
    await renderTree();
    expect(captured!.notifications).toHaveLength(1);

    act(() => {
      svc.notifyHandlers({
        type: 'turn_pool_updated',
        player_id: 'p1',
        turns: 120,
        max_turns: 1000,
        turns_added: 5,
        bonus_multiplier: 1.0,
        timestamp: new Date().toISOString(),
      } as unknown as WebSocketMessage);
    });
    await flush();

    expect(captured!.notifications).toHaveLength(1);
    expect(appendNavSpy).toHaveBeenCalledTimes(1);
  });
});
