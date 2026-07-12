// @vitest-environment jsdom
/**
 * ShipSelector — FLEET location-gate (WO-UI5-DOSSIER sub-part #3).
 *
 * The "Make Active Ship" button previously had no docked/location check
 * (only `!selectedShipId || selectedShipId === currentShip?.id ||
 * isChangingShip`) -- a player could select an out-of-sector or (while
 * landed) any ship and the click would round-trip to a guaranteed 400.
 * This mirrors the server's OWN gate on POST /api/v1/ships/{id}/set-active
 * (ship_upgrades.py `set_active_ship`): the target ship's sector_id must
 * equal the player's current_sector_id, and the player must not be landed.
 * Mirrors RankDisplay.test.tsx's seam (jsdom + react-dom/client createRoot
 * + act(), no RTL in this project).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const SAME_SECTOR_SHIP = {
  id: 'ship-1',
  name: 'Home Cruiser',
  type: 'CARGO_HAULER',
  sector_id: 42,
  cargo: {},
  cargo_capacity: 100,
  current_speed: 5,
  base_speed: 5,
  combat: {},
  maintenance: {},
  is_flagship: true,
  purchase_value: 1000,
  current_value: 1000,
  genesis_devices: 0,
  max_genesis_devices: 0,
};

const OTHER_SECTOR_SHIP = {
  ...SAME_SECTOR_SHIP,
  id: 'ship-2',
  name: 'Away Cruiser',
  sector_id: 99,
  is_flagship: false,
};

let mockPlayerState: { id: string; current_sector_id: number; is_landed: boolean } = {
  id: 'player-1',
  current_sector_id: 42,
  is_landed: false,
};
let mockCurrentShip: typeof SAME_SECTOR_SHIP | null = null;
const mockSetCurrentShip = vi.fn().mockResolvedValue(undefined);

vi.mock('../../contexts/GameContext', () => ({
  useGame: () => ({
    ships: [SAME_SECTOR_SHIP, OTHER_SECTOR_SHIP],
    currentShip: mockCurrentShip,
    setCurrentShip: mockSetCurrentShip,
    playerState: mockPlayerState,
  }),
}));

import { ShipSelector } from './ShipSelector';

describe('ShipSelector — FLEET location-gate', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockSetCurrentShip.mockClear();
    mockPlayerState = { id: 'player-1', current_sector_id: 42, is_landed: false };
    mockCurrentShip = null;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  const mount = async () => {
    await act(async () => {
      root.render(<ShipSelector />);
    });
  };

  const click = async (el: Element) => {
    await act(async () => {
      el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
  };

  it('enables the switch button for a ship in the player\'s current sector', async () => {
    await mount();
    const cards = container.querySelectorAll('.ship-card');
    const homeCard = Array.from(cards).find((c) => c.textContent?.includes('Home Cruiser'))!;
    await click(homeCard);

    const btn = container.querySelector('.selector-actions button.primary') as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
  });

  it('disables the switch button for an out-of-sector ship and explains why', async () => {
    await mount();
    const cards = container.querySelectorAll('.ship-card');
    const awayCard = Array.from(cards).find((c) => c.textContent?.includes('Away Cruiser'))!;
    await click(awayCard);

    const btn = container.querySelector('.selector-actions button.primary') as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.title).toContain('sector 99');
    expect(btn.title).toContain('travel there to board it');
    // Pixel a11y fix: `title` alone isn't reliably announced by screen
    // readers -- the reason must also be in the accessible name.
    expect(btn.getAttribute('aria-label')).toContain('Make Active Ship');
    expect(btn.getAttribute('aria-label')).toContain('sector 99');
    expect(btn.getAttribute('aria-label')).toContain('travel there to board it');

    // Defense in depth: even a forced click must not call the API.
    await click(btn);
    expect(mockSetCurrentShip).not.toHaveBeenCalled();
  });

  it('disables the switch button entirely while the player is landed, even for a same-sector ship', async () => {
    mockPlayerState = { id: 'player-1', current_sector_id: 42, is_landed: true };
    await mount();
    const cards = container.querySelectorAll('.ship-card');
    const homeCard = Array.from(cards).find((c) => c.textContent?.includes('Home Cruiser'))!;
    await click(homeCard);

    const btn = container.querySelector('.selector-actions button.primary') as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.title).toBe('Lift off before switching ships');
    expect(btn.getAttribute('aria-label')).toBe('Make Active Ship – Lift off before switching ships');

    await click(btn);
    expect(mockSetCurrentShip).not.toHaveBeenCalled();
  });

  it('carries no aria-label override when the button is enabled (accessible name stays "Make Active Ship")', async () => {
    await mount();
    const cards = container.querySelectorAll('.ship-card');
    const homeCard = Array.from(cards).find((c) => c.textContent?.includes('Home Cruiser'))!;
    await click(homeCard);

    const btn = container.querySelector('.selector-actions button.primary') as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
    expect(btn.getAttribute('aria-label')).toBeNull();
  });

  it('calls setCurrentShip when the gate is clear', async () => {
    await mount();
    const cards = container.querySelectorAll('.ship-card');
    const homeCard = Array.from(cards).find((c) => c.textContent?.includes('Home Cruiser'))!;
    await click(homeCard);

    const btn = container.querySelector('.selector-actions button.primary') as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
    await click(btn);
    expect(mockSetCurrentShip).toHaveBeenCalledWith('ship-1');
  });
});
