// @vitest-environment jsdom
/**
 * PoliceEnRouteBanner (LEG-902) — pending LAW en-route countdown HUD.
 * Mocks pendingEngagementApi + WebSocket/Game contexts (consumer-only tests).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { PendingEngagementSummary } from '../../../services/pendingEngagementApi';

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

import PoliceEnRouteBanner from '../PoliceEnRouteBanner';

const sampleEngagement: PendingEngagementSummary = {
  id: 'pe-1',
  jurisdiction: 'core',
  offense_type: 'smuggling',
  squad: ['Marshal Vance'],
  officer_names: ['Marshal Vance'],
  turns_to_arrival: 2,
  grace_window: null,
};

describe('PoliceEnRouteBanner', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  const render = async () => {
    await act(async () => {
      root.render(<PoliceEnRouteBanner />);
      await Promise.resolve();
    });
  };

  beforeEach(() => {
    listMine.mockReset();
    listMine.mockResolvedValue([]);
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

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it('is hidden when the pending list is empty', async () => {
    await render();
    expect(container.querySelector('.police-en-route-banner')).toBeNull();
    expect(listMine).toHaveBeenCalled();
  });

  it('shows server countdown copy when GET returns an engagement', async () => {
    listMine.mockResolvedValue([sampleEngagement]);
    await render();

    const banner = container.querySelector('.police-en-route-banner');
    expect(banner).not.toBeNull();
    expect(banner!.textContent).toContain('Marshal Vance is en route — 2 turns to arrival');
    expect(banner!.getAttribute('role')).toBe('alert');
  });

  it('updates when a WS police_en_route signal arrives', async () => {
    listMine.mockResolvedValue([]);
    await render();
    expect(container.querySelector('.police-en-route-banner')).toBeNull();

    mockWsState = {
      ...mockWsState,
      policeEnRouteSignal: 1,
      lastPoliceEnRoute: { ...sampleEngagement, turns_to_arrival: 1 },
    };
    await render();

    const banner = container.querySelector('.police-en-route-banner');
    expect(banner!.textContent).toContain('1 turn to arrival');
  });

  it('clears when LAW npc_combat_initiated targets the current player', async () => {
    listMine.mockResolvedValue([sampleEngagement]);
    await render();
    expect(container.querySelector('.police-en-route-banner')).not.toBeNull();

    mockWsState = {
      ...mockWsState,
      npcCombatSignal: 1,
      lastNpcCombatInitiated: {
        npc_archetype: 'LAW_ENFORCEMENT',
        defender_id: 'player-1',
      },
    };
    await render();

    expect(container.querySelector('.police-en-route-banner')).toBeNull();
  });

  it('dismiss hides the banner until a fresh WS update', async () => {
    listMine.mockResolvedValue([sampleEngagement]);
    await render();

    const dismiss = container.querySelector(
      '.police-en-route-banner-dismiss'
    ) as HTMLButtonElement;
    expect(dismiss).not.toBeNull();

    await act(async () => {
      dismiss.click();
    });
    expect(container.querySelector('.police-en-route-banner')).toBeNull();

    mockWsState = {
      ...mockWsState,
      policeEnRouteSignal: 1,
      lastPoliceEnRoute: { ...sampleEngagement, turns_to_arrival: 3 },
    };
    await render();

    expect(container.querySelector('.police-en-route-banner')).not.toBeNull();
    expect(container.textContent).toContain('3 turns to arrival');
  });

  it('load TypeError yields empty banner without Failed to fetch / TypeError in DOM (LEG-3258)', async () => {
    listMine.mockRejectedValue(new TypeError('Failed to fetch'));
    await render();

    expect(container.querySelector('.police-en-route-banner')).toBeNull();
    expect(container.textContent ?? '').not.toMatch(/Failed to fetch/i);
    expect(container.textContent ?? '').not.toMatch(/TypeError/i);
    expect(listMine).toHaveBeenCalled();
  });
});
