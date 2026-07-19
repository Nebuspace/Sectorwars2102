// @vitest-environment jsdom
/**
 * GameContext — quantum_harvest WS consumer (WO-RT-QUANTUM-HARVEST-CONSUMER).
 *
 * quantum.py's _emit_quantum_harvest pushes a personal `quantum_harvest`
 * frame after a harvest commits (canon Resolution step 6: "client UI
 * updates without polling"). This pins GameContext's subscription: the
 * frame refreshes quantumStatus and narrates exactly one line into the ARIA
 * feed store; unrelated frame types and malformed harvest payloads are
 * inert / non-throwing.
 *
 * Exercises the REAL websocketService singleton (not mocked) so the
 * type-filtering inside onQuantumHarvest is covered end-to-end, reaching
 * its private notifyHandlers the same way websocket.eviction.test.ts does.
 * apiClient and AuthContext are mocked at the module boundary, following
 * FirstLoginContext.resume.test.tsx.
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
  useAuth: () => ({ user: { id: 'player-1' }, isAuthenticated: true }),
}));

import { GameProvider, useGame } from '../GameContext';
import { websocketService, type WebSocketMessage } from '../../services/websocket';
import { ariaFeed } from '../../components/mfd/ariaFeedStore';

// websocketService is a module-level singleton; notifyHandlers is private,
// reached the same way websocket.eviction.test.ts reaches its private members.
const svc = websocketService as unknown as {
  notifyHandlers: (message: WebSocketMessage) => void;
};

const QUANTUM_STATUS_RESPONSE = {
  quantum_charges: 2,
  quantum_shards: 9,
  charge_capacity: 4,
  refine_cooldown_ends_at: null,
};

function defaultGet(url: string) {
  if (url === '/api/v1/first-login/status') {
    return Promise.resolve({ data: { requires_first_login: false } });
  }
  if (url === '/api/v1/player/state') {
    return Promise.resolve({ data: { turns: 10 } });
  }
  if (url === '/api/v1/player/ships') {
    return Promise.resolve({ data: [] });
  }
  if (url === '/api/v1/quantum/status') {
    return Promise.resolve({ data: QUANTUM_STATUS_RESPONSE });
  }
  return Promise.resolve({ data: {} });
}

let captured: ReturnType<typeof useGame> | null = null;
function Consumer() {
  captured = useGame();
  return null;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('GameContext quantum_harvest WS consumer', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let appendNavSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(async () => {
    captured = null;
    mockGet.mockReset();
    mockPost.mockReset();
    mockGet.mockImplementation(defaultGet);
    appendNavSpy = vi.spyOn(ariaFeed, 'appendNav').mockImplementation(() => {});

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);

    await act(async () => {
      root.render(
        <GameProvider>
          <Consumer />
        </GameProvider>
      );
    });
    // Flush the mount-time checkFirstLoginStatus -> refreshPlayerState/loadShips chain.
    await act(async () => {
      await flush();
      await flush();
      await flush();
    });

    // Isolate the harvest-triggered call from the mount-time hydration calls.
    mockGet.mockClear();
    appendNavSpy.mockClear();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    appendNavSpy.mockRestore();
  });

  it('refreshes quantum status and narrates exactly one feed line on a quantum_harvest frame', async () => {
    await act(async () => {
      svc.notifyHandlers({
        type: 'quantum_harvest',
        sector_id: 7,
        nebula_type: 'ion_storm',
        shards: 3,
        crit: false,
        timestamp: '2026-07-08T00:00:00Z',
      } as WebSocketMessage);
      await flush();
      await flush();
    });

    const quantumStatusCalls = mockGet.mock.calls.filter(([url]) => url === '/api/v1/quantum/status');
    expect(quantumStatusCalls).toHaveLength(1);
    expect(appendNavSpy).toHaveBeenCalledTimes(1);
    expect(appendNavSpy).toHaveBeenCalledWith('Harvested 3 quantum shards.');
    expect(captured?.quantumStatus).toEqual(QUANTUM_STATUS_RESPONSE);
  });

  it('appends the critical suffix on a crit harvest', async () => {
    await act(async () => {
      svc.notifyHandlers({
        type: 'quantum_harvest',
        sector_id: 7,
        nebula_type: 'ion_storm',
        shards: 1,
        crit: true,
        timestamp: '2026-07-08T00:00:00Z',
      } as WebSocketMessage);
      await flush();
      await flush();
    });

    expect(appendNavSpy).toHaveBeenCalledWith('Harvested 1 quantum shard — critical!.');
  });

  it('ignores unrelated frame types', async () => {
    await act(async () => {
      svc.notifyHandlers({ type: 'chat_message', content: 'hi' } as unknown as WebSocketMessage);
      await flush();
      await flush();
    });

    expect(mockGet).not.toHaveBeenCalled();
    expect(appendNavSpy).not.toHaveBeenCalled();
  });

  it('degrades defensively on a malformed quantum_harvest payload (missing shards/crit)', async () => {
    await act(async () => {
      expect(() => {
        svc.notifyHandlers({ type: 'quantum_harvest' } as WebSocketMessage);
      }).not.toThrow();
      await flush();
      await flush();
    });

    expect(appendNavSpy).toHaveBeenCalledTimes(1);
    expect(appendNavSpy).toHaveBeenCalledWith('Harvested 0 quantum shards.');
    const quantumStatusCalls = mockGet.mock.calls.filter(([url]) => url === '/api/v1/quantum/status');
    expect(quantumStatusCalls).toHaveLength(1);
  });
});
