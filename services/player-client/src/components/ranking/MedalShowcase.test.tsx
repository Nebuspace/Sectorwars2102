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
const mockPinMe = vi.fn();

vi.mock('../../services/api', () => ({
  medalsAPI: {
    getMe: (...a: unknown[]) => mockGetMedals(...a),
    pinMe: (...a: unknown[]) => mockPinMe(...a),
  },
}));

vi.mock('../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ medalAwardedSignal: 0 }),
}));

import MedalShowcase, { formatMedalShowcaseLoadError } from './MedalShowcase';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

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
    mockPinMe.mockReset();
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

  it('surfaces 404 with server detail on initial load failure', async () => {
    mockGetMedals.mockRejectedValue(apiRequestError(404, 'Player not found'));
    await mount();

    expect(container.querySelector('.medal-error')?.textContent).toBe('Player not found');
  });

  it('pins an earned medal via PUT /medals/me/pin and marks the card pinned', async () => {
    mockGetMedals.mockResolvedValue({
      earned: [makeMedal({ key: 'star_bronze' })],
      available: [],
      pinned_medal_id: null,
    });
    mockPinMe.mockResolvedValue({ pinned_medal_id: 'star_bronze', medal_count: 1 });
    await mount();

    const pinBtn = container.querySelector('.medal-pin-btn') as HTMLButtonElement;
    expect(pinBtn).toBeTruthy();
    expect(pinBtn.getAttribute('aria-pressed')).toBe('false');

    await act(async () => {
      pinBtn.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mockPinMe).toHaveBeenCalledWith('star_bronze');
    expect(container.querySelector('.medal-card.earned.pinned')).toBeTruthy();
    expect(
      container.querySelector('.medal-pin-btn')?.getAttribute('aria-pressed'),
    ).toBe('true');
  });

  it('unpins the active medal when pinMe is called with null', async () => {
    mockGetMedals.mockResolvedValue({
      earned: [makeMedal({ key: 'star_bronze' })],
      available: [],
      pinned_medal_id: 'star_bronze',
    });
    mockPinMe.mockResolvedValue({ pinned_medal_id: null, medal_count: 1 });
    await mount();

    const pinBtn = container.querySelector('.medal-pin-btn') as HTMLButtonElement;
    expect(pinBtn.getAttribute('aria-pressed')).toBe('true');

    await act(async () => {
      pinBtn.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mockPinMe).toHaveBeenCalledWith(null);
    expect(container.querySelector('.medal-card.earned.pinned')).toBeNull();
  });

  it('surfaces pin API errors without crashing the showcase', async () => {
    mockGetMedals.mockResolvedValue({
      earned: [makeMedal({ key: 'star_bronze' })],
      available: [],
      pinned_medal_id: null,
    });
    mockPinMe.mockRejectedValue(new Error('Medal not earned'));
    await mount();

    const pinBtn = container.querySelector('.medal-pin-btn') as HTMLButtonElement;
    await act(async () => {
      pinBtn.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.querySelector('.medal-pin-error')?.textContent).toBe('Medal not earned');
    expect(container.querySelector('.medal-card.earned')).toBeTruthy();
  });

  it('formatMedalShowcaseLoadError falls back on TypeError network collapse (LEG-3013)', () => {
    const text = formatMedalShowcaseLoadError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Failed to load medals/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

});
