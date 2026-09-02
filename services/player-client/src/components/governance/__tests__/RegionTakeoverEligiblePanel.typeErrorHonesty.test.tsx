// @vitest-environment jsdom
/**
 * LEG-3957 — RegionTakeoverEligiblePanel load-path typeErrorHonesty (403/429).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mockListTakeoverEligible = vi.fn();
const mockBeginTakeover = vi.fn();

vi.mock('../../../services/api', () => ({
  regionTakeoverAPI: {
    listTakeoverEligible: (...args: unknown[]) => mockListTakeoverEligible(...args),
    beginTakeover: (...args: unknown[]) => mockBeginTakeover(...args),
  },
}));

import RegionTakeoverEligiblePanel, {
  formatRegionTakeoverLoadError,
  REGION_TAKEOVER_LOAD_FALLBACK,
} from '../RegionTakeoverEligiblePanel';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

const bareStatusError = (status: number) => {
  const err = new Error('');
  (err as { status?: number }).status = status;
  return err;
};

describe('formatRegionTakeoverLoadError (LEG-3957)', () => {
  it('falls back on transport collapse without raw Network Error / TypeError text', () => {
    expect(formatRegionTakeoverLoadError(new Error('Network Error'))).toBe(
      REGION_TAKEOVER_LOAD_FALLBACK,
    );
    expect(formatRegionTakeoverLoadError(new Error('Failed to fetch'))).toBe(
      REGION_TAKEOVER_LOAD_FALLBACK,
    );
    expect(formatRegionTakeoverLoadError(new TypeError('Failed to fetch'))).toBe(
      REGION_TAKEOVER_LOAD_FALLBACK,
    );
    expect(formatRegionTakeoverLoadError(new Error('Network Error'))).not.toMatch(
      /Network Error/i,
    );
    expect(formatRegionTakeoverLoadError(new TypeError('Failed to fetch'))).not.toMatch(
      /TypeError/i,
    );
  });

  it('surfaces 403 with server detail when present', () => {
    expect(formatRegionTakeoverLoadError(bareStatusError(403))).toBe(
      'Galactic Citizen subscription required to view takeover-eligible regions.',
    );
    expect(formatRegionTakeoverLoadError(apiRequestError(403, 'ERR_GALACTIC_CITIZEN_REQUIRED'))).toBe(
      'ERR_GALACTIC_CITIZEN_REQUIRED',
    );
  });

  it('surfaces 429 without raw status code in message', () => {
    expect(formatRegionTakeoverLoadError(bareStatusError(429))).toBe(
      'Region takeover lookup rate limit exceeded — wait a moment and try again.',
    );
    expect(formatRegionTakeoverLoadError(bareStatusError(429))).not.toMatch(/\b429\b/);
    expect(formatRegionTakeoverLoadError(bareStatusError(429))).not.toMatch(/HTTP 429/i);
  });
});

describe('RegionTakeoverEligiblePanel load typeErrorHonesty (LEG-3957)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockListTakeoverEligible.mockReset();
    mockBeginTakeover.mockReset();
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

  const flush = async () => {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  it('403 load shows friendly copy without raw transport strings', async () => {
    mockListTakeoverEligible.mockRejectedValue(bareStatusError(403));

    await act(async () => {
      root.render(<RegionTakeoverEligiblePanel />);
    });
    await flush();

    const errEl = container.querySelector('[data-testid="rte-load-error"]');
    expect(errEl).toBeTruthy();
    const text = errEl?.textContent ?? '';
    expect(text).toMatch(/Galactic Citizen subscription required/i);
    expect(text).not.toMatch(/Network Error/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('429 load shows rate-limit copy without exposing HTTP 429', async () => {
    mockListTakeoverEligible.mockRejectedValue(bareStatusError(429));

    await act(async () => {
      root.render(<RegionTakeoverEligiblePanel />);
    });
    await flush();

    const errEl = container.querySelector('[data-testid="rte-load-error"]');
    expect(errEl).toBeTruthy();
    const text = errEl?.textContent ?? '';
    expect(text).toMatch(/rate limit exceeded/i);
    expect(text).not.toMatch(/\b429\b/);
    expect(text).not.toMatch(/HTTP 429/i);
    expect(text).not.toMatch(/Network Error/i);
  });
});
