// @vitest-environment jsdom
/**
 * FleetManagerPanel — LEG-INI-01 + LEG-61
 * Pins roster load, create, member composition, and move-as-one call paths.
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
  move,
} = vi.hoisted(() => ({
  getFleets: vi.fn(),
  createFleet: vi.fn(),
  getFleetMembers: vi.fn(),
  addShipToFleet: vi.fn(),
  removeShipFromFleet: vi.fn(),
  updateFormation: vi.fn(),
  disbandFleet: vi.fn(),
  resupplyFleet: vi.fn(),
  move: vi.fn(),
}));

vi.mock('../../../services/api', () => ({
  fleetAPI: {
    getFleets: (...a: unknown[]) => getFleets(...a),
    getMyFleets: vi.fn(),
    createFleet: (...a: unknown[]) => createFleet(...a),
    getFleetMembers: (...a: unknown[]) => getFleetMembers(...a),
    addShipToFleet: (...a: unknown[]) => addShipToFleet(...a),
    removeShipFromFleet: vi.fn(),
    updateFormation: vi.fn(),
    disbandFleet: vi.fn(),
    resupplyFleet: vi.fn(),
    move: (...a: unknown[]) => move(...a),
  },
}));

const CURRENT_SECTOR_UUID = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    ships: [
      { id: 'ship-1', name: 'Arrow', type: 'SCOUT' },
      { id: 'ship-2', name: 'Hammer', type: 'DESTROYER' },
    ],
    currentSector: {
      id: CURRENT_SECTOR_UUID,
      sector_id: 42,
      sector_number: 42,
      name: 'Home Dock',
    },
    availableMoves: { warps: [], tunnels: [] },
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

describe('FleetManagerPanel', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    getFleets.mockReset().mockResolvedValue([]);
    createFleet.mockReset().mockResolvedValue(sampleFleet);
    getFleetMembers.mockReset().mockResolvedValue([]);
    addShipToFleet.mockReset().mockResolvedValue({});
    move.mockReset().mockResolvedValue({ message: 'Fleet moved' });
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

  it('moves selected fleet via fleetAPI.move to current sector UUID', async () => {
    getFleets.mockResolvedValue([sampleFleet]);
    getFleetMembers.mockResolvedValue([]);

    await act(async () => {
      root.render(<FleetManagerPanel />);
    });

    await act(async () => {
      (container.querySelector(
        '[data-testid="fleet-select-fleet-1"]'
      ) as HTMLButtonElement).click();
    });

    expect(container.querySelector('[data-testid="fleet-move-unavailable"]')).toBeNull();
    expect(container.querySelector('[data-testid="fleet-move-controls"]')).toBeTruthy();

    const submit = container.querySelector(
      '[data-testid="fleet-move-submit"]'
    ) as HTMLButtonElement;
    expect(submit.disabled).toBe(false);

    await act(async () => {
      submit.click();
    });

    expect(move).toHaveBeenCalledWith('fleet-1', CURRENT_SECTOR_UUID);
  });

  it('disables move while fleet is in_battle and surfaces the reason', async () => {
    getFleets.mockResolvedValue([{ ...sampleFleet, status: 'in_battle' }]);
    getFleetMembers.mockResolvedValue([]);

    await act(async () => {
      root.render(<FleetManagerPanel />);
    });

    await act(async () => {
      (container.querySelector(
        '[data-testid="fleet-select-fleet-1"]'
      ) as HTMLButtonElement).click();
    });

    const submit = container.querySelector(
      '[data-testid="fleet-move-submit"]'
    ) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    expect(submit.textContent).toMatch(/In battle/i);
    expect(
      container.querySelector('[data-testid="fleet-move-in-battle"]')?.textContent
    ).toMatch(/Cannot move a fleet during battle/i);
  });

  it('surfaces move API errors in the panel alert', async () => {
    getFleets.mockResolvedValue([sampleFleet]);
    getFleetMembers.mockResolvedValue([]);
    move.mockRejectedValue(new Error('Cannot move fleet during battle'));

    await act(async () => {
      root.render(<FleetManagerPanel />);
    });

    await act(async () => {
      (container.querySelector(
        '[data-testid="fleet-select-fleet-1"]'
      ) as HTMLButtonElement).click();
    });

    await act(async () => {
      (container.querySelector(
        '[data-testid="fleet-move-submit"]'
      ) as HTMLButtonElement).click();
    });

    expect(
      container.querySelector('[data-testid="fleet-manager-error"]')?.textContent
    ).toMatch(/Cannot move fleet during battle/);
  });
});
