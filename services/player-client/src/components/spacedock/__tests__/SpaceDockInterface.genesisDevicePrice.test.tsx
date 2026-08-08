// @vitest-environment jsdom
/**
 * SpaceDockInterface — genesis device acquisition price is server-read, not
 * hardcoded (WO-API-PHASE1 Lane C / B8, Option A).
 *
 * Proves the Genesis Store venue reads its "Genesis Device" acquisition
 * price from GET /genesis/available's `device_acquisition_cost` field
 * (genesis_service.py's DRY-shared GENESIS_DEVICE_PRICE), NOT from
 * `tiers.basic.cost` (a same-valued-today but conceptually different field —
 * the per-tier DEPLOY sequence cost, see the STEP-0 finding on this WO) and
 * NOT from a client-side hardcoded literal. The response fixture below
 * deliberately uses two DIFFERENT numbers for device_acquisition_cost and
 * tiers.basic.cost so a regression back to reading the wrong field, or back
 * to the old hardcoded 25000, fails this test.
 *
 * Also proves the graceful-degrade path (#139): when the field is absent
 * (or the fetch fails outright), the device price renders "—", the Acquire
 * button is disabled with a "Price Unavailable" label, and nothing crashes
 * (no console.error) -- never a guessed/stale price used to gate or display
 * an afford check. This includes a REOPEN failure after a prior successful
 * load (mack finding, LOW): the price must degrade on every failed/absent
 * fetch, not just the first one, or a stale price keeps rendering.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const STATION_1: any = {
  id: 'station-1',
  name: 'Trading Post',
  type: 'TRADING',
  sector_id: 100,
  services: { genesis_dealer: true },
  status: 'OPERATIONAL',
};

function makeGameState(overrides: Record<string, unknown> = {}) {
  return {
    playerState: {
      id: 'player-1',
      credits: 1000000,
      current_port_id: 'station-1',
      is_docked: true,
    },
    stationsInSector: [STATION_1],
    updatePlayerCredits: vi.fn(),
    updateShipGenesis: vi.fn(),
    refreshPlayerState: vi.fn().mockResolvedValue(undefined),
    loadShips: vi.fn(),
    getStationSlips: vi.fn().mockResolvedValue(null),
    ...overrides,
  };
}

let gameState: ReturnType<typeof makeGameState>;
vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => gameState,
}));

import SpaceDockInterface from '../SpaceDockInterface';

// A ship able to hold Genesis Devices (max_genesis_devices > 0, none loaded
// yet) so the price/afford gating -- not an unrelated "Ship Incompatible"
// state -- is what actually drives the button's disabled/label.
const SHIP = {
  id: 'ship-1',
  name: 'Test Hauler',
  type: 'CARGO_HAULER',
  genesis_devices: 0,
  max_genesis_devices: 2,
};

// device_acquisition_cost (42000) is deliberately NOT equal to
// tiers.basic.cost (25000) -- the two are different server concepts that
// happen to coincide at 25000 in production today; keeping them distinct
// here means a regression to reading tiers.basic.cost is caught, not masked
// by a lucky value match.
function mockFetch(devicePrice: number | undefined) {
  return vi.fn((url: string) => {
    if (url.includes('/genesis/available')) {
      const body: Record<string, unknown> = {
        purchases_remaining: 3,
        max_purchases_per_week: 3,
        reputation_gate: { required: 250, current: 1000, met: true },
        tiers: { basic: { cost: 25000 }, enhanced: { cost: 75000 }, advanced: { cost: 250000 } },
      };
      if (devicePrice !== undefined) body.device_acquisition_cost = devicePrice;
      return Promise.resolve({ ok: true, json: async () => body });
    }
    if (url.includes('/player/current-ship')) {
      return Promise.resolve({ ok: true, json: async () => SHIP });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });
}

async function mountAndOpenGenesis() {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(<SpaceDockInterface />);
  });
  await act(async () => { await Promise.resolve(); await Promise.resolve(); }); // flush current-ship fetch

  const venueCard = Array.from(container.querySelectorAll('.venue-card'))
    .find(el => el.textContent?.includes('Genesis Store')) as HTMLElement;
  await act(async () => {
    venueCard.click();
  });
  await act(async () => { await Promise.resolve(); await Promise.resolve(); }); // flush /genesis/available

  return { container, root };
}

describe('SpaceDockInterface — Genesis Store reads device_acquisition_cost from the server', () => {
  let errorSpy: ReturnType<typeof vi.spyOn>;
  let mounted: Array<{ container: HTMLElement; root: ReturnType<typeof createRoot> }> = [];

  beforeEach(() => {
    gameState = makeGameState();
    localStorage.setItem('accessToken', 'test-token');
    errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    mounted = [];
  });

  afterEach(async () => {
    for (const { container, root } of mounted) {
      await act(async () => { root.unmount(); });
      container.remove();
    }
    vi.unstubAllGlobals();
    localStorage.clear();
    errorSpy.mockRestore();
  });

  it('renders the server device_acquisition_cost (not tiers.basic.cost, not a hardcoded 25000) and enables Acquire', async () => {
    vi.stubGlobal('fetch', mockFetch(42000));
    const { container, root } = await mountAndOpenGenesis();
    mounted.push({ container, root });

    const priceEl = container.querySelector('.device-price');
    expect(priceEl).not.toBeNull();
    expect(priceEl!.textContent).toContain('42,000');
    // The tiers.basic.cost value (25,000) must NOT be what's displayed here.
    expect(priceEl!.textContent).not.toContain('25,000');

    const button = container.querySelector('.purchase-device-btn') as HTMLButtonElement;
    expect(button).not.toBeNull();
    expect(button.disabled).toBe(false);
    expect(button.textContent).toBe('Acquire Device');
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('graceful-degrades to "—" and a disabled, clearly-labeled button when device_acquisition_cost is absent from the response', async () => {
    vi.stubGlobal('fetch', mockFetch(undefined));
    const { container, root } = await mountAndOpenGenesis();
    mounted.push({ container, root });

    const priceEl = container.querySelector('.device-price');
    expect(priceEl).not.toBeNull();
    expect(priceEl!.textContent).toBe('—');

    const button = container.querySelector('.purchase-device-btn') as HTMLButtonElement;
    expect(button).not.toBeNull();
    expect(button.disabled).toBe(true);
    expect(button.textContent).toBe('Price Unavailable');
    // No white-screen / crash from the missing field.
    expect(container.querySelector('.genesis-devices-grid')).not.toBeNull();
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('graceful-degrades the same way when the /genesis/available fetch fails outright', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      if (url.includes('/genesis/available')) {
        return Promise.resolve({ ok: false, json: async () => ({}) });
      }
      if (url.includes('/player/current-ship')) {
        return Promise.resolve({ ok: true, json: async () => SHIP });
      }
      return Promise.resolve({ ok: true, json: async () => ({}) });
    }));
    const { container, root } = await mountAndOpenGenesis();
    mounted.push({ container, root });

    const priceEl = container.querySelector('.device-price');
    expect(priceEl!.textContent).toBe('—');
    const button = container.querySelector('.purchase-device-btn') as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.textContent).toBe('Price Unavailable');
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('does NOT keep showing a stale price after a successful open, then a failed reopen (mack finding)', async () => {
    let genesisAvailableFails = false;
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      if (url.includes('/genesis/available')) {
        if (genesisAvailableFails) {
          return Promise.resolve({ ok: false, json: async () => ({}) });
        }
        return Promise.resolve({
          ok: true,
          json: async () => ({
            purchases_remaining: 3,
            max_purchases_per_week: 3,
            reputation_gate: { required: 250, current: 1000, met: true },
            tiers: { basic: { cost: 25000 } },
            device_acquisition_cost: 42000,
          }),
        });
      }
      if (url.includes('/player/current-ship')) {
        return Promise.resolve({ ok: true, json: async () => SHIP });
      }
      return Promise.resolve({ ok: true, json: async () => ({}) });
    }));

    const { container, root } = await mountAndOpenGenesis();
    mounted.push({ container, root });

    // First open succeeds -- price rendered.
    expect(container.querySelector('.device-price')!.textContent).toContain('42,000');

    // Back to hub, flip the mock to fail, then reopen Genesis.
    genesisAvailableFails = true;
    const backButton = container.querySelector('.back-button') as HTMLElement;
    expect(backButton).not.toBeNull();
    await act(async () => { backButton.click(); });

    const venueCardAgain = Array.from(container.querySelectorAll('.venue-card'))
      .find(el => el.textContent?.includes('Genesis Store')) as HTMLElement;
    await act(async () => { venueCardAgain.click(); });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    const priceElAfter = container.querySelector('.device-price');
    expect(priceElAfter!.textContent).toBe('—'); // NOT the stale "42,000"
    const buttonAfter = container.querySelector('.purchase-device-btn') as HTMLButtonElement;
    expect(buttonAfter.disabled).toBe(true);
    expect(buttonAfter.textContent).toBe('Price Unavailable');
    expect(errorSpy).not.toHaveBeenCalled();
  });
});
