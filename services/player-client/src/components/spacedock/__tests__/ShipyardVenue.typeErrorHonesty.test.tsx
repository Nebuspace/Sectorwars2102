// @vitest-environment jsdom
/**
 * LEG-3772 Soft-ORDER — ShipyardVenue catalog load TypeError densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ShipyardVenue, {
  formatShipyardCatalogError,
  SHIPYARD_CATALOG_LOAD_FALLBACK,
} from '../ShipyardVenue';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../../ships', () => ({
  ModuleGridInterface: () => null,
}));

const FALLBACK = SHIPYARD_CATALOG_LOAD_FALLBACK;

describe('formatShipyardCatalogError TypeError densify (LEG-3772)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatShipyardCatalogError(new TypeError('Failed to fetch'), FALLBACK);
    expect(text).toBe(FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch non-TypeError', () => {
    expect(formatShipyardCatalogError(new Error('Network Error'), FALLBACK)).toBe(FALLBACK);
    expect(formatShipyardCatalogError(new Error('Failed to fetch'), FALLBACK)).toBe(FALLBACK);
    expect(formatShipyardCatalogError(new Error('Network Error'), FALLBACK)).not.toMatch(/Network Error/i);
  });

  it('preserves server detail for non-TypeError errors', () => {
    expect(formatShipyardCatalogError(new Error('Shipyard offline for maintenance.'), FALLBACK)).toBe(
      'Shipyard offline for maintenance.',
    );
  });
});

describe('ShipyardVenue catalog load TypeError densify (LEG-3772)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let fetchShipCatalog: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchShipCatalog = vi.fn();
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

  const renderVenue = (shipCatalogError: string) => {
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
          shipCatalog={null}
          shipCatalogError={shipCatalogError}
          fetchShipCatalog={fetchShipCatalog}
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
  };

  it('catalog Failed to fetch surfaces fallback without raw transport text', () => {
    renderVenue('Failed to fetch');
    const errEl = container.querySelector('.genesis-error-message');
    expect(errEl?.textContent).toContain(FALLBACK);
    expect(errEl?.textContent).not.toMatch(/Failed to fetch/i);
    expect(errEl?.textContent).not.toMatch(/TypeError/i);
  });

  it('catalog Network Error surfaces fallback and Retry calls fetchShipCatalog', async () => {
    renderVenue('Network Error');
    const errEl = container.querySelector('.genesis-error-message');
    expect(errEl?.textContent).toContain(FALLBACK);
    expect(errEl?.textContent).not.toMatch(/Network Error/i);

    await act(async () => {
      (container.querySelector('button.action-button') as HTMLButtonElement).click();
    });
    expect(fetchShipCatalog).toHaveBeenCalled();
  });
});
