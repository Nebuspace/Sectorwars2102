// @vitest-environment jsdom
/**
 * PriorityHailConsumer — urgent modal sender PlayerNamePlate (LEG-2779).
 * Mocks WebSocketContext with lastUrgentMessage medal fields from the GS
 * new_message frame (sender_pinned_medal_id / sender_medal_count).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const addNotification = vi.fn();
const removeNotification = vi.fn();
const markMessageRead = vi.fn();

let mockWsState: {
  notifications: unknown[];
  removeNotification: typeof removeNotification;
  urgentMessageSignal: number;
  lastUrgentMessage: {
    message_id: string;
    sender_name: string;
    sender_pinned_medal_id?: string | null;
    sender_medal_count?: number | null;
    preview: string;
    sent_at: string | null;
  } | null;
  linkStatus: 'up';
  addNotification: typeof addNotification;
  newMessageSignal: number;
};

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => mockWsState,
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({ markMessageRead, refreshInbox: vi.fn() }),
}));

import PriorityHailConsumer from '../PriorityHailConsumer';

describe('PriorityHailConsumer — urgent modal sender PlayerNamePlate', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  const renderWithUrgent = (urgentMessageSignal: number) => {
    mockWsState = {
      ...mockWsState,
      urgentMessageSignal,
      lastUrgentMessage: {
        message_id: 'msg-urgent-1',
        sender_name: 'Admiral Vex',
        sender_pinned_medal_id: 'bronze_cluster',
        sender_medal_count: 4,
        preview: 'Priority transmission received.',
        sent_at: '2026-08-28T12:00:00Z',
      },
    };
    act(() => {
      root.render(<PriorityHailConsumer />);
    });
  };

  beforeEach(() => {
    vi.useFakeTimers();
    addNotification.mockClear();
    removeNotification.mockClear();
    markMessageRead.mockClear();
    mockWsState = {
      notifications: [],
      removeNotification,
      urgentMessageSignal: 0,
      lastUrgentMessage: null,
      linkStatus: 'up',
      addNotification,
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
    document.body.querySelectorAll('.phc-modal-backdrop').forEach((el) => el.remove());
    vi.useRealTimers();
  });

  it('renders PlayerNamePlate with medal pin and count when urgent modal opens', () => {
    renderWithUrgent(1);

    const label = document.body.querySelector('.phc-modal-sender-label');
    expect(label?.textContent).toBe('FROM:');

    const plate = document.body.querySelector('[data-testid="player-name-plate"]');
    expect(plate).not.toBeNull();
    expect(plate?.querySelector('.pnp-name')?.textContent).toBe('Admiral Vex');
    expect(plate?.getAttribute('data-pinned-medal')).toBe('bronze_cluster');
    expect(plate?.querySelector('[data-testid="player-name-plate-medal"]')).not.toBeNull();
    expect(plate?.querySelector('[data-testid="player-name-plate-count"]')?.textContent).toBe('4');
  });

  it('renders name-only plate when medal fields are absent', () => {
    mockWsState = {
      ...mockWsState,
      urgentMessageSignal: 1,
      lastUrgentMessage: {
        message_id: 'msg-urgent-2',
        sender_name: 'Unknown Contact',
        preview: 'No medal identity on frame.',
        sent_at: null,
      },
    };
    act(() => {
      root.render(<PriorityHailConsumer />);
    });

    const plate = document.body.querySelector('[data-testid="player-name-plate"]');
    expect(plate?.querySelector('.pnp-name')?.textContent).toBe('Unknown Contact');
    expect(plate?.querySelector('[data-testid="player-name-plate-medal"]')).toBeNull();
    expect(plate?.querySelector('[data-testid="player-name-plate-count"]')).toBeNull();
  });
});
