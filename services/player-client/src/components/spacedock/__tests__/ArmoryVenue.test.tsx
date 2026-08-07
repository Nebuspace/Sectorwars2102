// @vitest-environment jsdom
/**
 * ArmoryVenue — money-path UI coverage (WO-TESTCOV-PLAYER-ARMORY).
 * Catalog cards, capacity/credit gates, Buy → purchaseArmoryItem(qty).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import ArmoryVenue, { type ArmoryCatalogItem, type ArmoryLoadout } from '../ArmoryVenue';

const ATTACK: ArmoryCatalogItem = {
  item: 'attack_drone',
  name: 'Attack Drone',
  price: 500,
  description: 'Offensive drone',
  service: 'drone_shop',
  available: true,
};

const LOADOUT: ArmoryLoadout = {
  attack_drones: 0,
  defense_drones: 0,
  mines: 0,
  caps: { attack_drones: 10, defense_drones: 10, mines: 5 },
};

describe('ArmoryVenue', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let purchaseArmoryItem: (item: ArmoryCatalogItem, quantity: number) => void;
  let onBack: () => void;
  let setArmoryQuantities: React.Dispatch<React.SetStateAction<Record<string, number>>>;

  const renderVenue = (overrides: Record<string, unknown> = {}) => {
    act(() => {
      root.render(
        <ArmoryVenue
          armoryCatalog={[ATTACK]}
          armoryLoading={false}
          armoryCatalogError={null}
          fetchArmoryCatalog={vi.fn()}
          armoryLoadout={LOADOUT}
          armoryQuantities={{}}
          setArmoryQuantities={setArmoryQuantities}
          armoryBuying={null}
          armoryError={null}
          armorySuccess={null}
          purchaseArmoryItem={purchaseArmoryItem}
          displayCredits={5000}
          stationServices={{ drone_shop: true, mine_dealer: true }}
          stationIsSpacedock={false}
          playerAttackDrones={0}
          playerDefenseDrones={0}
          onBack={onBack}
          blackMarketButton={null}
          {...overrides}
        />,
      );
    });
  };

  beforeEach(() => {
    purchaseArmoryItem = vi.fn<(item: ArmoryCatalogItem, quantity: number) => void>();
    onBack = vi.fn<() => void>();
    setArmoryQuantities = vi.fn();
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

  it('renders catalog item and loadout counters', () => {
    renderVenue();
    expect(container.textContent).toContain('Attack Drone');
    expect(container.textContent).toContain('0 / 10');
    expect(container.querySelector('button.buy-btn')?.textContent).toBe('Buy');
  });

  it('Buy calls purchaseArmoryItem with qty 1 by default', async () => {
    renderVenue();
    await act(async () => {
      (container.querySelector('button.buy-btn') as HTMLButtonElement).click();
    });
    expect(purchaseArmoryItem).toHaveBeenCalledWith(ATTACK, 1);
  });

  it('disables Buy with Insufficient credits when underfunded', () => {
    renderVenue({ displayCredits: 100 });
    const buy = container.querySelector('button.buy-btn') as HTMLButtonElement;
    expect(buy.disabled).toBe(true);
    expect(buy.title).toBe('Insufficient credits');
  });

  it('disables Buy at capacity', () => {
    renderVenue({
      armoryLoadout: {
        ...LOADOUT,
        attack_drones: 10,
      },
    });
    const buy = container.querySelector('button.buy-btn') as HTMLButtonElement;
    expect(buy.disabled).toBe(true);
    expect(buy.title).toBe('At capacity');
  });

  it('shows catalog error + Retry', async () => {
    const fetchArmoryCatalog = vi.fn();
    renderVenue({
      armoryCatalog: null,
      armoryCatalogError: 'Failed to load armory catalog',
      fetchArmoryCatalog,
    });
    expect(container.textContent).toContain('Failed to load armory catalog');
    await act(async () => {
      (container.querySelector('button.action-button') as HTMLButtonElement).click();
    });
    expect(fetchArmoryCatalog).toHaveBeenCalled();
  });

  it('Back to Hub invokes onBack', async () => {
    renderVenue();
    await act(async () => {
      (container.querySelector('.back-button') as HTMLButtonElement).click();
    });
    expect(onBack).toHaveBeenCalled();
  });
});
