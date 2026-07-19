// @vitest-environment jsdom
/**
 * GenesisDeployment — WO-API-B2 REVISE (mack HIGH).
 *
 * Proves the pre-submit price re-confirm: GameContext has no live
 * reputation push, so the displayed Chartered total can go stale between
 * the panel's initial quote load and the moment the player clicks Deploy.
 * handleDeploy re-fetches the SELECTED quote fresh right before the charge
 * fires; if it matches what's on screen it proceeds immediately (no extra
 * click for the common case), but if it drifted, deployGenesis must NOT be
 * called on that click -- the player has to see the new total and click
 * Deploy again to confirm.
 *
 * No-RTL raw createRoot()+act() convention (mirrors ContractBoardVenue.
 * bulkPost.test.tsx's setInputValue/flush idiom) -- no new deps.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// player-client's vitest.config.ts has no setupFiles / IS_REACT_ACT_ENVIRONMENT,
// so every createRoot()+act() jsdom test otherwise logs a baseline console.error
// unrelated to the component under test (see vitest-act-environment-noise memory).
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { mockGetGenesisQuote, mockDeployGenesis, setCharteredFee, setRepGateMet } = vi.hoisted(() => {
  let charteredFee = 50000;
  let repGateMet = true;
  const DEVICE_COST: Record<string, number> = { basic: 25000, enhanced: 75000, advanced: 250000 };

  const mockGetGenesisQuote = vi.fn(async (tier: string, registration: string = 'registered') => {
    const deviceCost = DEVICE_COST[tier];
    const fee = registration === 'clandestine' ? 60000 : registration === 'chartered' ? charteredFee : 10000;
    return {
      tier,
      registration,
      device_cost: deviceCost,
      registration_fee: fee,
      total_cost: deviceCost + fee,
      player_credits: 1000000,
      can_afford: true,
      reputation_gate: { required: 250, current: repGateMet ? 300 : 100, met: repGateMet },
    };
  });

  const mockDeployGenesis = vi.fn(async () => ({
    success: true,
    planetId: 'planet-1',
    planetName: 'Nova Prime',
    planetType: 'OCEANIC',
    genesisDevicesRemaining: 4,
    deploymentTime: 0,
    formationStatus: 'forming',
  }));

  return {
    mockGetGenesisQuote,
    mockDeployGenesis,
    setCharteredFee: (v: number) => { charteredFee = v; },
    setRepGateMet: (v: boolean) => { repGateMet = v; },
  };
});

vi.mock('../../services/api', () => ({
  gameAPI: {
    planetary: {
      getGenesisQuote: mockGetGenesisQuote,
      deployGenesis: mockDeployGenesis,
    },
  },
}));

const mockUpdateShipGenesis = vi.fn();
const mockCurrentShip = {
  genesis_devices: 5,
  type: 'CARGO_HAULER',
  name: 'Test Hull',
};
const mockCurrentSector = { sector_id: 5 };
const mockPlayerState = { personal_reputation: 1000 };

vi.mock('../../contexts/GameContext', () => ({
  useGame: () => ({
    currentShip: mockCurrentShip,
    currentSector: mockCurrentSector,
    updateShipGenesis: mockUpdateShipGenesis,
    playerState: mockPlayerState,
  }),
}));

import { GenesisDeployment } from './GenesisDeployment';

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
};

describe('GenesisDeployment — pre-submit price re-confirm', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    mockGetGenesisQuote.mockClear();
    mockDeployGenesis.mockClear();
    mockUpdateShipGenesis.mockClear();
    setCharteredFee(50000);
    setRepGateMet(true);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  const mount = async () => {
    await act(async () => {
      root.render(<GenesisDeployment onSuccess={vi.fn()} onClose={vi.fn()} />);
    });
    await flush();
  };

  const setInputValue = async (selector: string, value: string) => {
    const el = container.querySelector(selector) as HTMLInputElement;
    expect(el, `expected an input matching ${selector}`).toBeTruthy();
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!;
      setter.call(el, value);
      el.dispatchEvent(new Event('input', { bubbles: true }));
    });
  };

  const clickRegistrationCard = async (index: number) => {
    // Order matches the REGISTRATIONS array: clandestine, registered, chartered.
    const cards = container.querySelectorAll('.genesis-registration-card');
    expect(cards.length).toBe(3);
    await act(async () => {
      (cards[index] as HTMLButtonElement).dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await flush();
  };

  const getDeployButton = () =>
    Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.includes('Deploy')) as
      | HTMLButtonElement
      | undefined;

  const clickDeploy = async () => {
    const btn = getDeployButton();
    expect(btn, 'expected a Deploy button').toBeTruthy();
    await act(async () => {
      btn!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await flush();
  };

  it('deploys on the first click when the re-verified price matches what is displayed', async () => {
    await mount();
    await setInputValue('#planet-name', 'Nova Prime');

    // Default tier=basic, registration=registered -- fee is fixed (10,000),
    // no drift possible, so this should charge on the FIRST click.
    await clickDeploy();

    expect(mockDeployGenesis).toHaveBeenCalledTimes(1);
    expect(mockDeployGenesis).toHaveBeenCalledWith('5', 'Nova Prime', 'basic', 'registered');
    expect(container.querySelector('.error-message')?.textContent ?? '').not.toMatch(/Price changed/);
  });

  it('blocks the charge and surfaces the new total when the price drifts between load and submit', async () => {
    await mount();
    await setInputValue('#planet-name', 'Nova Prime');
    await clickRegistrationCard(2); // chartered

    // Displayed total is now basic(25,000) + chartered(50,000) = 75,000.
    // Simulate the player's reputation improving elsewhere (combat/bounty/
    // another tab) between the panel loading and the player clicking Deploy
    // -- GameContext never pushes this locally, so only the pre-submit
    // re-fetch inside handleDeploy can catch it.
    setCharteredFee(20000);

    await clickDeploy();

    // Must NOT have charged the (now stale) price it originally displayed.
    expect(mockDeployGenesis).not.toHaveBeenCalled();
    const errorText = container.querySelector('.error-message')?.textContent || '';
    expect(errorText).toMatch(/Price changed/);
    expect(errorText).toContain('75,000'); // old total: 25,000 + 50,000
    expect(errorText).toContain('45,000'); // new total: 25,000 + 20,000

    // The displayed total should now reflect the fresh number.
    const totalEl = Array.from(container.querySelectorAll('.summary-total .summary-value')).find((el) =>
      el.textContent?.includes('cr'),
    );
    expect(totalEl?.textContent).toContain('45,000');

    // Second click re-verifies against the NOW-displayed (already fresh) total,
    // finds no further drift, and proceeds.
    await clickDeploy();

    expect(mockDeployGenesis).toHaveBeenCalledTimes(1);
    expect(mockDeployGenesis).toHaveBeenCalledWith('5', 'Nova Prime', 'basic', 'chartered');
  });

  it('disables Deploy and shows the reputation requirement when the player is under the gate', async () => {
    setRepGateMet(false);
    await mount();
    await setInputValue('#planet-name', 'Nova Prime');

    const btn = getDeployButton();
    expect(btn?.disabled).toBe(true);
    expect(container.textContent).toContain('Requires Federation reputation 250');

    await clickDeploy();
    expect(mockDeployGenesis).not.toHaveBeenCalled();
  });

  it('blocks the charge with the friendly message when reputation drops below the gate between load and submit', async () => {
    await mount();
    await setInputValue('#planet-name', 'Nova Prime');

    // Deploy was enabled at load (met=true). Simulate the player's reputation
    // dropping below the gate in the narrow window before they click Deploy --
    // the pre-submit re-fetch must catch this against the FRESH quote, not
    // just the (still-permissive) one rendered at load.
    setRepGateMet(false);

    await clickDeploy();

    expect(mockDeployGenesis).not.toHaveBeenCalled();
    expect(container.querySelector('.error-message')?.textContent).toMatch(/Requires Federation reputation 250/);
  });
});
