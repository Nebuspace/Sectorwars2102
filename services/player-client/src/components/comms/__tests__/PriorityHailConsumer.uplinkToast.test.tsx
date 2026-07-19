// @vitest-environment jsdom
/**
 * PriorityHailConsumer — uplink lost/restored toast pairing
 * (WO-PUX-UPLINK-HUD). WebSocketContext is mocked to a mutable, reassignable
 * object (mirrors WelcomeBackToast.test.tsx / market-stream-integration
 * .test.tsx's seam) so linkStatus transitions can be driven directly, rather
 * than through the full websocketService reconnect machinery — that
 * machinery's own linkStatus emission is covered separately in
 * websocket.linkStatus.test.ts. This file pins only the CONSUMER's
 * debounce + one-pair-per-outage behavior.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

type LinkStatus = 'up' | 'reconnecting' | 'down';

const addNotification = vi.fn();
let mockWsState: {
  notifications: unknown[];
  removeNotification: (i: number) => void;
  urgentMessageSignal: number;
  lastUrgentMessage: null;
  linkStatus: LinkStatus;
  addNotification: typeof addNotification;
};

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => mockWsState,
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({ markMessageRead: vi.fn() }),
}));

import PriorityHailConsumer from '../PriorityHailConsumer';

describe('PriorityHailConsumer — uplink toast pairing', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  const setLinkStatus = (linkStatus: LinkStatus) => {
    mockWsState = { ...mockWsState, linkStatus };
    act(() => {
      root.render(<PriorityHailConsumer />);
    });
  };

  beforeEach(() => {
    vi.useFakeTimers();
    addNotification.mockClear();
    mockWsState = {
      notifications: [],
      removeNotification: vi.fn(),
      urgentMessageSignal: 0,
      lastUrgentMessage: null,
      linkStatus: 'up',
      addNotification,
    };
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => {
      root.render(<PriorityHailConsumer />);
    });
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.useRealTimers();
  });

  it('is inert on mount at the "up" baseline', () => {
    expect(addNotification).not.toHaveBeenCalled();
  });

  it('does not toast a sub-threshold blip (reconnecting -> up before 2s)', () => {
    setLinkStatus('reconnecting');
    act(() => { vi.advanceTimersByTime(1999); });
    setLinkStatus('up');
    act(() => { vi.advanceTimersByTime(10000); });

    expect(addNotification).not.toHaveBeenCalled();
  });

  it('does not fire before the debounce boundary (1999ms)', () => {
    setLinkStatus('reconnecting');
    act(() => { vi.advanceTimersByTime(1999); });

    expect(addNotification).not.toHaveBeenCalled();
  });

  it('fires the "lost" toast exactly at the 2000ms debounce boundary', () => {
    setLinkStatus('reconnecting');
    act(() => { vi.advanceTimersByTime(2000); });

    expect(addNotification).toHaveBeenCalledTimes(1);
    expect(addNotification).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Uplink lost — reconnecting', level: 'warning' })
    );
  });

  it('fires exactly one lost + one restored pair for a sustained outage', () => {
    setLinkStatus('reconnecting');
    act(() => { vi.advanceTimersByTime(2000); });
    setLinkStatus('up');

    expect(addNotification).toHaveBeenCalledTimes(2);
    expect(addNotification.mock.calls[0][0]).toEqual(
      expect.objectContaining({ title: 'Uplink lost — reconnecting', level: 'warning' })
    );
    expect(addNotification.mock.calls[1][0]).toEqual(
      expect.objectContaining({ title: 'Uplink restored', level: 'success' })
    );
  });

  it('does not double-toast "lost" across multiple backoff flaps within one outage', () => {
    setLinkStatus('reconnecting');
    act(() => { vi.advanceTimersByTime(2000); }); // lost toast fires
    setLinkStatus('down');    // terminal close mid-outage
    setLinkStatus('reconnecting'); // retry loop resumes
    setLinkStatus('down');
    setLinkStatus('reconnecting');

    expect(addNotification).toHaveBeenCalledTimes(1);
    expect(addNotification).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Uplink lost — reconnecting' })
    );

    setLinkStatus('up');
    expect(addNotification).toHaveBeenCalledTimes(2);
    expect(addNotification.mock.calls[1][0]).toEqual(
      expect.objectContaining({ title: 'Uplink restored' })
    );
  });

  it('does not fire "restored" for a recovery that never crossed the debounce', () => {
    setLinkStatus('reconnecting');
    act(() => { vi.advanceTimersByTime(500); });
    setLinkStatus('up');

    expect(addNotification).not.toHaveBeenCalled();
  });

  it('fires a second independent pair for a second distinct outage', () => {
    setLinkStatus('reconnecting');
    act(() => { vi.advanceTimersByTime(2000); });
    setLinkStatus('up');
    expect(addNotification).toHaveBeenCalledTimes(2);

    setLinkStatus('down');
    act(() => { vi.advanceTimersByTime(2000); });
    setLinkStatus('up');

    expect(addNotification).toHaveBeenCalledTimes(4);
    expect(addNotification.mock.calls[2][0]).toEqual(
      expect.objectContaining({ title: 'Uplink lost — reconnecting' })
    );
    expect(addNotification.mock.calls[3][0]).toEqual(
      expect.objectContaining({ title: 'Uplink restored' })
    );
  });
});
