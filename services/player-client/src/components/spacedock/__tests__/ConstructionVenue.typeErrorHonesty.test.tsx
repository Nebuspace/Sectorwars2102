// @vitest-environment jsdom
/**
 * LEG-3774 Soft-ORDER — ConstructionVenue shell typeErrorHonesty.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ConstructionVenue, {
  formatConstructionQuotesLoadError,
  formatConstructionReservationsLoadError,
} from '../ConstructionVenue';

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

describe('ConstructionVenue shell formatters TypeError densify (LEG-3774)', () => {
  it('formatConstructionQuotesLoadError falls back on transport collapse', () => {
    expect(formatConstructionQuotesLoadError(new TypeError('Failed to fetch'), FALLBACK)).toBe(
      FALLBACK,
    );
    expect(formatConstructionQuotesLoadError(new Error('Network Error'), FALLBACK)).toBe(FALLBACK);
    expect(formatConstructionQuotesLoadError(new Error('Failed to fetch'), FALLBACK)).toBe(FALLBACK);
  });

  it('formatConstructionReservationsLoadError falls back on transport collapse', () => {
    expect(formatConstructionReservationsLoadError(new TypeError('Failed to fetch'), FALLBACK)).toBe(
      FALLBACK,
    );
    expect(formatConstructionReservationsLoadError(new Error('Network Error'), FALLBACK)).toBe(
      FALLBACK,
    );
  });

  it('preserves server detail for non-transport errors', () => {
    expect(
      formatConstructionQuotesLoadError(new Error('Dockmaster offline for maintenance.'), FALLBACK),
    ).toBe('Dockmaster offline for maintenance.');
  });
});

describe('formatConstructionQuotesLoadError 403/429 densify (LEG-4077)', () => {
  const apiRequestError = (status: number, message?: string) => {
    const err = new Error(message ?? `API Error: ${status}`);
    (err as { status?: number }).status = status;
    return err;
  };

  it('surfaces 403/429 without raw status codes', () => {
    expect(formatConstructionQuotesLoadError(apiRequestError(403), FALLBACK)).toMatch(
      /permission/i,
    );
    expect(
      formatConstructionQuotesLoadError(apiRequestError(403, 'construction_denied'), FALLBACK),
    ).toBe('construction_denied');
    expect(formatConstructionQuotesLoadError(apiRequestError(429), FALLBACK)).toMatch(
      /rate limit/i,
    );
    expect(formatConstructionQuotesLoadError(apiRequestError(429), FALLBACK)).not.toMatch(
      /\b429\b/,
    );
    expect(formatConstructionQuotesLoadError(apiRequestError(403), FALLBACK)).not.toMatch(
      /TypeError/i,
    );
  });

  it('reservations alias inherits 403/429 densify', () => {
    expect(
      formatConstructionReservationsLoadError(apiRequestError(403), FALLBACK),
    ).toMatch(/permission/i);
    expect(
      formatConstructionReservationsLoadError(apiRequestError(429), FALLBACK),
    ).toMatch(/rate limit/i);
  });
});

describe('ConstructionVenue shell load TypeError densify (LEG-3774)', () => {
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

  it('primary venue quotes load TypeError surfaces fallback without raw transport text', async () => {
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
