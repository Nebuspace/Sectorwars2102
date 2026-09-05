// @vitest-environment jsdom
/**
 * LEG-3781 Soft-ORDER — EcmSuiteInstallCta catalog load + install TypeError densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import EcmSuiteInstallCta, {
  ECM_SUITE_INSTALL_COST_CR,
  formatEcmSuiteInstallError,
} from '../EcmSuiteInstallCta';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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

const FALLBACK = 'ECM Suite install failed';

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
}

const catalogReady = {
  success: true,
  equipment: {
    ecm_suite: { installed: false, cost: ECM_SUITE_INSTALL_COST_CR },
  },
  equipped: {},
};

describe('formatEcmSuiteInstallError typeErrorHonesty (LEG-3781)', () => {
  it.each([
    ['TypeError', new TypeError('Failed to fetch')],
    ['Network Error', new Error('Network Error')],
    ['Failed to fetch', new Error('Failed to fetch')],
  ])('collapses %s to player-safe fallback', (_label, err) => {
    const text = formatEcmSuiteInstallError(err);
    expect(text).toBe(FALLBACK);
    assertNoTransportLeak(text);
  });

  it('preserves non-transport server detail', () => {
    const err = Object.assign(new Error('insufficient credits'), {
      response: { data: { detail: 'Not enough credits for ECM Suite.' } },
    });
    expect(formatEcmSuiteInstallError(err)).toBe('Not enough credits for ECM Suite.');
  });
});

describe('EcmSuiteInstallCta typeErrorHonesty densify (LEG-3781)', () => {
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
  });

  const mount = async () => {
    await act(async () => {
      root.render(<EcmSuiteInstallCta shipId="ship-1" shipType="SCOUT_SHIP" />);
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  it.each([
    ['TypeError', new TypeError('Failed to fetch')],
    ['Network Error', new Error('Network Error')],
    ['Failed to fetch', new Error('Failed to fetch')],
  ])('catalog load %s does not leak raw transport text', async (_label, err) => {
    getUpgradesMock.mockRejectedValue(err);

    await mount();

    assertNoTransportLeak(container.textContent ?? '');
    expect(container.querySelector('[data-testid="ecm-suite-cta-checking"]')).toBeTruthy();
  });

  it.each([
    ['TypeError', new TypeError('Failed to fetch')],
    ['Network Error', new Error('Network Error')],
    ['Failed to fetch', new Error('Failed to fetch')],
  ])('install %s surfaces fallback without raw transport text', async (_label, err) => {
    getUpgradesMock.mockResolvedValue(catalogReady);
    installEquipmentMock.mockRejectedValue(err);

    await mount();

    const btn = container.querySelector(
      '[data-testid="ecm-suite-install-btn"]',
    ) as HTMLButtonElement | null;
    expect(btn).toBeTruthy();

    await act(async () => {
      btn!.click();
      await Promise.resolve();
    });

    const alert = container.querySelector('.tactical-equip-cta-err');
    expect(alert?.textContent).toBe(FALLBACK);
    assertNoTransportLeak(alert?.textContent ?? '');
  });
});

describe('formatEcmSuiteInstallError 403/429 densify (LEG-4078)', () => {
  const apiRequestError = (status: number, message?: string) => {
    const err = new Error(message ?? `API Error: ${status}`);
    (err as { status?: number }).status = status;
    return err;
  };
  it('surfaces 403/429 without raw status codes', () => {
    expect(formatEcmSuiteInstallError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatEcmSuiteInstallError(apiRequestError(403, 'ecm_denied'))).toBe('ecm_denied');
    expect(formatEcmSuiteInstallError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatEcmSuiteInstallError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatEcmSuiteInstallError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});
