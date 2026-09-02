// @vitest-environment jsdom
/**
 * LEG-3771 Soft-ORDER — ArmoryVenue catalog load TypeError densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ArmoryVenue, {
  ARMORY_CATALOG_LOAD_FALLBACK,
  formatArmoryCatalogError,
  type ArmoryCatalogItem,
  type ArmoryLoadout,
} from '../ArmoryVenue';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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

const FALLBACK = ARMORY_CATALOG_LOAD_FALLBACK;

describe('formatArmoryCatalogError TypeError densify (LEG-3771)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatArmoryCatalogError(new TypeError('Failed to fetch'), FALLBACK);
    expect(text).toBe(FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch non-TypeError', () => {
    expect(formatArmoryCatalogError(new Error('Network Error'), FALLBACK)).toBe(FALLBACK);
    expect(formatArmoryCatalogError(new Error('Failed to fetch'), FALLBACK)).toBe(FALLBACK);
    expect(formatArmoryCatalogError(new Error('Network Error'), FALLBACK)).not.toMatch(/Network Error/i);
  });

  it('preserves server detail for non-TypeError errors', () => {
    expect(formatArmoryCatalogError(new Error('Armory offline for maintenance.'), FALLBACK)).toBe(
      'Armory offline for maintenance.',
    );
    expect(formatArmoryCatalogError('Failed to load armory catalog', FALLBACK)).toBe(
      'Failed to load armory catalog',
    );
  });
});

describe('ArmoryVenue catalog load TypeError densify (LEG-3771)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let fetchArmoryCatalog: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchArmoryCatalog = vi.fn();
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

  const renderVenue = (armoryCatalogError: string) => {
    act(() => {
      root.render(
        <ArmoryVenue
          armoryCatalog={null}
          armoryLoading={false}
          armoryCatalogError={armoryCatalogError}
          fetchArmoryCatalog={fetchArmoryCatalog}
          armoryLoadout={LOADOUT}
          armoryQuantities={{}}
          setArmoryQuantities={vi.fn()}
          armoryBuying={null}
          armoryError={null}
          armorySuccess={null}
          purchaseArmoryItem={vi.fn()}
          displayCredits={5000}
          stationServices={{ drone_shop: true }}
          stationIsSpacedock={false}
          playerAttackDrones={0}
          playerDefenseDrones={0}
          onBack={vi.fn()}
          blackMarketButton={null}
        />,
      );
    });
  };

  it('catalog Failed to fetch surfaces fallback without raw transport text', () => {
    renderVenue('Failed to fetch');
    const errEl = container.querySelector('.genesis-error-message');
    expect(errEl?.textContent).toContain(FALLBACK);
    expect(errEl?.textContent).not.toMatch(/Failed to fetch/i);
    expect(errEl?.textContent).not.toMatch(/TypeError/i);
  });

  it('catalog Network Error surfaces fallback and Retry calls fetchArmoryCatalog', async () => {
    renderVenue('Network Error');
    const errEl = container.querySelector('.genesis-error-message');
    expect(errEl?.textContent).toContain(FALLBACK);
    expect(errEl?.textContent).not.toMatch(/Network Error/i);

    await act(async () => {
      (container.querySelector('button.action-button') as HTMLButtonElement).click();
    });
    expect(fetchArmoryCatalog).toHaveBeenCalled();
  });
});
