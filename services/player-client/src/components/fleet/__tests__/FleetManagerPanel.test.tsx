// @vitest-environment jsdom
/**
 * FleetManagerPanel — LEG-INI-01 roster + LEG-2278 / LEG-308 battle viewer.
 * Pins roster load, create, member composition, formation/gauges, and
 * initiateBattle / simulateBattleRound with the correct IDs.
 */
import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const {
  getFleets,
  createFleet,
  getFleetMembers,
  addShipToFleet,
  getBattles,
  getBattle,
  initiateBattle,
  simulateBattleRound,
} = vi.hoisted(() => ({
  getFleets: vi.fn(),
  createFleet: vi.fn(),
  getFleetMembers: vi.fn(),
  addShipToFleet: vi.fn(),
  removeShipFromFleet: vi.fn(),
  updateFormation: vi.fn(),
  disbandFleet: vi.fn(),
  resupplyFleet: vi.fn(),
  getBattles: vi.fn(),
  getBattle: vi.fn(),
  initiateBattle: vi.fn(),
  simulateBattleRound: vi.fn(),
}));

vi.mock('../../../services/api', () => ({
  fleetAPI: {
    getFleets: (...a: unknown[]) => getFleets(...a),
    createFleet: (...a: unknown[]) => createFleet(...a),
    getFleetMembers: (...a: unknown[]) => getFleetMembers(...a),
    addShipToFleet: (...a: unknown[]) => addShipToFleet(...a),
    removeShipFromFleet: vi.fn(),
    updateFormation: vi.fn(),
    disbandFleet: vi.fn(),
    resupplyFleet: vi.fn(),
    getBattles: (...a: unknown[]) => getBattles(...a),
    getBattle: (...a: unknown[]) => getBattle(...a),
    initiateBattle: (...a: unknown[]) => initiateBattle(...a),
    simulateBattleRound: (...a: unknown[]) => simulateBattleRound(...a),
  },
}));

const DEFENDER_FLEET_UUID = 'cccccccc-dddd-eeee-ffff-000000000001';

const mockGame = vi.hoisted(() => ({
  ships: [
    { id: 'ship-1', name: 'Arrow', type: 'SCOUT' },
    { id: 'ship-2', name: 'Hammer', type: 'DESTROYER' },
  ],
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    ships: mockGame.ships,
  }),
}));

vi.mock('../../cockpit/CockpitInstrument', () => ({
  default: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="cockpit-instrument">{children}</div>
  ),
}));

vi.mock('../../cockpit/EmbeddedContext', () => ({
  useEmbedded: () => true,
}));

import FleetManagerPanel from '../FleetManagerPanel';

const sampleFleet = {
  id: 'fleet-1',
  name: 'Alpha Wing',
  status: 'forming',
  formation: 'standard',
  total_ships: 0,
  total_firepower: 0,
  total_shields: 0,
  total_hull: 0,
  coordination_bonus: 1,
  morale: 100,
  supply_level: 100,
  commander_name: null,
  sector_id: null,
  sector_name: null,
  member_count: 0,
};

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

const selectFleet = async (container: HTMLDivElement) => {
  await act(async () => {
    (container.querySelector(
      '[data-testid="fleet-select-fleet-1"]'
    ) as HTMLButtonElement).click();
  });
};

const setSelectValue = async (select: HTMLSelectElement, value: string) => {
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLSelectElement.prototype,
      'value'
    )?.set;
    setter?.call(select, value);
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
};

