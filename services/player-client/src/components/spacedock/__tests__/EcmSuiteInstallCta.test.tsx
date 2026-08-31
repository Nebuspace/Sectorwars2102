// @vitest-environment jsdom
/**
 * EcmSuiteInstallCta — LEG-126.
 *
 * Covered:
 *  1. Install posts /equipment/install with equipment_key=ecm_suite.
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

import EcmSuiteInstallCta, {
  ECM_SUITE_INSTALL_COST_CR,
  formatEcmSuiteInstallError,
  isEcmSuiteHullCompatible,
} from '../EcmSuiteInstallCta';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('isEcmSuiteHullCompatible', () => {
  it('accepts Scout / Defender / Carrier / Warp Jumper (canon)', () => {
    expect(isEcmSuiteHullCompatible('SCOUT_SHIP')).toBe(true);
    expect(isEcmSuiteHullCompatible('SCOUT')).toBe(true);
    expect(isEcmSuiteHullCompatible('DEFENDER')).toBe(true);
    expect(isEcmSuiteHullCompatible('CARRIER')).toBe(true);
    expect(isEcmSuiteHullCompatible('WARP_JUMPER')).toBe(true);
  });

  it('rejects incompatible hulls', () => {
    expect(isEcmSuiteHullCompatible('CARGO_HAULER')).toBe(false);
    expect(isEcmSuiteHullCompatible('FAST_COURIER')).toBe(false);
    expect(isEcmSuiteHullCompatible(null)).toBe(false);
  });
});

describe('formatEcmSuiteInstallError TypeError densify (LEG-3098)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatEcmSuiteInstallError(new TypeError('Failed to fetch'));
    expect(text).toBe('ECM Suite install failed');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves server detail for non-TypeError errors', () => {
    const err = Object.assign(new Error('insufficient credits'), {
      response: { data: { detail: 'Not enough credits for ECM Suite.' } },
    });
    expect(formatEcmSuiteInstallError(err)).toBe(
      'Not enough credits for ECM Suite.',
    );
  });
});

describe('EcmSuiteInstallCta — LEG-126', () => {
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
        <EcmSuiteInstallCta
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

  it('posts equipment_key=ecm_suite when Install is clicked', async () => {
    getUpgradesMock.mockResolvedValue({
      success: true,
      equipment: {
        ecm_suite: { installed: false, cost: ECM_SUITE_INSTALL_COST_CR },
      },
      equipped: {},
    });
    installEquipmentMock.mockResolvedValue({
      success: true,
      message: 'ECM Suite installed',
      cost_paid: 45_000,
      remaining_credits: 55_000,
    });
    const onInstalled = vi.fn();

    await mount({ onInstalled });

    const btn = container.querySelector(
      '[data-testid="ecm-suite-install-btn"]',
    ) as HTMLButtonElement | null;
    expect(btn).toBeTruthy();
    expect(btn!.textContent).toContain('INSTALL ECM SUITE');
    expect(btn!.textContent).toContain('45,000');

    await act(async () => {
      btn!.click();
      await Promise.resolve();
    });

    expect(installEquipmentMock).toHaveBeenCalledWith('ship-1', 'ecm_suite');
    expect(onInstalled).toHaveBeenCalledWith({
      remainingCredits: 55_000,
      message: 'ECM Suite installed',
    });
  });

  it('hides Install CTA when ecm_suite is already fitted', async () => {
    getUpgradesMock.mockResolvedValue({
      success: true,
      equipment: {
        ecm_suite: { installed: true, cost: 45_000 },
      },
      equipped: {
        ecm_suite: {
          installed_at: '2026-01-01T00:00:00Z',
          effects: { ecm_hit_penalty: 0.15 },
        },
      },
    });

    await mount({});

    expect(container.querySelector('[data-testid="ecm-suite-install-btn"]')).toBeNull();
    expect(container.querySelector('[data-testid="ecm-suite-cta-fitted"]')).toBeTruthy();
    expect(installEquipmentMock).not.toHaveBeenCalled();
  });

  it('renders nothing for an incompatible hull', async () => {
    getUpgradesMock.mockResolvedValue({ success: true, equipment: {}, equipped: {} });

    await mount({ shipType: 'CARGO_HAULER' });

    expect(container.querySelector('[data-testid="ecm-suite-cta"]')).toBeNull();
    expect(container.querySelector('[data-testid="ecm-suite-install-btn"]')).toBeNull();
    expect(getUpgradesMock).not.toHaveBeenCalled();
  });

  it('install TypeError surfaces fallback without Failed to fetch / TypeError (LEG-3098)', async () => {
    getUpgradesMock.mockResolvedValue({
      success: true,
      equipment: {
        ecm_suite: { installed: false, cost: ECM_SUITE_INSTALL_COST_CR },
      },
      equipped: {},
    });
    installEquipmentMock.mockRejectedValue(new TypeError('Failed to fetch'));

    await mount({});

    const btn = container.querySelector(
      '[data-testid="ecm-suite-install-btn"]',
    ) as HTMLButtonElement | null;
    expect(btn).toBeTruthy();

    await act(async () => {
      btn!.click();
      await Promise.resolve();
    });

    const alert = container.querySelector('.tactical-equip-cta-err');
    expect(alert).toBeTruthy();
    expect(alert?.textContent).toBe('ECM Suite install failed');
    expect(alert?.textContent).not.toMatch(/Failed to fetch/i);
    expect(alert?.textContent).not.toMatch(/TypeError/i);
  });
});
