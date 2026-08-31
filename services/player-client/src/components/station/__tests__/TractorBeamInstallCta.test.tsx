// @vitest-environment jsdom
/**
 * TractorBeamInstallCta — LEG-120.
 *
 * Covered:
 *  1. Install posts /equipment/install with equipment_key=tractor_beam.
 *  2. CTA hidden when already fitted (upgrades report installed).
 *  3. CTA absent for incompatible hulls (e.g. SCOUT).
 *
 * Mirrors PlanetaryLanderInstallCta / MiningVenue install-test seam: jsdom +
 * createRoot + act(), no RTL.
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

import TractorBeamInstallCta, {
  TRACTOR_BEAM_INSTALL_COST_CR,
  formatTractorBeamInstallError,
  isTractorBeamHullCompatible,
} from '../TractorBeamInstallCta';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('isTractorBeamHullCompatible', () => {
  it('accepts Cargo Hauler / Defender / Carrier / Warp Jumper (canon)', () => {
    expect(isTractorBeamHullCompatible('CARGO_HAULER')).toBe(true);
    expect(isTractorBeamHullCompatible('DEFENDER')).toBe(true);
    expect(isTractorBeamHullCompatible('CARRIER')).toBe(true);
    expect(isTractorBeamHullCompatible('WARP_JUMPER')).toBe(true);
    expect(isTractorBeamHullCompatible('Cargo Hauler')).toBe(true);
  });

  it('rejects incompatible hulls', () => {
    expect(isTractorBeamHullCompatible('SCOUT')).toBe(false);
    expect(isTractorBeamHullCompatible('COLONY_SHIP')).toBe(false);
    expect(isTractorBeamHullCompatible(null)).toBe(false);
  });
});

describe('formatTractorBeamInstallError TypeError densify (LEG-3099)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatTractorBeamInstallError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Tractor Beam install failed/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves axios detail when not TypeError', () => {
    const err = {
      response: { data: { detail: 'Insufficient credits for tractor beam install.' } },
    };
    expect(formatTractorBeamInstallError(err)).toBe(
      'Insufficient credits for tractor beam install.',
    );
  });
});

describe('TractorBeamInstallCta — LEG-120', () => {
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
        <TractorBeamInstallCta
          shipId={props.shipId ?? 'ship-1'}
          shipType={props.shipType ?? 'CARGO_HAULER'}
          onInstalled={props.onInstalled}
        />,
      );
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  it('posts equipment_key=tractor_beam when Install is clicked', async () => {
    getUpgradesMock.mockResolvedValue({
      success: true,
      equipment: {
        tractor_beam: { installed: false, cost: TRACTOR_BEAM_INSTALL_COST_CR },
      },
      equipped: {},
    });
    installEquipmentMock.mockResolvedValue({
      success: true,
      message: 'Tractor Beam installed',
      cost_paid: 40_000,
      remaining_credits: 60_000,
    });
    const onInstalled = vi.fn();

    await mount({ onInstalled });

    const btn = container.querySelector(
      '[data-testid="tractor-beam-install-btn"]',
    ) as HTMLButtonElement | null;
    expect(btn).toBeTruthy();
    expect(btn!.textContent).toContain('INSTALL TRACTOR BEAM');
    expect(btn!.textContent).toContain('40,000');

    await act(async () => {
      btn!.click();
      await Promise.resolve();
    });

    expect(installEquipmentMock).toHaveBeenCalledWith('ship-1', 'tractor_beam');
    expect(onInstalled).toHaveBeenCalledWith({
      remainingCredits: 60_000,
      message: 'Tractor Beam installed',
    });
  });

  it('hides Install CTA when tractor_beam is already fitted', async () => {
    getUpgradesMock.mockResolvedValue({
      success: true,
      equipment: {
        tractor_beam: { installed: true, cost: 40_000 },
      },
      equipped: {
        tractor_beam: {
          installed_at: '2026-01-01T00:00:00Z',
          effects: { tow_capable: true },
        },
      },
    });

    await mount({});

    expect(container.querySelector('[data-testid="tractor-beam-install-btn"]')).toBeNull();
    expect(container.querySelector('[data-testid="tractor-beam-cta-fitted"]')).toBeTruthy();
    expect(container.textContent).toMatch(/tow-capable/i);
    expect(installEquipmentMock).not.toHaveBeenCalled();
  });

  it('renders nothing for an incompatible hull', async () => {
    getUpgradesMock.mockResolvedValue({ success: true, equipment: {}, equipped: {} });

    await mount({ shipType: 'SCOUT' });

    expect(container.querySelector('[data-testid="tractor-beam-cta"]')).toBeNull();
    expect(container.querySelector('[data-testid="tractor-beam-install-btn"]')).toBeNull();
    expect(getUpgradesMock).not.toHaveBeenCalled();
  });

  it('install TypeError surfaces fallback without Failed to fetch / TypeError (LEG-3099)', async () => {
    getUpgradesMock.mockResolvedValue({
      success: true,
      equipment: {
        tractor_beam: { installed: false, cost: TRACTOR_BEAM_INSTALL_COST_CR },
      },
      equipped: {},
    });
    installEquipmentMock.mockRejectedValue(new TypeError('Failed to fetch'));

    await mount({});

    const btn = container.querySelector(
      '[data-testid="tractor-beam-install-btn"]',
    ) as HTMLButtonElement | null;
    expect(btn).toBeTruthy();

    await act(async () => {
      btn!.click();
      await Promise.resolve();
    });

    const alert = container.querySelector('.tractor-beam-cta-err');
    expect(alert).toBeTruthy();
    expect(alert?.textContent).toMatch(/Tractor Beam install failed/i);
    expect(alert?.textContent).not.toMatch(/Failed to fetch/i);
    expect(alert?.textContent).not.toMatch(/TypeError/i);
  });
});
