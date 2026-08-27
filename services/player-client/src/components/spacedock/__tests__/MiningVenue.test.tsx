// @vitest-environment jsdom
/**
 * MiningVenue — claim license / laser install+refit UI (LEG-1226 / LEG-109).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import MiningVenue from '../MiningVenue';

describe('MiningVenue', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let purchaseClaimLicense: () => void;
  let installMiningLaser: () => void;
  let upgradeMiningLaser: () => void;

  const renderVenue = (overrides: Record<string, unknown> = {}) => {
    act(() => {
      root.render(
        <MiningVenue
          shipId="ship-1"
          miningLaserLevel={null}
          licenseBusy={false}
          licenseError={null}
          licenseSuccess={null}
          purchaseClaimLicense={purchaseClaimLicense}
          laserBusy={false}
          laserError={null}
          laserSuccess={null}
          installMiningLaser={installMiningLaser}
          upgradeMiningLaser={upgradeMiningLaser}
          onBack={vi.fn<() => void>()}
          blackMarketButton={null}
          {...overrides}
        />,
      );
    });
  };

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    purchaseClaimLicense = vi.fn<() => void>();
    installMiningLaser = vi.fn<() => void>();
    upgradeMiningLaser = vi.fn<() => void>();
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it('Purchase / Renew License calls purchaseClaimLicense when a ship is present', async () => {
    renderVenue();
    const btn = Array.from(container.querySelectorAll('button.service-btn')).find((b) =>
      b.textContent?.includes('Purchase / Renew License'),
    ) as HTMLButtonElement;
    await act(async () => {
      btn.click();
    });
    expect(purchaseClaimLicense).toHaveBeenCalled();
  });

  it('shows Install Mining Laser when no laser is fitted', async () => {
    renderVenue({ miningLaserLevel: null });
    const installBtn = Array.from(container.querySelectorAll('button.service-btn')).find((b) =>
      b.textContent?.includes('Install Mining Laser'),
    ) as HTMLButtonElement;
    const upgradeBtn = Array.from(container.querySelectorAll('button.service-btn')).find((b) =>
      b.textContent?.includes('Upgrade Mining Laser'),
    );
    expect(installBtn).toBeTruthy();
    expect(upgradeBtn).toBeUndefined();
    await act(async () => {
      installBtn.click();
    });
    expect(installMiningLaser).toHaveBeenCalled();
    expect(upgradeMiningLaser).not.toHaveBeenCalled();
  });

  it('shows Upgrade Mining Laser when a laser is installed', async () => {
    renderVenue({ miningLaserLevel: 1 });
    expect(container.textContent).toMatch(/Current level:\s*1/);
    const upgradeBtn = Array.from(container.querySelectorAll('button.service-btn')).find((b) =>
      b.textContent?.includes('Upgrade Mining Laser'),
    ) as HTMLButtonElement;
    const installBtn = Array.from(container.querySelectorAll('button.service-btn')).find((b) =>
      b.textContent?.includes('Install Mining Laser'),
    );
    expect(upgradeBtn).toBeTruthy();
    expect(installBtn).toBeUndefined();
    await act(async () => {
      upgradeBtn.click();
    });
    expect(upgradeMiningLaser).toHaveBeenCalled();
    expect(installMiningLaser).not.toHaveBeenCalled();
  });

  it('disables laser + license actions when no ship is present', () => {
    renderVenue({ shipId: undefined });
    const buttons = Array.from(container.querySelectorAll('button.service-btn')) as HTMLButtonElement[];
    expect(buttons.every((b) => b.disabled)).toBe(true);
    expect(buttons[0].title).toBe('No active ship');
  });
});
