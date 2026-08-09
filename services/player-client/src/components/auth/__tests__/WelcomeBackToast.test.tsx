// @vitest-environment jsdom
/**
 * WelcomeBackToast — bridges AuthContext welcomeBackSignal into a cockpit
 * toast + ARIA feed line (one bump = one grant; signal 0 is baseline).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockAddNotification = vi.fn();
const mockAppendNav = vi.fn();

let welcomeBackSignal = 0;
let lastWelcomeBack: { granted: boolean; bonus: number; days_inactive: number } | null = null;

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({ welcomeBackSignal, lastWelcomeBack }),
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ addNotification: mockAddNotification }),
}));

vi.mock('../../mfd/ariaFeedStore', () => ({
  ariaFeed: { appendNav: (...args: unknown[]) => mockAppendNav(...args) },
}));

import WelcomeBackToast from '../WelcomeBackToast';

describe('WelcomeBackToast', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockAddNotification.mockReset();
    mockAppendNav.mockReset();
    welcomeBackSignal = 0;
    lastWelcomeBack = null;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it('renders nothing and stays quiet on the baseline signal', async () => {
    await act(async () => {
      root.render(<WelcomeBackToast />);
    });
    expect(container.innerHTML).toBe('');
    expect(mockAddNotification).not.toHaveBeenCalled();
    expect(mockAppendNav).not.toHaveBeenCalled();
  });

  it('fires one toast + ARIA line when the signal bumps with a grant', async () => {
    await act(async () => {
      root.render(<WelcomeBackToast />);
    });

    welcomeBackSignal = 1;
    lastWelcomeBack = { granted: true, bonus: 25, days_inactive: 3 };
    await act(async () => {
      root.render(<WelcomeBackToast />);
    });

    expect(mockAddNotification).toHaveBeenCalledTimes(1);
    expect(mockAddNotification.mock.calls[0][0]).toMatchObject({
      title: 'Welcome Back',
      level: 'success',
    });
    expect(mockAddNotification.mock.calls[0][0].content).toContain('+25 turns');
    expect(mockAddNotification.mock.calls[0][0].content).toContain('3 days');
    expect(mockAppendNav).toHaveBeenCalledTimes(1);
    expect(mockAppendNav.mock.calls[0][0]).toContain('+25 turns');
  });

  it('does not re-fire when the same signal is rendered again', async () => {
    await act(async () => {
      root.render(<WelcomeBackToast />);
    });

    welcomeBackSignal = 2;
    lastWelcomeBack = { granted: true, bonus: 10, days_inactive: 1 };
    await act(async () => {
      root.render(<WelcomeBackToast />);
    });
    expect(mockAddNotification).toHaveBeenCalledTimes(1);

    await act(async () => {
      root.render(<WelcomeBackToast />);
    });
    expect(mockAddNotification).toHaveBeenCalledTimes(1);
    expect(mockAppendNav).toHaveBeenCalledTimes(1);
  });

  it('ignores a signal bump when granted is false', async () => {
    await act(async () => {
      root.render(<WelcomeBackToast />);
    });
    welcomeBackSignal = 1;
    lastWelcomeBack = { granted: false, bonus: 0, days_inactive: 0 };
    await act(async () => {
      root.render(<WelcomeBackToast />);
    });
    expect(mockAddNotification).not.toHaveBeenCalled();
    expect(mockAppendNav).not.toHaveBeenCalled();
  });
});
