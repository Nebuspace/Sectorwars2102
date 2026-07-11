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

vi.mock('../../services/api', () => ({
  rankingAPI: {
    getMedals: (...a: unknown[]) => mockGetMedals(...a),
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
});
