// @vitest-environment jsdom
/**
 * PlanetaryLanderInstallCta — LEG-117.
 *
 * Covered:
 *  1. Install posts /equipment/install with equipment_key=planetary_lander.
 *  2. CTA hidden when already fitted (upgrades report installed).
 *  3. CTA absent for incompatible hulls (e.g. SCOUT).
 *
 * Mirrors QuantumDriveConsole / MiningVenue install-test seam: jsdom +
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

import PlanetaryLanderInstallCta, {
  PLANETARY_LANDER_INSTALL_COST_CR,
  isPlanetaryLanderHullCompatible,
} from '../PlanetaryLanderInstallCta';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('isPlanetaryLanderHullCompatible', () => {
  it('accepts Colony Ship / Light Freighter / Cargo Hauler (canon)', () => {
    expect(isPlanetaryLanderHullCompatible('COLONY_SHIP')).toBe(true);
    expect(isPlanetaryLanderHullCompatible('LIGHT_FREIGHTER')).toBe(true);
    expect(isPlanetaryLanderHullCompatible('CARGO_HAULER')).toBe(true);
    expect(isPlanetaryLanderHullCompatible('Colony Ship')).toBe(true);
  });

  it('rejects incompatible hulls', () => {
    expect(isPlanetaryLanderHullCompatible('SCOUT')).toBe(false);
    expect(isPlanetaryLanderHullCompatible('WARP_JUMPER')).toBe(false);
    expect(isPlanetaryLanderHullCompatible(null)).toBe(false);
  });
});

describe('PlanetaryLanderInstallCta — LEG-117', () => {
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
        <PlanetaryLanderInstallCta
          shipId={props.shipId ?? 'ship-1'}
          shipType={props.shipType ?? 'COLONY_SHIP'}
          onInstalled={props.onInstalled}
        />,
      );
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  it('posts equipment_key=planetary_lander when Install is clicked', async () => {
    getUpgradesMock.mockResolvedValue({
      success: true,
      equipment: {
        planetary_lander: { installed: false, cost: PLANETARY_LANDER_INSTALL_COST_CR },
      },
      equipped: {},
    });
    installEquipmentMock.mockResolvedValue({
      success: true,
      message: 'Planetary Lander installed',
      cost_paid: 20_000,
      remaining_credits: 80_000,
    });
    const onInstalled = vi.fn();

    await mount({ onInstalled });

    const btn = container.querySelector(
      '[data-testid="planetary-lander-install-btn"]',
    ) as HTMLButtonElement | null;
    expect(btn).toBeTruthy();
    expect(btn!.textContent).toContain('INSTALL PLANETARY LANDER');
    expect(btn!.textContent).toContain('20,000');

    await act(async () => {
      btn!.click();
      await Promise.resolve();
    });

    expect(installEquipmentMock).toHaveBeenCalledWith('ship-1', 'planetary_lander');
    expect(onInstalled).toHaveBeenCalledWith({
      remainingCredits: 80_000,
      message: 'Planetary Lander installed',
    });
  });

  it('hides Install CTA when planetary_lander is already fitted', async () => {
    getUpgradesMock.mockResolvedValue({
      success: true,
      equipment: {
        planetary_lander: { installed: true, cost: 20_000 },
      },
      equipped: {
        planetary_lander: { installed_at: '2026-01-01T00:00:00Z', effects: { landing_bonus: 1.25 } },
      },
    });

    await mount({});

    expect(container.querySelector('[data-testid="planetary-lander-install-btn"]')).toBeNull();
    expect(container.querySelector('[data-testid="planetary-lander-cta-fitted"]')).toBeTruthy();
    expect(container.textContent).toMatch(/landing bonus/i);
    expect(installEquipmentMock).not.toHaveBeenCalled();
  });

  it('renders nothing for an incompatible hull', async () => {
    getUpgradesMock.mockResolvedValue({ success: true, equipment: {}, equipped: {} });

    await mount({ shipType: 'SCOUT' });

    expect(container.querySelector('[data-testid="planetary-lander-cta"]')).toBeNull();
    expect(container.querySelector('[data-testid="planetary-lander-install-btn"]')).toBeNull();
    expect(getUpgradesMock).not.toHaveBeenCalled();
  });
});
