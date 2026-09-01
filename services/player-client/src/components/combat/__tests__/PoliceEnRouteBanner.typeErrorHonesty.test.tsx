// @vitest-environment jsdom
/**
 * LEG-3713 Soft-ORDER — PoliceEnRouteBanner TypeError/network densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PendingEngagementSummary } from '../../../services/pendingEngagementApi';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const listMine = vi.fn<() => Promise<PendingEngagementSummary[]>>();

let mockWsState: {
  isConnected: boolean;
  policeEnRouteSignal: number;
  lastPoliceEnRoute: PendingEngagementSummary | null;
  npcCombatSignal: number;
  lastNpcCombatInitiated: {
    npc_archetype: 'LAW_ENFORCEMENT' | 'HOSTILE_RAIDER';
    defender_id: string;
  } | null;
};

let mockGameState: { playerState: { id: string } | null };

vi.mock('../../../services/pendingEngagementApi', () => ({
  default: { listMine: () => listMine() },
  parsePendingEngagementSummary: vi.fn(),
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => mockWsState,
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => mockGameState,
}));

import PoliceEnRouteBanner, {
  formatPoliceEnRouteLoadError,
} from '../PoliceEnRouteBanner';

const FALLBACK = 'Failed to load law enforcement status';
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('formatPoliceEnRouteLoadError (LEG-3713)', () => {
  it('formatPoliceEnRouteLoadError falls back on TypeError network collapse', () => {
    const text = formatPoliceEnRouteLoadError(new TypeError('Failed to fetch'));
    expect(text).toBe(FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatPoliceEnRouteLoadError(new Error('Network Error'))).toBe(FALLBACK);
    expect(formatPoliceEnRouteLoadError(new Error('Failed to fetch'))).toBe(FALLBACK);
    expect(formatPoliceEnRouteLoadError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic server detail when not transport collapse', () => {
    expect(formatPoliceEnRouteLoadError(new Error('pending_engagements_offline'))).toBe(
      'pending_engagements_offline',
    );
  });
});

describe('PoliceEnRouteBanner transport collapse densify (LEG-3713)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    listMine.mockReset();
    mockWsState = {
      isConnected: true,
      policeEnRouteSignal: 0,
      lastPoliceEnRoute: null,
      npcCombatSignal: 0,
      lastNpcCombatInitiated: null,
    };
    mockGameState = { playerState: { id: 'player-1' } };
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

  it('network rejection surfaces role=alert fallback without raw transport text', async () => {
    listMine.mockRejectedValue(new Error('Network Error'));

    await act(async () => {
      root.render(<PoliceEnRouteBanner />);
    });
    await act(async () => {
      await flush();
    });

    const alert = container.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain(FALLBACK);
    expect(container.textContent).not.toMatch(/Network Error/i);
  });

  it('malformed JSON TypeError surfaces role=alert fallback without raw exception text', async () => {
    listMine.mockRejectedValue(
      new TypeError("Cannot read properties of undefined (reading 'id')"),
    );

    await act(async () => {
      root.render(<PoliceEnRouteBanner />);
    });
    await act(async () => {
      await flush();
    });

    const alert = container.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain(FALLBACK);
    expect(container.textContent).not.toMatch(/Cannot read properties/i);
  });
});
