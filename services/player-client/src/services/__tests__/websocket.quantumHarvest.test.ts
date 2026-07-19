/**
 * websocket.ts — onQuantumHarvest helper (WO-RT-QUANTUM-HARVEST-CONSUMER).
 *
 * quantum.py's _emit_quantum_harvest pushes a personal `quantum_harvest`
 * frame after a harvest commits (canon Resolution step 6). This pins the
 * new typed subscription helper: it fires only for that frame type, leaves
 * every other frame type alone, and unsubscribes cleanly — mirroring the
 * onChatMessage/onNotification helpers it sits beside.
 *
 * @vitest-environment jsdom
 */
import { describe, it, expect, vi } from 'vitest';

vi.mock('../apiClient', () => ({
  getAccessToken: vi.fn(() => 'fake-access-token'),
  refreshAccessToken: vi.fn(),
}));

import { websocketService, type WebSocketMessage, type QuantumHarvestMessage } from '../websocket';

// websocketService is a module-level singleton; notifyHandlers is private,
// reached the same way the eviction test reaches its private members.
const svc = websocketService as unknown as {
  notifyHandlers: (message: WebSocketMessage) => void;
};

const FRAME: QuantumHarvestMessage = {
  type: 'quantum_harvest',
  sector_id: 42,
  nebula_type: 'ion_storm',
  shards: 3,
  crit: false,
  timestamp: '2026-07-08T00:00:00Z',
};

describe('WebSocketService.onQuantumHarvest', () => {
  it('fires only for quantum_harvest frames, and stops after unsubscribe', () => {
    const received: QuantumHarvestMessage[] = [];
    const unsubscribe = websocketService.onQuantumHarvest((m) => received.push(m));

    // Unrelated frame types are ignored.
    svc.notifyHandlers({ type: 'chat_message', content: 'hi' } as WebSocketMessage);
    svc.notifyHandlers({ type: 'notification', title: 't' } as unknown as WebSocketMessage);
    expect(received).toHaveLength(0);

    svc.notifyHandlers(FRAME);
    expect(received).toEqual([FRAME]);

    unsubscribe();
    svc.notifyHandlers(FRAME);
    expect(received).toHaveLength(1);
  });
});
