// @vitest-environment jsdom
/**
 * MedalShowcase — WO-UIPC-COCKPITINSTRUMENT-OCCLUSION follow-up hardening.
 *
 * `MedalData`'s shape is enforced by the TS type, not at runtime --
 * discovered live while proving the CockpitInstrument occlusion fix: a 200
 * response missing `earned`/`available` crashed the whole SERVICE RECORD
 * panel (`medalData.earned.filter` on undefined). Mirrors
 * ReputationPage.test.tsx's seam (jsdom + react-dom/client createRoot +
 * act(), no RTL in this project).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const mockGetMedals = vi.fn();
const mockPinMedal = vi.fn();

vi.mock('../../services/api', () => ({
  medalsAPI: {
    getMe: (...a: unknown[]) => mockGetMedals(...a),
    pinMedal: (...a: unknown[]) => mockPinMedal(...a),
  },
}));

vi.mock('../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ medalAwardedSignal: 0 }),
}));

import MedalShowcase from './MedalShowcase';

const makeMedal = (overrides: Record<string, unknown> = {}) => ({
  key: 'star_bronze',
  name: 'Bronze Star',
  category: 'Combat',
  description: 'First blood.',
  icon: 'star_bronze',
  ...overrides,
});

describe('MedalShowcase', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGetMedals.mockReset();
    mockPinMedal.mockReset();
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

  const mount = async () => {
    await act(async () => {
      root.render(<MedalShowcase />);
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  it('renders earned/available medal cards and the count on a well-formed response', async () => {
    mockGetMedals.mockResolvedValue({
      earned: [makeMedal({ key: 'star_bronze' })],
      available: [makeMedal({ key: 'star_silver', name: 'Silver Star' })],
    });
    await mount();

    expect(container.querySelector('.medal-count')?.textContent).toBe('1 / 2');
    expect(container.querySelectorAll('.medal-card.earned').length).toBe(1);
    expect(container.querySelectorAll('.medal-card.unearned').length).toBe(1);
  });

  it('does not crash and shows zero medals when `earned`/`available` are missing from the response', async () => {
    mockGetMedals.mockResolvedValue({});

    await expect(mount()).resolves.not.toThrow();

    expect(container.querySelector('.medal-error')).toBeNull();
    expect(container.querySelector('.medal-count')?.textContent).toBe('0 / 0');
    expect(container.querySelectorAll('.medal-card').length).toBe(0);
  });

  it('shows the error state instead of crashing when the fetch rejects', async () => {
    mockGetMedals.mockRejectedValue(new Error('Network down'));
    await mount();

    expect(container.querySelector('.medal-error')?.textContent).toBe('Network down');
  });

  it('pins an earned medal and shows the pinned state after refetch', async () => {
    mockGetMedals
      .mockResolvedValueOnce({
        earned: [makeMedal({ key: 'star_bronze' }), makeMedal({ key: 'star_silver', name: 'Silver Star' })],
        available: [],
        pinned_medal_id: null,
      })
      .mockResolvedValueOnce({
        earned: [makeMedal({ key: 'star_bronze' }), makeMedal({ key: 'star_silver', name: 'Silver Star' })],
        available: [],
        pinned_medal_id: 'star_bronze',
      });
    mockPinMedal.mockResolvedValue({ pinned_medal_id: 'star_bronze', medal_count: 2 });

    await mount();

    const pinButtons = Array.from(container.querySelectorAll('.medal-pin-btn'));
    expect(pinButtons[0].textContent).toBe('Pin');

    await act(async () => {
      (pinButtons[0] as HTMLButtonElement).click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mockPinMedal).toHaveBeenCalledWith('star_bronze');
    expect(container.querySelector('.medal-card.pinned')).not.toBeNull();
    expect(container.querySelector('.medal-pin-btn[aria-pressed="true"]')?.textContent).toBe('Unpin');
  });

  it('unpins the current medal by sending null', async () => {
    mockGetMedals
      .mockResolvedValueOnce({
        earned: [makeMedal({ key: 'star_bronze' })],
        available: [],
        pinned_medal_id: 'star_bronze',
      })
      .mockResolvedValueOnce({
        earned: [makeMedal({ key: 'star_bronze' })],
        available: [],
        pinned_medal_id: null,
      });
    mockPinMedal.mockResolvedValue({ pinned_medal_id: null, medal_count: 1 });

    await mount();

    const unpinBtn = container.querySelector('.medal-pin-btn') as HTMLButtonElement;
    expect(unpinBtn.textContent).toBe('Unpin');

    await act(async () => {
      unpinBtn.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mockPinMedal).toHaveBeenCalledWith(null);
    expect(container.querySelector('.medal-card.pinned')).toBeNull();
  });

  it('surfaces a 400 pin error without crashing', async () => {
    mockGetMedals.mockResolvedValue({
      earned: [makeMedal({ key: 'star_bronze' })],
      available: [],
      pinned_medal_id: null,
    });
    mockPinMedal.mockRejectedValue(new Error('Medal not earned'));

    await mount();

    const pinBtn = container.querySelector('.medal-pin-btn') as HTMLButtonElement;
    await act(async () => {
      pinBtn.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.querySelector('.medal-pin-error')?.textContent).toBe('Medal not earned');
  });
});
