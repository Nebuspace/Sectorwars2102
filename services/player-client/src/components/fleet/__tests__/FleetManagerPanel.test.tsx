// @vitest-environment jsdom
/**
 * FleetManagerPanel — LEG-INI-01 + LEG-61 + LEG-133 + LEG-141
 * Pins roster load, create, member composition, move-as-one (adjacent hop),
 * and availableMoves refresh on panel mount.
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
const WARP_UUID = '11111111-2222-3333-4444-555555555555';
const TUNNEL_UUID = '66666666-7777-8888-9999-aaaaaaaaaaaa';
const UNAFFORDABLE_UUID = 'bbbbbbbb-cccc-dddd-eeee-ffffffffffff';

const mockGame = vi.hoisted(() => {
  const CURRENT = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
  return {
    CURRENT_SECTOR_UUID: CURRENT,
    ships: [
      { id: 'ship-1', name: 'Arrow', type: 'SCOUT' },
      { id: 'ship-2', name: 'Hammer', type: 'DESTROYER' },
    ],
    currentSector: {
      id: CURRENT,
      sector_id: 42,
      sector_number: 42,
      name: 'Home Dock',
    } as {
      id: string;
      sector_id: number;
      sector_number: number;
      name: string;
    } | null,
    availableMoves: {
      warps: [] as Array<Record<string, unknown>>,
      tunnels: [] as Array<Record<string, unknown>>,
    },
    getAvailableMoves: vi.fn().mockResolvedValue(undefined),
  };
});

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    ships: mockGame.ships,
    currentSector: mockGame.currentSector,
    availableMoves: mockGame.availableMoves,
    getAvailableMoves: (...a: unknown[]) => mockGame.getAvailableMoves(...a),
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
    move.mockReset().mockResolvedValue({ message: 'Fleet moved' });
    mockGame.currentSector = {
      id: CURRENT_SECTOR_UUID,
      sector_id: 42,
      sector_number: 42,
      name: 'Home Dock',
    };
    mockGame.availableMoves = { warps: [], tunnels: [] };
    mockGame.getAvailableMoves.mockReset().mockResolvedValue(undefined);
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

  it('refreshes availableMoves on mount when cache is empty (LEG-141)', async () => {
    mockGame.availableMoves = { warps: [], tunnels: [] };
    await act(async () => {
      root.render(<FleetManagerPanel />);
    });
    expect(mockGame.getAvailableMoves).toHaveBeenCalled();
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

  it('moves selected fleet via fleetAPI.move to current sector UUID when no hops', async () => {
    getFleets.mockResolvedValue([sampleFleet]);
    getFleetMembers.mockResolvedValue([]);

    await act(async () => {
      root.render(<FleetManagerPanel />);
    });
    await selectFleet(container);

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

  it('moves via affordable warp hop UUID (LEG-133)', async () => {
    mockGame.availableMoves = {
      warps: [
        {
          id: WARP_UUID,
          sector_id: 101,
          sector_number: 101,
          name: 'Outpost Alpha',
          type: 'standard',
          turn_cost: 1,
          can_afford: true,
        },
      ],
      tunnels: [],
    };
    getFleets.mockResolvedValue([sampleFleet]);
    getFleetMembers.mockResolvedValue([]);

    await act(async () => {
      root.render(<FleetManagerPanel />);
    });
    await selectFleet(container);

    const dest = container.querySelector(
      '[data-testid="fleet-move-dest"]'
    ) as HTMLSelectElement;
    expect(dest.querySelector(`[value="${WARP_UUID}"]`)).toBeTruthy();
    expect(dest.value).toBe(WARP_UUID);

    await act(async () => {
      (container.querySelector(
        '[data-testid="fleet-move-submit"]'
      ) as HTMLButtonElement).click();
    });

    expect(move).toHaveBeenCalledWith('fleet-1', WARP_UUID);
  });

  it('moves via affordable tunnel hop UUID (LEG-133)', async () => {
    mockGame.availableMoves = {
      warps: [],
      tunnels: [
        {
          id: TUNNEL_UUID,
          sector_id: 202,
          sector_number: 202,
          name: 'Gate Beta',
          type: 'tunnel',
          tunnel_type: 'natural',
          turn_cost: 2,
          can_afford: true,
        },
      ],
    };
    getFleets.mockResolvedValue([sampleFleet]);
    getFleetMembers.mockResolvedValue([]);

    await act(async () => {
      root.render(<FleetManagerPanel />);
    });
    await selectFleet(container);

    const dest = container.querySelector(
      '[data-testid="fleet-move-dest"]'
    ) as HTMLSelectElement;
    await setSelectValue(dest, TUNNEL_UUID);
    expect(dest.value).toBe(TUNNEL_UUID);

    await act(async () => {
      (container.querySelector(
        '[data-testid="fleet-move-submit"]'
      ) as HTMLButtonElement).click();
    });

    expect(move).toHaveBeenCalledWith('fleet-1', TUNNEL_UUID);
  });

  it('omits unaffordable and invalid/missing-id hops from the selector', async () => {
    mockGame.availableMoves = {
      warps: [
        {
          id: UNAFFORDABLE_UUID,
          sector_id: 303,
          sector_number: 303,
          name: 'Too Far',
          type: 'standard',
          turn_cost: 99,
          can_afford: false,
        },
        {
          // missing id — must not appear
          sector_id: 304,
          sector_number: 304,
          name: 'No UUID',
          type: 'standard',
          turn_cost: 1,
          can_afford: true,
        },
        {
          id: 'not-a-uuid',
          sector_id: 305,
          sector_number: 305,
          name: 'Bad Id',
          type: 'standard',
          turn_cost: 1,
          can_afford: true,
        },
        {
          id: WARP_UUID,
          sector_id: 101,
          sector_number: 101,
          name: 'Good Hop',
          type: 'standard',
          turn_cost: 1,
          can_afford: true,
        },
      ],
      tunnels: [],
    };
    getFleets.mockResolvedValue([sampleFleet]);
    getFleetMembers.mockResolvedValue([]);

    await act(async () => {
      root.render(<FleetManagerPanel />);
    });
    await selectFleet(container);

    const dest = container.querySelector(
      '[data-testid="fleet-move-dest"]'
    ) as HTMLSelectElement;
    const values = Array.from(dest.options).map((o) => o.value);
    expect(values).toContain(WARP_UUID);
    expect(values).toContain(CURRENT_SECTOR_UUID);
    expect(values).not.toContain(UNAFFORDABLE_UUID);
    expect(values).not.toContain('not-a-uuid');
    expect(values).not.toContain('');
    expect(dest.textContent).not.toMatch(/Too Far|No UUID|Bad Id/);
  });

  it('disables move while fleet is in_battle and surfaces the reason', async () => {
    getFleets.mockResolvedValue([{ ...sampleFleet, status: 'in_battle' }]);
    getFleetMembers.mockResolvedValue([]);

    await act(async () => {
      root.render(<FleetManagerPanel />);
    });
    await selectFleet(container);

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
    await selectFleet(container);

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
