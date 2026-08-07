// @vitest-environment jsdom
/**
 * ShipyardVenue — Scroll Law DOM order (catalog Purchase before module bay).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../../ships', () => ({
  ModuleGridInterface: () => <div data-testid="module-grid">modules</div>,
}));

import ShipyardVenue from '../ShipyardVenue';

describe('ShipyardVenue — Scroll Law (catalog before customization)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
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

  it('mounts Ship Catalog section before Ship Customization', async () => {
    await act(async () => {
      root.render(
        <ShipyardVenue
          shipId="ship-1"
          shipType="SCOUT_SHIP"
          tradedockTier={null}
          displayCredits={50_000}
          refreshPlayerState={vi.fn()}
          fetchShipData={vi.fn()}
          shipPurchaseSuccess={null}
          shipPurchaseError={null}
          shipCatalogLoading={false}
          shipCatalog={[
            {
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
              description: 'test',
            },
          ]}
          shipCatalogError={null}
          fetchShipCatalog={vi.fn()}
          confirmShip={null}
          setConfirmShip={vi.fn()}
          newShipName=""
          setNewShipName={vi.fn()}
          shipPurchasing={false}
          setShipPurchaseError={vi.fn()}
          setShipPurchaseSuccess={vi.fn()}
          purchaseShip={vi.fn()}
          onBack={vi.fn()}
          onOpenConstruction={vi.fn()}
        />,
      );
    });

    const sections = Array.from(container.querySelectorAll('.shipyard-section'));
    const headings = sections.map((s) => s.querySelector('h3')?.textContent || '');
    const catalogIdx = headings.findIndex((h) => h.includes('Ship Catalog'));
    const customIdx = headings.findIndex((h) => h.includes('Ship Customization'));
    expect(catalogIdx).toBeGreaterThanOrEqual(0);
    expect(customIdx).toBeGreaterThanOrEqual(0);
    expect(catalogIdx).toBeLessThan(customIdx);
    expect(container.querySelector('.buy-ship-btn')).not.toBeNull();
  });
});
