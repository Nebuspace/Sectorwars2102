// @vitest-environment jsdom
/**
 * GameContext — messageAPI inbox/send/read (WO-WIRE-MESSAGE-API-INBOX).
 *
 * COMMS mailbox helpers must call the shared messageAPI wrappers (not raw
 * apiClient) so api.ts bindings stay live.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const {
  mockGet,
  mockPost,
  mockPut,
  mockGetInbox,
  mockSendMessage,
  mockMarkAsRead,
} = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockPut: vi.fn(),
  mockGetInbox: vi.fn(),
  mockSendMessage: vi.fn(),
  mockMarkAsRead: vi.fn(),
}));

vi.mock('../../services/apiClient', () => ({
  default: { get: mockGet, post: mockPost, put: mockPut },
  getAccessToken: vi.fn(() => 'fake-access-token'),
  refreshAccessToken: vi.fn(),
}));

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual<typeof import('../../services/api')>('../../services/api');
  return {
    ...actual,
    messageAPI: {
      ...actual.messageAPI,
      getInbox: (...a: unknown[]) => mockGetInbox(...a),
      sendMessage: (...a: unknown[]) => mockSendMessage(...a),
      markAsRead: (...a: unknown[]) => mockMarkAsRead(...a),
    },
    sectorAPI: {
      ...actual.sectorAPI,
      getPlanets: vi.fn().mockResolvedValue({ planets: [] }),
      getStations: vi.fn().mockResolvedValue({ stations: [] }),
    },
  };
});

vi.mock('../AuthContext', () => ({
  useAuth: () => ({ user: { id: 'user-1' }, isAuthenticated: true }),
}));

import { GameProvider, useGame } from '../GameContext';

function defaultGet(url: string) {
  if (url === '/api/v1/first-login/status') {
    return Promise.resolve({ data: { requires_first_login: false } });
  }
  if (url === '/api/v1/player/state') {
    return Promise.resolve({
      data: {
        id: 'player-1',
        username: 'tester',
        credits: 1000,
        turns: 10,
        max_turns: 500,
        current_sector_id: 1,
        is_docked: false,
        is_landed: false,
        defense_drones: 0,
        attack_drones: 0,
        mines: 0,
        personal_reputation: 0,
        reputation_tier: 'unknown',
        name_color: '#fff',
        military_rank: 'Recruit',
      },
    });
  }
  if (url === '/api/v1/player/ships') {
    return Promise.resolve({ data: [] });
  }
  if (url === '/api/v1/player/current-sector') {
    return Promise.resolve({ data: { sector_id: 1, name: 'Home' } });
  }
  if (url === '/api/v1/quantum/status') {
    return Promise.resolve({
      data: {
        quantum_charges: 0,
        quantum_shards: 0,
        charge_capacity: 0,
        refine_cooldown_ends_at: null,
      },
    });
  }
  return Promise.resolve({ data: {} });
}

let captured: ReturnType<typeof useGame> | null = null;
function Consumer() {
  captured = useGame();
  return null;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('GameContext messageAPI inbox/send/read', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(async () => {
    mockGet.mockImplementation(defaultGet);
    mockPost.mockResolvedValue({ data: {} });
    mockPut.mockResolvedValue({ data: {} });
    mockGetInbox.mockResolvedValue({
      messages: [
        {
          id: 'm1',
          sender_id: 'other',
          sender_name: 'Other',
          content: 'hail',
          is_read: false,
        },
      ],
      unread_count: 1,
    });
    mockSendMessage.mockResolvedValue({ message_id: 'm2', sent_at: '2026-08-08T00:00:00Z' });
    mockMarkAsRead.mockResolvedValue({});
    captured = null;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(
        <GameProvider>
          <Consumer />
        </GameProvider>,
      );
      await flush();
      await flush();
    });
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  it('refreshInbox loads via messageAPI.getInbox', async () => {
    await act(async () => {
      await captured!.refreshInbox();
      await flush();
    });
    expect(mockGetInbox).toHaveBeenCalled();
    expect(captured?.inboxMessages?.some((m) => m.id === 'm1')).toBe(true);
    expect(captured?.unreadMessageCount).toBe(1);
    const rawInbox = mockGet.mock.calls.filter((c) => String(c[0]).includes('/messages/inbox'));
    expect(rawInbox).toHaveLength(0);
  });

  it('sendPlayerMessage + markMessageRead use messageAPI wrappers', async () => {
    await act(async () => {
      await captured!.refreshInbox();
      await flush();
    });
    await act(async () => {
      const sent = await captured!.sendPlayerMessage('recv-1', 'hello', 'subj', 'm1');
      expect(sent.message_id).toBe('m2');
      await flush();
    });
    expect(mockSendMessage).toHaveBeenCalledWith('recv-1', 'hello', 'subj', 'm1');

    await act(async () => {
      await captured!.markMessageRead('m1');
      await flush();
    });
    expect(mockMarkAsRead).toHaveBeenCalledWith('m1');
    expect(captured?.unreadMessageCount).toBe(0);
    const rawRead = mockPut.mock.calls.filter((c) => String(c[0]).includes('/messages/'));
    expect(rawRead).toHaveLength(0);
  });
});
