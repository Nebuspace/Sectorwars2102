// @vitest-environment jsdom
/**
 * PriorityHailConsumer — inbox / unread-badge liveness off `newMessageSignal`
 * (WO-REAP-RESIDUE item 1).
 *
 * CommsMailbox.tsx (the old deck COMMS monitor) was already zero-importer
 * before its WAVE-3 physical deletion — the live consumer of the inbox has
 * been MFD-B COMM's CommsCrewPage.tsx since the WO-UI2-DECK-RECONCILE port.
 * But CommsCrewPage only mounts (mfd/MFDScreen.tsx renders exactly one
 * active page per screen), so its own newMessageSignal effect only refreshes
 * the badge while COMM happens to be the selected MFD-B page. This pins that
 * PriorityHailConsumer — mounted once in GameLayout for the whole /game
 * session — independently keeps the badge/transmissions list live off the
 * same signal, matching WebSocketContext's `new_message` handler's own
 * documented intent ("the badge stays live") regardless of which MFD page
 * is on screen.
 *
 * Mirrors PriorityHailConsumer.uplinkToast.test.tsx's seam: jsdom +
 * react-dom/client createRoot + act(), no RTL, no new deps.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

let mockWsState: {
  notifications: unknown[];
  removeNotification: (i: number) => void;
  urgentMessageSignal: number;
  lastUrgentMessage: null;
  linkStatus: 'up' | 'reconnecting' | 'down';
  addNotification: (...a: unknown[]) => void;
  newMessageSignal: number;
};

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => mockWsState,
}));

const mockRefreshInbox = vi.fn();
vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({ markMessageRead: vi.fn(), refreshInbox: mockRefreshInbox }),
}));

import PriorityHailConsumer from '../PriorityHailConsumer';

describe('PriorityHailConsumer — inbox refresh liveness', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  const bumpSignal = (newMessageSignal: number) => {
    mockWsState = { ...mockWsState, newMessageSignal };
    act(() => {
      root.render(<PriorityHailConsumer />);
    });
  };

  beforeEach(() => {
    vi.useFakeTimers();
    mockRefreshInbox.mockClear();
    mockWsState = {
      notifications: [],
      removeNotification: vi.fn(),
      urgentMessageSignal: 0,
      lastUrgentMessage: null,
      linkStatus: 'up',
      addNotification: vi.fn(),
      newMessageSignal: 0,
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

  it('does not refresh the inbox on mount at the baseline signal', () => {
    act(() => { vi.advanceTimersByTime(5000); });
    expect(mockRefreshInbox).not.toHaveBeenCalled();
  });

  it('refreshes the inbox after the debounce once a priority hail bumps newMessageSignal', () => {
    bumpSignal(1);
    expect(mockRefreshInbox).not.toHaveBeenCalled(); // debounced, not immediate

    act(() => { vi.advanceTimersByTime(1499); });
    expect(mockRefreshInbox).not.toHaveBeenCalled();

    act(() => { vi.advanceTimersByTime(1); });
    expect(mockRefreshInbox).toHaveBeenCalledTimes(1);
  });

  it('collapses a burst of several hails into a single refetch', () => {
    bumpSignal(1);
    act(() => { vi.advanceTimersByTime(500); });
    bumpSignal(2);
    act(() => { vi.advanceTimersByTime(500); });
    bumpSignal(3);
    act(() => { vi.advanceTimersByTime(1500); });

    expect(mockRefreshInbox).toHaveBeenCalledTimes(1);
  });

  it('fires again for a second, later hail after the first refetch settles', () => {
    bumpSignal(1);
    act(() => { vi.advanceTimersByTime(1500); });
    expect(mockRefreshInbox).toHaveBeenCalledTimes(1);

    bumpSignal(2);
    act(() => { vi.advanceTimersByTime(1500); });
    expect(mockRefreshInbox).toHaveBeenCalledTimes(2);
  });

  it('stays live even though CommsCrewPage (MFD-B COMM) is never mounted in this tree', () => {
    // No CommsCrewPage import/render anywhere in this file — the refresh
    // above comes entirely from PriorityHailConsumer's own effect, proving
    // the badge/list liveness no longer depends on COMM being the active
    // MFD-B page.
    bumpSignal(1);
    act(() => { vi.advanceTimersByTime(1500); });
    expect(mockRefreshInbox).toHaveBeenCalledTimes(1);
  });
});