describe('FleetManagerPanel', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    getFleets.mockReset().mockResolvedValue([]);
    createFleet.mockReset().mockResolvedValue(sampleFleet);
    getFleetMembers.mockReset().mockResolvedValue([]);
    addShipToFleet.mockReset().mockResolvedValue({});
    getBattles.mockReset().mockResolvedValue([]);
    getBattle.mockReset().mockResolvedValue({});
    initiateBattle.mockReset().mockResolvedValue({ battle_id: 'battle-1' });
    simulateBattleRound.mockReset().mockResolvedValue({
      battle_id: 'battle-1',
      battle_ongoing: true,
      round_results: { round: 1, attacker_damage: 4, defender_damage: 2, ships_destroyed: [] },
    });
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

  it('shows empty roster when player has no fleets', async () => {
    await act(async () => {
      root.render(<FleetManagerPanel />);
    });
    expect(getFleets).toHaveBeenCalled();
    expect(container.querySelector('[data-testid="fleet-empty"]')?.textContent).toMatch(/No fleets/);
  });

  it('lists fleets and loads composition on select', async () => {
    getFleets.mockResolvedValue([sampleFleet]);
    getFleetMembers.mockResolvedValue([
      {
        id: 'm1',
        ship_id: 'ship-1',
        ship_name: 'Arrow',
        ship_type: 'SCOUT',
        player_name: 'pilot',
        role: 'attacker',
        position: 0,
        ready_status: true,
      },
    ]);

    await act(async () => {
      root.render(<FleetManagerPanel />);
    });

    const selectBtn = container.querySelector(
      '[data-testid="fleet-select-fleet-1"]'
    ) as HTMLButtonElement;
    expect(selectBtn).toBeTruthy();

    await act(async () => {
      selectBtn.click();
    });

    expect(getFleetMembers).toHaveBeenCalledWith('fleet-1');
    expect(container.querySelector('[data-testid="fleet-members"]')?.textContent).toMatch(/Arrow/);
    expect(container.querySelector('[data-testid="fleet-stats"]')?.textContent).toMatch(/forming/i);
  });

  it('creates a fleet via fleetAPI.createFleet', async () => {
    getFleets
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([sampleFleet]);

    await act(async () => {
      root.render(<FleetManagerPanel />);
    });

    const nameInput = container.querySelector(
      '[data-testid="fleet-create-name"]'
    ) as HTMLInputElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value'
      )?.set;
      setter?.call(nameInput, 'Alpha Wing');
      nameInput.dispatchEvent(new Event('input', { bubbles: true }));
    });

    const submit = container.querySelector(
      '[data-testid="fleet-create-submit"]'
    ) as HTMLButtonElement;
    await act(async () => {
      submit.click();
    });

    expect(createFleet).toHaveBeenCalledWith('Alpha Wing', 'standard');
    expect(getFleets.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it('shows formation attack/defense preview from canon table (LEG-308)', async () => {
    getFleets.mockResolvedValue([sampleFleet]);
    getFleetMembers.mockResolvedValue([]);

    await act(async () => {
      root.render(<FleetManagerPanel />);
    });
    await selectFleet(container);

    const preview = container.querySelector('[data-testid="fleet-formation-preview"]');
    expect(preview?.textContent).toMatch(/Attack ×1\.00/);
    expect(preview?.textContent).toMatch(/Defense ×1\.00/);

    const select = container.querySelector(
      '[data-testid="fleet-formation-select"]',
    ) as HTMLSelectElement;
    await setSelectValue(select, 'turtle');
    expect(preview?.textContent).toMatch(/Attack ×0\.60/);
    expect(preview?.textContent).toMatch(/Defense ×1\.40/);
  });

  it('renders morale/supply as gauges (LEG-308)', async () => {
    getFleets.mockResolvedValue([{ ...sampleFleet, morale: 72, supply_level: 40 }]);
    getFleetMembers.mockResolvedValue([]);

    await act(async () => {
      root.render(<FleetManagerPanel />);
    });
    await selectFleet(container);

    expect(container.querySelector('[data-testid="fleet-morale-gauge"]')?.textContent).toMatch(
      /72/,
    );
    expect(container.querySelector('[data-testid="fleet-supply-gauge"]')?.textContent).toMatch(
      /40/,
    );
  });

  it('polls battle status and renders battle_log rounds when present (LEG-308)', async () => {
    getFleets.mockResolvedValue([{ ...sampleFleet, status: 'in_battle' }]);
    getFleetMembers.mockResolvedValue([]);
    getBattles.mockResolvedValue([
      {
        battle_id: 'battle-1',
        attacker_fleet_id: 'fleet-1',
        defender_fleet_id: 'fleet-2',
        battle_ongoing: true,
      },
    ]);
    getBattle.mockResolvedValue({
      battle_id: 'battle-1',
      phase: 'combat',
      rounds_completed: 2,
      battle_log: [
        { round: 1, attacker_damage: 12, defender_damage: 8, ships_destroyed: [] },
        { round: 2, attacker_damage: 5, defender_damage: 20, ships_destroyed: ['x'] },
      ],
      attacker: { ships_remaining: 3 },
      defender: { ships_remaining: 2 },
      casualties: { attacker: [], defender: [{ ship_name: 'X', destroyed: true }] },
    });

    await act(async () => {
      root.render(<FleetManagerPanel />);
    });
    await selectFleet(container);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(getBattles).toHaveBeenCalled();
    expect(getBattle).toHaveBeenCalledWith('battle-1');
    expect(container.querySelector('[data-testid="fleet-battle-viewer"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="fleet-battle-log"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="fleet-battle-round-1"]')?.textContent).toMatch(
      /Round 1/,
    );
    expect(container.querySelector('[data-testid="fleet-battle-round-2"]')?.textContent).toMatch(
      /destroyed 1/,
    );
  });

  it('calls initiateBattle with selected fleet id and defender uuid (LEG-2278)', async () => {
    getFleets.mockResolvedValue([sampleFleet]);
    getFleetMembers.mockResolvedValue([]);

    await act(async () => {
      root.render(<FleetManagerPanel />);
    });
    await selectFleet(container);

    const input = container.querySelector(
      '[data-testid="fleet-battle-defender"]',
    ) as HTMLInputElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      )?.set;
      setter?.call(input, DEFENDER_FLEET_UUID);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });

    await act(async () => {
      (container.querySelector(
        '[data-testid="fleet-battle-initiate-submit"]',
      ) as HTMLButtonElement).click();
    });

    expect(initiateBattle).toHaveBeenCalledWith('fleet-1', DEFENDER_FLEET_UUID);
  });

  it('surfaces createFleet 403 refusal in fleet-manager-error', async () => {
    getFleets.mockResolvedValue([]);
    createFleet.mockRejectedValue(
      apiRequestError(403, 'Galactic Citizen membership required to create fleets.'),
    );

    await act(async () => {
      root.render(<FleetManagerPanel />);
    });

    const nameInput = container.querySelector(
      '[data-testid="fleet-create-name"]',
    ) as HTMLInputElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      )?.set;
      setter?.call(nameInput, 'New Wing');
      nameInput.dispatchEvent(new Event('input', { bubbles: true }));
    });

    await act(async () => {
      (container.querySelector(
        '[data-testid="fleet-create-submit"]',
      ) as HTMLButtonElement).click();
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    const alert = container.querySelector('[data-testid="fleet-manager-error"]');
    expect(alert?.getAttribute('role')).toBe('alert');
    expect(alert?.textContent).toContain('Galactic Citizen membership required to create fleets.');
  });

  it('surfaces createFleet 429 rate-limit copy in fleet-manager-error', async () => {
    getFleets.mockResolvedValue([]);
    createFleet.mockRejectedValue(
      apiRequestError(429, 'Rate limit exceeded — try again shortly.'),
    );

    await act(async () => {
      root.render(<FleetManagerPanel />);
    });

    const nameInput = container.querySelector(
      '[data-testid="fleet-create-name"]',
    ) as HTMLInputElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      )?.set;
      setter?.call(nameInput, 'New Wing');
      nameInput.dispatchEvent(new Event('input', { bubbles: true }));
    });

    await act(async () => {
      (container.querySelector(
        '[data-testid="fleet-create-submit"]',
      ) as HTMLButtonElement).click();
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    const alert = container.querySelector('[data-testid="fleet-manager-error"]');
    expect(alert?.getAttribute('role')).toBe('alert');
    expect(alert?.textContent).toMatch(/rate limit exceeded/i);
  });

  it('surfaces getFleets 403 load error in fleet-manager-error on mount', async () => {
    getFleets.mockRejectedValue(
      apiRequestError(403, 'Team membership required to view fleet roster.'),
    );

    await act(async () => {
      root.render(<FleetManagerPanel />);
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    const alert = container.querySelector('[data-testid="fleet-manager-error"]');
    expect(alert?.getAttribute('role')).toBe('alert');
    expect(alert?.textContent).toContain('Team membership required to view fleet roster.');
  });

  it('surfaces initiateBattle API errors in the panel alert', async () => {
    getFleets.mockResolvedValue([sampleFleet]);
    getFleetMembers.mockResolvedValue([]);
    initiateBattle.mockRejectedValue(new Error('Fleets must be in the same sector'));

    await act(async () => {
      root.render(<FleetManagerPanel />);
    });
    await selectFleet(container);

    const input = container.querySelector(
      '[data-testid="fleet-battle-defender"]',
    ) as HTMLInputElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      )?.set;
      setter?.call(input, DEFENDER_FLEET_UUID);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });

    await act(async () => {
      (container.querySelector(
        '[data-testid="fleet-battle-initiate-submit"]',
      ) as HTMLButtonElement).click();
    });

    expect(
      container.querySelector('[data-testid="fleet-manager-error"]')?.textContent,
    ).toMatch(/Fleets must be in the same sector/);
  });

  it('calls simulateBattleRound with the polled battle id (LEG-2278)', async () => {
    getFleets.mockResolvedValue([{ ...sampleFleet, status: 'in_battle' }]);
    getFleetMembers.mockResolvedValue([]);
    getBattles.mockResolvedValue([
      {
        battle_id: 'battle-1',
        attacker_fleet_id: 'fleet-1',
        defender_fleet_id: 'fleet-2',
        battle_ongoing: true,
      },
    ]);
    getBattle.mockResolvedValue({
      battle_id: 'battle-1',
      phase: 'combat',
      rounds_completed: 1,
      is_active: true,
    });

    await act(async () => {
      root.render(<FleetManagerPanel />);
    });
    await selectFleet(container);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });

    await act(async () => {
      (container.querySelector(
        '[data-testid="fleet-battle-simulate"]',
      ) as HTMLButtonElement).click();
    });

    expect(simulateBattleRound).toHaveBeenCalledWith('battle-1');
  });

  it('shows terminal winner when simulate-round reports battle_ongoing false', async () => {
    getFleets.mockResolvedValue([{ ...sampleFleet, status: 'in_battle' }]);
    getFleetMembers.mockResolvedValue([]);
    getBattles.mockResolvedValue([
      {
        battle_id: 'battle-1',
        attacker_fleet_id: 'fleet-1',
        defender_fleet_id: 'fleet-2',
        battle_ongoing: true,
      },
    ]);
    getBattle.mockResolvedValue({
      battle_id: 'battle-1',
      phase: 'combat',
      is_active: true,
    });
    simulateBattleRound.mockResolvedValue({
      battle_id: 'battle-1',
      battle_ongoing: false,
      winner: 'attacker',
    });

    await act(async () => {
      root.render(<FleetManagerPanel />);
    });
    await selectFleet(container);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });

    await act(async () => {
      (container.querySelector(
        '[data-testid="fleet-battle-simulate"]',
      ) as HTMLButtonElement).click();
    });

    expect(container.querySelector('[data-testid="fleet-battle-terminal"]')?.textContent).toMatch(
      /winner attacker/,
    );
  });
});
