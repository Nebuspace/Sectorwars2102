// @vitest-environment jsdom
/**
 * StealthModuleInstallCta — LEG-126.
 *
 * Covered:
 *  1. Install posts /equipment/install with equipment_key=stealth_module.
 *  2. CTA hidden when already fitted.
 *  3. CTA absent for incompatible hulls.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const getUpgradesMock = vi.fn();
const installEquipmentMock = vi.fn();

vi.mock('../../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api')>();
  return {
    ...actual,
    shipUpgradeAPI: {
      ...actual.shipUpgradeAPI,
      getUpgrades: (...args: unknown[]) => getUpgradesMock(...args),
      installEquipment: (...args: unknown[]) => installEquipmentMock(...args),
    },
  };
});

import StealthModuleInstallCta, {
  STEALTH_MODULE_INSTALL_COST_CR,
  isStealthModuleHullCompatible,
} from '../StealthModuleInstallCta';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('isStealthModuleHullCompatible', () => {
  it('accepts Scout / Fast Courier / Warp Jumper (canon)', () => {
    expect(isStealthModuleHullCompatible('SCOUT_SHIP')).toBe(true);
    expect(isStealthModuleHullCompatible('SCOUT')).toBe(true);
    expect(isStealthModuleHullCompatible('FAST_COURIER')).toBe(true);
    expect(isStealthModuleHullCompatible('WARP_JUMPER')).toBe(true);
  });

  it('rejects incompatible hulls', () => {
    expect(isStealthModuleHullCompatible('DEFENDER')).toBe(false);
    expect(isStealthModuleHullCompatible('CARRIER')).toBe(false);
    expect(isStealthModuleHullCompatible(null)).toBe(false);
  });
});

describe('StealthModuleInstallCta — LEG-126', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    getUpgradesMock.mockReset();
    installEquipmentMock.mockReset();
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

  const mount = async (props: {
    shipId?: string | null;
    shipType?: string | null;
    onInstalled?: (r: { remainingCredits?: number }) => void;
  }) => {
    await act(async () => {
      root.render(
        <StealthModuleInstallCta
          shipId={props.shipId ?? 'ship-1'}
          shipType={props.shipType ?? 'SCOUT_SHIP'}
          onInstalled={props.onInstalled}
        />,
      );
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  it('posts equipment_key=stealth_module when Install is clicked', async () => {
    getUpgradesMock.mockResolvedValue({
      success: true,
      equipment: {
        stealth_module: { installed: false, cost: STEALTH_MODULE_INSTALL_COST_CR },
      },
      equipped: {},
    });
    installEquipmentMock.mockResolvedValue({
      success: true,
      message: 'Stealth Module installed',
      cost_paid: 40_000,
      remaining_credits: 60_000,
    });
    const onInstalled = vi.fn();

    await mount({ onInstalled });

    const btn = container.querySelector(
      '[data-testid="stealth-module-install-btn"]',
    ) as HTMLButtonElement | null;
    expect(btn).toBeTruthy();
    expect(btn!.textContent).toContain('INSTALL STEALTH MODULE');
    expect(btn!.textContent).toContain('40,000');

    await act(async () => {
      btn!.click();
      await Promise.resolve();
    });

    expect(installEquipmentMock).toHaveBeenCalledWith('ship-1', 'stealth_module');
    expect(onInstalled).toHaveBeenCalledWith({
      remainingCredits: 60_000,
      message: 'Stealth Module installed',
    });
  });

  it('hides Install CTA when stealth_module is already fitted', async () => {
    getUpgradesMock.mockResolvedValue({
      success: true,
      equipment: {
        stealth_module: { installed: true, cost: 40_000 },
      },
      equipped: {
        stealth_module: {
          installed_at: '2026-01-01T00:00:00Z',
          effects: { stealth_evasion_bonus: 15 },
        },
      },
    });

    await mount({});

    expect(
      container.querySelector('[data-testid="stealth-module-install-btn"]'),
    ).toBeNull();
    expect(
      container.querySelector('[data-testid="stealth-module-cta-fitted"]'),
    ).toBeTruthy();
    expect(installEquipmentMock).not.toHaveBeenCalled();
  });

  it('renders nothing for an incompatible hull', async () => {
    getUpgradesMock.mockResolvedValue({ success: true, equipment: {}, equipped: {} });

    await mount({ shipType: 'DEFENDER' });

    expect(container.querySelector('[data-testid="stealth-module-cta"]')).toBeNull();
    expect(
      container.querySelector('[data-testid="stealth-module-install-btn"]'),
    ).toBeNull();
    expect(getUpgradesMock).not.toHaveBeenCalled();
  });
});
