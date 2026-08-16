// @vitest-environment jsdom
/**
 * MedalShowcase — WO-UIPC-COCKPITINSTRUMENT-OCCLUSION follow-up hardening
 * + LEG-87 Trophy Room pin control (PUT /api/v1/medals/me/pin).
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
const mockPin = vi.fn();

vi.mock('../../services/api', () => ({
  medalsAPI: {
    getMe: (...a: unknown[]) => mockGetMedals(...a),
    pin: (...a: unknown[]) => mockPin(...a),
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
    mockPin.mockReset();
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

  it('pins an earned medal on happy path (LEG-87)', async () => {
    mockGetMedals.mockResolvedValue({
      earned: [makeMedal({ key: 'star_bronze' })],
      available: [],
    });
    mockPin.mockResolvedValue({ pinned_medal_id: 'star_bronze', medal_count: 1 });
    await mount();

    const pinBtn = container.querySelector(
      '[data-testid="medal-pin-btn-star_bronze"]',
    ) as HTMLButtonElement;
    expect(pinBtn).toBeTruthy();
    expect(pinBtn.textContent).toBe('Pin');

    await act(async () => {
      pinBtn.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mockPin).toHaveBeenCalledWith('star_bronze');
    expect(container.querySelector('[data-testid="medal-pinned-badge"]')?.textContent).toBe(
      'Pinned',
    );
    expect(
      container.querySelector('[data-testid="medal-card-star_bronze"]')?.getAttribute('data-pinned'),
    ).toBe('true');
    expect(pinBtn.textContent).toBe('Unpin');
  });

  it('clears the pin via Clear pin (LEG-87)', async () => {
    mockGetMedals.mockResolvedValue({
      earned: [makeMedal({ key: 'star_bronze' })],
      available: [],
    });
    mockPin
      .mockResolvedValueOnce({ pinned_medal_id: 'star_bronze', medal_count: 1 })
      .mockResolvedValueOnce({ pinned_medal_id: null, medal_count: 1 });
    await mount();

    await act(async () => {
      (container.querySelector(
        '[data-testid="medal-pin-btn-star_bronze"]',
      ) as HTMLButtonElement).click();
      await Promise.resolve();
      await Promise.resolve();
    });

    const clearBtn = container.querySelector(
      '[data-testid="medal-clear-pin"]',
    ) as HTMLButtonElement;
    expect(clearBtn).toBeTruthy();

    await act(async () => {
      clearBtn.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mockPin).toHaveBeenLastCalledWith(null);
    expect(container.querySelector('[data-testid="medal-pinned-badge"]')).toBeNull();
    expect(container.querySelector('[data-testid="medal-clear-pin"]')).toBeNull();
  });

  it('surfaces pin rejection detail (LEG-87)', async () => {
    mockGetMedals.mockResolvedValue({
      earned: [makeMedal({ key: 'star_bronze' })],
      available: [],
    });
    mockPin.mockRejectedValue(new Error('Cannot pin a medal you have not earned'));
    await mount();

    await act(async () => {
      (container.querySelector(
        '[data-testid="medal-pin-btn-star_bronze"]',
      ) as HTMLButtonElement).click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.querySelector('[data-testid="medal-pin-error"]')?.textContent).toBe(
      'Cannot pin a medal you have not earned',
    );
    expect(container.querySelector('[data-testid="medal-pinned-badge"]')).toBeNull();
  });
});
