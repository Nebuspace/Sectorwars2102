// @vitest-environment jsdom
/**
 * LEG-3762 Soft-ORDER — PriorityHailConsumer inbox-refresh TypeError/network densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

let mockWsState: {
  notifications: Array<{ title: string; content?: string; level: string; timestamp: number }>;
  removeNotification: (i: number) => void;
  urgentMessageSignal: number;
  lastUrgentMessage: {
    message_id?: string;
    sender_name?: string;
    preview?: string;
  } | null;
  linkStatus: 'up' | 'reconnecting' | 'down';
  addNotification: (...a: unknown[]) => void;
  newMessageSignal: number;
};

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => mockWsState,
}));

const mockRefreshInbox = vi.fn();
const mockMarkMessageRead = vi.fn();

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({ markMessageRead: mockMarkMessageRead, refreshInbox: mockRefreshInbox }),
}));

import PriorityHailConsumer, { formatInboxRefreshError } from '../PriorityHailConsumer';

const FALLBACK = 'Inbox refresh failed';

describe('formatInboxRefreshError (LEG-3762)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatInboxRefreshError(new TypeError('Failed to fetch'));
    expect(text).toBe(FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatInboxRefreshError(new Error('Network Error'))).toBe(FALLBACK);
    expect(formatInboxRefreshError(new Error('Failed to fetch'))).toBe(FALLBACK);
    expect(formatInboxRefreshError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic server detail when not transport collapse', () => {
    expect(formatInboxRefreshError(new Error('inbox_offline'))).toBe('inbox_offline');
  });
});

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('formatInboxRefreshError 403/429 densify (LEG-4041)', () => {
  it('surfaces 403/429 without raw status codes', () => {
    expect(formatInboxRefreshError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatInboxRefreshError(apiRequestError(403, 'inbox_denied'))).toBe('inbox_denied');
    expect(formatInboxRefreshError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatInboxRefreshError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatInboxRefreshError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});

describe('PriorityHailConsumer inbox refresh transport collapse densify (LEG-3762)', () => {
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
    mockRefreshInbox.mockReset().mockRejectedValue(new Error('Network Error'));
    mockMarkMessageRead.mockReset().mockResolvedValue(undefined);
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

  it('inbox refresh Network Error does not leak transport strings into toast/modal chrome', async () => {
    bumpSignal(1);
    await act(async () => {
      vi.advanceTimersByTime(1500);
      await Promise.resolve();
    });

    expect(mockRefreshInbox).toHaveBeenCalledTimes(1);
    expect(document.body.textContent).not.toMatch(/Network Error/i);
    expect(document.body.textContent).not.toMatch(/Failed to fetch/i);
    expect(document.body.textContent).not.toMatch(/TypeError/i);
    expect(container.querySelector('.phc-toast')).toBeNull();
    expect(container.querySelector('.phc-modal')).toBeNull();
  });

  it('urgent modal preview stays stable when inbox refresh rejects', async () => {
    mockWsState = {
      ...mockWsState,
      urgentMessageSignal: 1,
      lastUrgentMessage: {
        message_id: 'msg-1',
        sender_name: 'Dispatch',
        preview: 'Priority hail received.',
      },
    };
    act(() => {
      root.render(<PriorityHailConsumer />);
    });
    bumpSignal(1);
    await act(async () => {
      vi.advanceTimersByTime(1500);
      await Promise.resolve();
    });

    expect(document.body.textContent).toContain('Priority hail received.');
    expect(document.body.textContent).not.toMatch(/Network Error/i);
    expect(document.body.textContent).not.toMatch(/Failed to fetch/i);
  });
});
