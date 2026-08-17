// @vitest-environment jsdom
/**
 * PortOfficeVenue — defense-policy owner console (LEG-278).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const OWNER_ID = 'player-1';
const OTHER_ID = 'player-2';

const {
  getListing,
  getMyStations,
  getDefensePolicy,
  setDefensePolicy,
} = vi.hoisted(() => ({
  getListing: vi.fn(),
  getMyStations: vi.fn(),
  getDefensePolicy: vi.fn(),
  setDefensePolicy: vi.fn(),
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: { id: OWNER_ID },
    getListing,
    listStation: vi.fn(),
    placeOffer: vi.fn(),
    getMyStations,
    setStationTax: vi.fn(),
    withdrawTreasury: vi.fn(),
    getDefensePolicy,
    setDefensePolicy,
    getTakeoverStatus: vi.fn(async () => ({})),
    launchTakeover: vi.fn(),
    counterTakeover: vi.fn(),
  }),
}));

import PortOfficeVenue from '../PortOfficeVenue';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const ownedListing = {
  owner_id: OWNER_ID,
  owner_name: 'You',
  status: 'owned',
  is_listed: false,
  tax_rate: 0.1,
  treasury_balance: 5000,
};

const foreignListing = {
  owner_id: OTHER_ID,
  owner_name: 'Rival',
  status: 'owned',
  is_listed: false,
  tax_rate: 0.1,
  treasury_balance: 0,
};

describe('PortOfficeVenue — defense policy (LEG-278)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    getListing.mockReset();
    getMyStations.mockReset();
    getDefensePolicy.mockReset();
    setDefensePolicy.mockReset();
    getMyStations.mockResolvedValue([
      { station_id: 'station-1', treasury_balance: 5000, tax_rate: 0.1 },
    ]);
    getDefensePolicy.mockResolvedValue({
      station_id: 'station-1',
      defense_policy: {
        docking_access: 'open',
        hostility_list: [],
        punitive_fee_mult: 1.0,
        defender_posture: 'passive',
        drone_allocation_pct: 100,
        patrol_radius: 0,
      },
    });
    setDefensePolicy.mockResolvedValue({
      station_id: 'station-1',
      defense_policy: {
        docking_access: 'hostile_deny',
        hostility_list: ['rival-9'],
        punitive_fee_mult: 2.5,
        defender_posture: 'aggressive',
        drone_allocation_pct: 40,
        patrol_radius: 0,
      },
    });
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

  it('submits the GS DefensePolicyRequest payload shape', async () => {
    getListing.mockResolvedValue(ownedListing);

    await act(async () => {
      root.render(
        <PortOfficeVenue
          stationId="station-1"
          stationName="Test Port"
          credits={100_000}
          onCreditsSet={vi.fn()}
          onBack={vi.fn()}
        />,
      );
    });
    await act(async () => {
      await flush();
      await flush();
    });

    const ownerTab = Array.from(container.querySelectorAll('[role="tab"]')).find((t) =>
      (t.textContent || '').toLowerCase().includes('owner'),
    ) as HTMLElement | undefined;
    expect(ownerTab).toBeTruthy();
    await act(async () => {
      ownerTab!.click();
      await flush();
      await flush();
    });

    expect(container.querySelector('[data-testid="po-defense-policy"]')).toBeTruthy();
    expect(getDefensePolicy).toHaveBeenCalledWith('station-1');

    const access = container.querySelector(
      'select[aria-label="Docking access mode"]',
    ) as HTMLSelectElement;
    const posture = container.querySelector(
      'select[aria-label="Defender posture"]',
    ) as HTMLSelectElement;
    const fee = container.querySelector(
      'input[aria-label="Punitive fee multiplier"]',
    ) as HTMLInputElement;
    const drones = container.querySelector(
      'input[aria-label="Drone allocation percentage"]',
    ) as HTMLInputElement;
    const list = container.querySelector(
      'textarea[aria-label="Hostility list player ids"]',
    ) as HTMLTextAreaElement;

    await act(async () => {
      access.value = 'hostile_deny';
      access.dispatchEvent(new Event('change', { bubbles: true }));
      posture.value = 'aggressive';
      posture.dispatchEvent(new Event('change', { bubbles: true }));
      const feeSetter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      )!.set!;
      feeSetter.call(fee, '2.5');
      fee.dispatchEvent(new Event('input', { bubbles: true }));
      feeSetter.call(drones, '40');
      drones.dispatchEvent(new Event('input', { bubbles: true }));
      const areaSetter = Object.getOwnPropertyDescriptor(
        window.HTMLTextAreaElement.prototype,
        'value',
      )!.set!;
      areaSetter.call(list, 'rival-9');
      list.dispatchEvent(new Event('input', { bubbles: true }));
    });

    const postBtn = Array.from(container.querySelectorAll('button')).find((b) =>
      (b.textContent || '').includes('Post Defense Policy'),
    ) as HTMLButtonElement;
    expect(postBtn).toBeTruthy();
    await act(async () => {
      postBtn.click();
      await flush();
      await flush();
    });

    expect(setDefensePolicy).toHaveBeenCalledWith('station-1', {
      docking_access: 'hostile_deny',
      hostility_list: ['rival-9'],
      punitive_fee_mult: 2.5,
      defender_posture: 'aggressive',
      drone_allocation_pct: 40,
    });
  });

  it('hides the defense-policy panel for non-owners', async () => {
    getListing.mockResolvedValue(foreignListing);

    await act(async () => {
      root.render(
        <PortOfficeVenue
          stationId="station-1"
          stationName="Test Port"
          credits={100_000}
          onCreditsSet={vi.fn()}
          onBack={vi.fn()}
        />,
      );
    });
    await act(async () => {
      await flush();
      await flush();
    });

    const ownerTab = Array.from(container.querySelectorAll('[role="tab"]')).find((t) =>
      (t.textContent || '').toLowerCase().includes('owner'),
    );
    expect(ownerTab).toBeFalsy();
    expect(container.querySelector('[data-testid="po-defense-policy"]')).toBeNull();
    expect(getDefensePolicy).not.toHaveBeenCalled();
  });
});
