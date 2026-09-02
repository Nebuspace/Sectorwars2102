// @vitest-environment jsdom
/**
 * LEG-3680 Soft-ORDER — MedalShowcase TypeError / Network Error densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockGetMedals = vi.fn();
const mockPinMe = vi.fn();

vi.mock('../../../services/api', () => ({
  medalsAPI: {
    getMe: (...a: unknown[]) => mockGetMedals(...a),
    pinMe: (...a: unknown[]) => mockPinMe(...a),
  },
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ medalAwardedSignal: 0 }),
}));

import MedalShowcase, {
  formatMedalShowcaseLoadError,
  formatMedalShowcasePinError,
} from '../MedalShowcase';

const makeMedal = (overrides: Record<string, unknown> = {}) => ({
  key: 'star_bronze',
  name: 'Bronze Star',
  category: 'Combat',
  description: 'First blood.',
  icon: 'star_bronze',
  ...overrides,
});

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('formatMedalShowcaseLoadError TypeError densify (LEG-3680)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatMedalShowcaseLoadError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Failed to load medals/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatMedalShowcaseLoadError(new Error('Network Error'))).toBe('Failed to load medals');
    expect(formatMedalShowcaseLoadError(new Error('Failed to fetch'))).toBe('Failed to load medals');
    expect(formatMedalShowcaseLoadError(new Error('Network Error'))).not.toBe('Network Error');
  });

  it('preserves non-generic Error.message detail when not transport collapse', () => {
    expect(formatMedalShowcaseLoadError(new Error('medals_denied'))).toBe('medals_denied');
  });
});

describe('formatMedalShowcasePinError TypeError densify (LEG-3680)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatMedalShowcasePinError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to update pinned medal');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatMedalShowcasePinError(new Error('Network Error'))).toBe(
      'Failed to update pinned medal',
    );
    expect(formatMedalShowcasePinError(new Error('Failed to fetch'))).toBe(
      'Failed to update pinned medal',
    );
    expect(formatMedalShowcasePinError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not transport collapse', () => {
    expect(formatMedalShowcasePinError(new Error('Medal not earned'))).toBe('Medal not earned');
  });
});

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('formatMedalShowcaseLoadError 403/429 densify (LEG-4040)', () => {
  it('surfaces 403/429 without raw status codes', () => {
    expect(formatMedalShowcaseLoadError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatMedalShowcaseLoadError(apiRequestError(403, 'medals_denied'))).toBe(
      'medals_denied',
    );
    expect(formatMedalShowcaseLoadError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatMedalShowcaseLoadError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatMedalShowcaseLoadError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});

describe('formatMedalShowcasePinError 403/429 densify (LEG-4040)', () => {
  it('surfaces 403/429 without raw status codes', () => {
    expect(formatMedalShowcasePinError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatMedalShowcasePinError(apiRequestError(403, 'pin_denied'))).toBe('pin_denied');
    expect(formatMedalShowcasePinError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatMedalShowcasePinError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatMedalShowcasePinError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});

describe('MedalShowcase load/pin TypeError honesty (LEG-3680)', () => {
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
      await flush();
    });
  };

  it('load getMe TypeError surfaces honest fallback without Failed to fetch / TypeError in DOM', async () => {
    mockGetMedals.mockRejectedValue(new TypeError('Failed to fetch'));
    await mount();

    const loadErr = container.querySelector('.medal-error');
    expect(loadErr?.textContent).toMatch(/Failed to load medals/i);
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
  });

  it('load getMe Network Error surfaces honest fallback without Network Error in DOM', async () => {
    mockGetMedals.mockRejectedValue(new Error('Network Error'));
    await mount();

    const loadErr = container.querySelector('.medal-error');
    expect(loadErr?.textContent).toMatch(/Failed to load medals/i);
    expect(container.textContent).not.toMatch(/Network Error/i);
  });

  it('load getMe Failed to fetch surfaces honest fallback without raw transport in DOM', async () => {
    mockGetMedals.mockRejectedValue(new Error('Failed to fetch'));
    await mount();

    const loadErr = container.querySelector('.medal-error');
    expect(loadErr?.textContent).toMatch(/Failed to load medals/i);
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
  });

  it('pinMe TypeError surfaces honest fallback without Failed to fetch / TypeError in DOM', async () => {
    mockGetMedals.mockResolvedValue({
      earned: [makeMedal({ key: 'star_bronze' })],
      available: [],
      pinned_medal_id: null,
    });
    mockPinMe.mockRejectedValue(new TypeError('Failed to fetch'));
    await mount();

    const pinBtn = container.querySelector('.medal-pin-btn') as HTMLButtonElement;
    await act(async () => {
      pinBtn.click();
      await flush();
    });

    const pinErr = container.querySelector('.medal-pin-error');
    expect(pinErr?.textContent).toBe('Failed to update pinned medal');
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
    expect(container.querySelector('.medal-card.earned')).toBeTruthy();
  });

  it('pinMe Network Error surfaces honest fallback without Network Error in DOM', async () => {
    mockGetMedals.mockResolvedValue({
      earned: [makeMedal({ key: 'star_bronze' })],
      available: [],
      pinned_medal_id: null,
    });
    mockPinMe.mockRejectedValue(new Error('Network Error'));
    await mount();

    const pinBtn = container.querySelector('.medal-pin-btn') as HTMLButtonElement;
    await act(async () => {
      pinBtn.click();
      await flush();
    });

    const pinErr = container.querySelector('.medal-pin-error');
    expect(pinErr?.textContent).toBe('Failed to update pinned medal');
    expect(container.textContent).not.toMatch(/Network Error/i);
  });

  it('pinMe Failed to fetch surfaces honest fallback without raw transport in DOM', async () => {
    mockGetMedals.mockResolvedValue({
      earned: [makeMedal({ key: 'star_bronze' })],
      available: [],
      pinned_medal_id: null,
    });
    mockPinMe.mockRejectedValue(new Error('Failed to fetch'));
    await mount();

    const pinBtn = container.querySelector('.medal-pin-btn') as HTMLButtonElement;
    await act(async () => {
      pinBtn.click();
      await flush();
    });

    const pinErr = container.querySelector('.medal-pin-error');
    expect(pinErr?.textContent).toBe('Failed to update pinned medal');
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
  });
});
