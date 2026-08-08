// @vitest-environment jsdom
/**
 * SiegeStatusMonitor — WO-WIRE-SIEGE-ACTION-BUTTONS.
 * Pins Emergency Aid / Negotiate Surrender onClick → team/message APIs.
 */
import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const sendTeamMessage = vi.fn();
const sendDirectMessage = vi.fn();

vi.mock('../../../services/api', () => ({
  gameAPI: {},
  teamAPI: {
    sendMessage: (...args: unknown[]) => sendTeamMessage(...args),
  },
  messageAPI: {
    sendMessage: (...args: unknown[]) => sendDirectMessage(...args),
  },
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: { team_id: 'team-1' },
  }),
}));

import { SiegeStatusMonitor } from '../SiegeStatusMonitor';
import type { Planet } from '../../../types/planetary';

const siegedPlanet: Planet = {
  id: 'p1',
  name: 'Outpost Alpha',
  sectorId: '42',
  sectorName: 'Sector 42',
  planetType: 'TERRAN',
  colonists: 1000,
  maxColonists: 5000,
  productionRates: { fuel: 0, organics: 0, equipment: 0, colonists: 0, research: 0 },
  allocations: { fuel: 0, organics: 0, equipment: 0, unused: 0 },
  buildings: [],
  defenses: { turrets: 1, shields: 1, drones: 0 },
  underSiege: true,
  siegeDetails: {
    attackerId: 'attacker-1',
    attackerName: 'Raider',
    phase: 'orbital',
    startTime: new Date().toISOString(),
    defenseEffectiveness: 50,
  },
};

describe('SiegeStatusMonitor action buttons', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    sendTeamMessage.mockReset().mockResolvedValue({});
    sendDirectMessage.mockReset().mockResolvedValue({});
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.restoreAllMocks();
  });

  it('Emergency Aid posts a high-priority team message', async () => {
    await act(async () => {
      root.render(<SiegeStatusMonitor planet={siegedPlanet} />);
    });

    const btn = container.querySelector('[data-testid="siege-emergency-aid"]') as HTMLButtonElement;
    expect(btn).not.toBeNull();

    await act(async () => {
      btn.click();
      await Promise.resolve();
    });

    expect(sendTeamMessage).toHaveBeenCalledTimes(1);
    expect(sendTeamMessage.mock.calls[0][0]).toBe('team-1');
    expect(sendTeamMessage.mock.calls[0][2]).toBe('high');
    expect(String(sendTeamMessage.mock.calls[0][1])).toMatch(/EMERGENCY AID/);
  });

  it('Negotiate Surrender hails the besieger when confirmed', async () => {
    await act(async () => {
      root.render(<SiegeStatusMonitor planet={siegedPlanet} />);
    });

    const btn = container.querySelector(
      '[data-testid="siege-negotiate-surrender"]'
    ) as HTMLButtonElement;

    await act(async () => {
      btn.click();
      await Promise.resolve();
    });

    expect(sendDirectMessage).toHaveBeenCalledTimes(1);
    expect(sendDirectMessage.mock.calls[0][0]).toBe('attacker-1');
    expect(String(sendDirectMessage.mock.calls[0][1])).toMatch(/negotiation/i);
  });
});
