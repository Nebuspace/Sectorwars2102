// @vitest-environment jsdom
/**
 * MiningVenue — claim license / laser refit UI (WO-TESTCOV-PLAYER-MINING-LICENSE).
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
  let upgradeMiningLaser: () => void;

  const renderVenue = (overrides: Record<string, unknown> = {}) => {
    act(() => {
      root.render(
        <MiningVenue
          shipId="ship-1"
          licenseBusy={false}
          licenseError={null}
          licenseSuccess={null}
          purchaseClaimLicense={purchaseClaimLicense}
          laserBusy={false}
          laserError={null}
          laserSuccess={null}
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

  it('Upgrade Mining Laser calls upgradeMiningLaser', async () => {
    renderVenue();
    const btn = Array.from(container.querySelectorAll('button.service-btn')).find((b) =>
      b.textContent?.includes('Upgrade Mining Laser'),
    ) as HTMLButtonElement;
    await act(async () => {
      btn.click();
    });
    expect(upgradeMiningLaser).toHaveBeenCalled();
  });

  it('disables both actions when no ship is present', () => {
    renderVenue({ shipId: undefined });
    const buttons = Array.from(container.querySelectorAll('button.service-btn')) as HTMLButtonElement[];
    expect(buttons.every((b) => b.disabled)).toBe(true);
    expect(buttons[0].title).toBe('No active ship');
  });
});
