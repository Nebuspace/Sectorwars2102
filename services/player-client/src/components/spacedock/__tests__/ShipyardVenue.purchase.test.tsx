// @vitest-environment jsdom
/**
 * ShipyardVenue — purchase confirm money path (WO-TESTCOV-PLAYER-SHIPYARD-PURCHASE).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../../ships', () => ({
  ModuleGridInterface: () => <div data-testid="module-grid">modules</div>,
}));

import ShipyardVenue from '../ShipyardVenue';
import type { ShipCatalogEntry } from '../ShipyardVenue';

const SCOUT: ShipCatalogEntry = {
  type: 'SCOUT_SHIP',
  name: 'Scout',
  base_cost: 1000,
  purchasable: true,
  speed: 10,
  turn_cost: 1,
  max_cargo: 10,
  max_colonists: 0,
  max_drones: 2,
  max_shields: 5,
  hull_points: 50,
  attack_rating: 1,
  defense_rating: 1,
  max_genesis_devices: 0,
  description: 'test scout',
};

describe('ShipyardVenue — purchase confirm', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let purchaseShip: (entry: ShipCatalogEntry, requestedName: string) => void;
  let setConfirmShip: React.Dispatch<React.SetStateAction<ShipCatalogEntry | null>>;
  let setNewShipName: React.Dispatch<React.SetStateAction<string>>;
  let confirmShip: ShipCatalogEntry | null;
  let newShipName: string;

  const renderVenue = () => {
    act(() => {
      root.render(
        <ShipyardVenue
          shipId="ship-1"
          shipType="FREIGHTER"
          tradedockTier={null}
          displayCredits={50_000}
          refreshPlayerState={vi.fn()}
          fetchShipData={vi.fn()}
          shipPurchaseSuccess={null}
          shipPurchaseError={null}
          shipCatalogLoading={false}
          shipCatalog={[SCOUT]}
          shipCatalogError={null}
          fetchShipCatalog={vi.fn()}
          confirmShip={confirmShip}
          setConfirmShip={setConfirmShip}
          newShipName={newShipName}
          setNewShipName={setNewShipName}
          shipPurchasing={false}
          setShipPurchaseError={vi.fn()}
          setShipPurchaseSuccess={vi.fn()}
          purchaseShip={purchaseShip}
          onBack={vi.fn<() => void>()}
          onOpenConstruction={vi.fn<() => void>()}
        />,
      );
    });
  };

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    purchaseShip = vi.fn<(entry: ShipCatalogEntry, requestedName: string) => void>();
    confirmShip = null;
    newShipName = '';
    setConfirmShip = vi.fn((next) => {
      confirmShip = typeof next === 'function' ? next(confirmShip) : next;
      renderVenue();
    }) as React.Dispatch<React.SetStateAction<ShipCatalogEntry | null>>;
    setNewShipName = vi.fn((next) => {
      newShipName = typeof next === 'function' ? next(newShipName) : next;
      renderVenue();
    }) as React.Dispatch<React.SetStateAction<string>>;
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it('Purchase opens confirm; Confirm Purchase calls purchaseShip', async () => {
    renderVenue();
    await act(async () => {
      (container.querySelector('.buy-ship-btn') as HTMLButtonElement).click();
    });

    expect(container.querySelector('.ship-confirm-panel')).toBeTruthy();
    expect(container.textContent).toContain('Confirm Purchase — Scout');

    await act(async () => {
      (container.querySelector('.action-button.primary') as HTMLButtonElement).click();
    });

    expect(purchaseShip).toHaveBeenCalledWith(SCOUT, '');
  });

  it('disables Purchase when underfunded', () => {
    confirmShip = null;
    act(() => {
      root.render(
        <ShipyardVenue
          shipId="ship-1"
          shipType="FREIGHTER"
          tradedockTier={null}
          displayCredits={100}
          refreshPlayerState={vi.fn()}
          fetchShipData={vi.fn()}
          shipPurchaseSuccess={null}
          shipPurchaseError={null}
          shipCatalogLoading={false}
          shipCatalog={[SCOUT]}
          shipCatalogError={null}
          fetchShipCatalog={vi.fn()}
          confirmShip={null}
          setConfirmShip={vi.fn()}
          newShipName=""
          setNewShipName={vi.fn()}
          shipPurchasing={false}
          setShipPurchaseError={vi.fn()}
          setShipPurchaseSuccess={vi.fn()}
          purchaseShip={purchaseShip}
          onBack={vi.fn<() => void>()}
          onOpenConstruction={vi.fn<() => void>()}
        />,
      );
    });
    const btn = container.querySelector('.buy-ship-btn') as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.title).toBe('Insufficient credits');
  });
});
