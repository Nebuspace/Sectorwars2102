// @vitest-environment jsdom
/**
 * LEG-3500 Soft-ORDER — ConstructionVenue quotes load TypeError densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ConstructionVenue, { formatConstructionQuotesLoadError } from '../ConstructionVenue';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../../../services/api', () => ({
  resourceAPI: { list: vi.fn(() => new Promise(() => {})) },
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    currentShip: { cargo: { contents: {} } },
    refreshPlayerState: vi.fn(),
    loadShips: vi.fn(),
  }),
}));

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const VENUE_PROPS = {
  stationId: 'station-1',
  stationName: 'Test Dock',
  tier: 'A' as const,
  credits: 100000,
  onCreditsDelta: vi.fn(),
  onCreditsSet: vi.fn(),
  onBack: vi.fn(),
};

const FALLBACK = 'Connection error. Please try again.';

describe('formatConstructionQuotesLoadError TypeError densify (LEG-3500)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatConstructionQuotesLoadError(new TypeError('Failed to fetch'), FALLBACK);
    expect(text).toBe(FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves server detail for non-TypeError errors', () => {
    expect(
      formatConstructionQuotesLoadError(
        new Error('Dockmaster offline for maintenance.'),
        FALLBACK,
      ),
    ).toBe('Dockmaster offline for maintenance.');
  });

  it('falls back on axios Network Error / Failed to fetch non-TypeError', () => {
    expect(formatConstructionQuotesLoadError(new Error('Network Error'), FALLBACK)).toBe(FALLBACK);
    expect(formatConstructionQuotesLoadError(new Error('Failed to fetch'), FALLBACK)).toBe(FALLBACK);
    expect(formatConstructionQuotesLoadError(new Error('   '), FALLBACK)).toBe(FALLBACK);
  });
});

describe('ConstructionVenue quotes load TypeError densify (LEG-3500)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    localStorage.setItem('accessToken', 'test-token');
    fetchMock = vi.fn((url: string) => {
      if (url.includes('/construction/quotes')) {
        return Promise.reject(new TypeError('Failed to fetch'));
      }
      if (url.includes('/construction/reservations/mine')) {
        return Promise.resolve({ ok: true, json: async () => ({ reservations: [] }) });
      }
      return Promise.resolve({ ok: true, json: async () => ({}) });
    });
    vi.stubGlobal('fetch', fetchMock);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('quotes TypeError surfaces fallback without Failed to fetch / TypeError in DOM', async () => {
    await act(async () => {
      root.render(<ConstructionVenue {...VENUE_PROPS} />);
    });
    await act(async () => {
      await flush();
    });

    const errEl = container.querySelector('.genesis-error-message');
    expect(errEl).toBeTruthy();
    const text = errEl?.textContent ?? '';
    expect(text).toContain(FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });
});
