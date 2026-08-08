// @vitest-environment jsdom
/**
 * GameContext — turn_pool_updated WS consumer (WO-WIRE-WS-TURN-POOL-UNCONSUMED).
 *
 * turn_service._emit_turn_pool_update pushes a personal turn_pool_updated
 * frame when lazy regen (or welcome_back) credits turns. This pins
 * GameContext's subscription: the frame patches playerState.turns /
 * max_turns in place; toast layer stays inert (WelcomeBackToast.wsNoOp).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const { mockGet, mockPost } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
}));

vi.mock('../../services/apiClient', () => ({
  default: { get: mockGet, post: mockPost },
  getAccessToken: vi.fn(() => 'fake-access-token'),
  refreshAccessToken: vi.fn(),
}));

vi.mock('../AuthContext', () => ({
  useAuth: () => ({ user: { id: 'user-1' }, isAuthenticated: true }),
}));

import { GameProvider, useGame } from '../GameContext';
import { websocketService, type WebSocketMessage } from '../../services/websocket';

const svc = websocketService as unknown as {
  notifyHandlers: (message: WebSocketMessage) => void;
};

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

describe('GameContext turn_pool_updated WS consumer', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(async () => {
    mockGet.mockImplementation(defaultGet);
    mockPost.mockResolvedValue({ data: {} });
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

  it('patches turns + max_turns from an authoritative turn_pool_updated frame', async () => {
    expect(captured?.playerState?.turns).toBe(10);
    expect(captured?.playerState?.max_turns).toBe(500);

    await act(async () => {
      svc.notifyHandlers({
        type: 'turn_pool_updated',
        player_id: 'player-1',
        turns: 42,
        max_turns: 500,
        turns_added: 32,
        bonus_multiplier: 1,
      });
      await flush();
    });

    expect(captured?.playerState?.turns).toBe(42);
    expect(captured?.playerState?.max_turns).toBe(500);
    // No refetch — the push is authoritative.
    expect(mockGet.mock.calls.filter((c) => c[0] === '/api/v1/player/state').length).toBe(1);
  });

  it('ignores frames for a different player_id', async () => {
    await act(async () => {
      svc.notifyHandlers({
        type: 'turn_pool_updated',
        player_id: 'someone-else',
        turns: 99,
        max_turns: 500,
      });
      await flush();
    });

    expect(captured?.playerState?.turns).toBe(10);
  });

  it('ignores frames missing a numeric turns field', async () => {
    await act(async () => {
      svc.notifyHandlers({
        type: 'turn_pool_updated',
        player_id: 'player-1',
        max_turns: 500,
      });
      await flush();
    });

    expect(captured?.playerState?.turns).toBe(10);
  });
});
