// @vitest-environment jsdom
/**
 * WelcomeBackToast (WO-PUX-WBACK-SURFACE) — the AuthContext-signal consumer
 * that bridges the welcome-back grant into the cockpit's toast rail + ARIA
 * feed. AuthContext sits outside WebSocketProvider (see the component's own
 * doc comment), so this is the seam that actually fires the surfaces; the
 * companion AuthContext.welcomeBack.test.tsx only pins that the signal/
 * payload are threaded correctly, not that anything renders from them.
 *
 * Exercises the REAL WebSocketProvider (its `notifications` queue is what
 * PriorityHailConsumer renders) with AuthContext mocked to a controllable
 * signal/payload pair, mirroring WebSocketContext.teammateUnderAttack.test
 * .tsx's real-provider technique.
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
import { ariaFeed } from '../../mfd/ariaFeedStore';
import WelcomeBackToast from '../WelcomeBackToast';

let captured: ReturnType<typeof useWebSocket> | null = null;
function Consumer() {
  captured = useWebSocket();
  return null;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('WelcomeBackToast', () => {
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

  it('is inert on mount -- signal 0 is the baseline, never a real grant', () => {
    expect(captured!.notifications).toHaveLength(0);
    expect(appendNavSpy).not.toHaveBeenCalled();
  });

  it('fires exactly one toast + one ARIA line on a granted signal bump', async () => {
    mockAuthState = {
      welcomeBackSignal: 1,
      lastWelcomeBack: { granted: true, bonus: 400, days_inactive: 8 },
    };
    await renderTree();

    expect(captured!.notifications).toHaveLength(1);
    expect(captured!.notifications[0].title).toBe('Welcome Back');
    expect(captured!.notifications[0].content).toBe(
      '+400 turns — welcome back, Commander (8 days away)'
    );
    expect(captured!.notifications[0].level).toBe('success');

    expect(appendNavSpy).toHaveBeenCalledTimes(1);
    expect(appendNavSpy).toHaveBeenCalledWith(
      'Welcome back, Commander. +400 turns credited — 8 days away.'
    );
  });

  it('does not re-fire on a re-render with the same signal', async () => {
    mockAuthState = {
      welcomeBackSignal: 1,
      lastWelcomeBack: { granted: true, bonus: 400, days_inactive: 8 },
    };
    await renderTree();
    await renderTree(); // identical signal, second render pass

    expect(captured!.notifications).toHaveLength(1);
    expect(appendNavSpy).toHaveBeenCalledTimes(1);
  });

  it('does not fire when the payload is granted:false (defensive -- AuthContext never bumps this way)', async () => {
    mockAuthState = {
      welcomeBackSignal: 1,
      lastWelcomeBack: { granted: false, bonus: 0, days_inactive: 0 },
    };
    await renderTree();

    expect(captured!.notifications).toHaveLength(0);
    expect(appendNavSpy).not.toHaveBeenCalled();
  });

  it('does not fire when lastWelcomeBack is null even if the signal is nonzero (defensive)', async () => {
    mockAuthState = { welcomeBackSignal: 1, lastWelcomeBack: null };
    await renderTree();

    expect(captured!.notifications).toHaveLength(0);
    expect(appendNavSpy).not.toHaveBeenCalled();
  });

  it('pluralizes "day" correctly for a 1-day gap', async () => {
    mockAuthState = {
      welcomeBackSignal: 1,
      lastWelcomeBack: { granted: true, bonus: 50, days_inactive: 1 },
    };
    await renderTree();

    expect(captured!.notifications[0].content).toBe(
      '+50 turns — welcome back, Commander (1 day away)'
    );
    expect(appendNavSpy).toHaveBeenCalledWith(
      'Welcome back, Commander. +50 turns credited — 1 day away.'
    );
  });

  it('fires again on a second distinct signal bump (a genuine second grant)', async () => {
    mockAuthState = {
      welcomeBackSignal: 1,
      lastWelcomeBack: { granted: true, bonus: 400, days_inactive: 8 },
    };
    await renderTree();

    mockAuthState = {
      welcomeBackSignal: 2,
      lastWelcomeBack: { granted: true, bonus: 500, days_inactive: 40 },
    };
    await renderTree();

    expect(captured!.notifications).toHaveLength(2);
    expect(appendNavSpy).toHaveBeenCalledTimes(2);
  });
});
