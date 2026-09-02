// @vitest-environment jsdom
/**
 * LEG-3153 Soft-ORDER — RegionTreatyPanel TypeError densify.
 * LEG-4018 Soft-ORDER — 403/429 densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import RegionTreatyPanel, { formatRegionTreatyError } from '../RegionTreatyPanel';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { mockListMyTreaties } = vi.hoisted(() => ({
  mockListMyTreaties: vi.fn(),
}));

vi.mock('../../../services/api', () => ({
  regionOwnerAPI: {
    listMyTreaties: (...a: unknown[]) => mockListMyTreaties(...a),
    acceptTreaty: vi.fn(),
    rejectTreaty: vi.fn(),
    terminateTreaty: vi.fn(),
    proposeTreaty: vi.fn(),
  },
}));

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('formatRegionTreatyError TypeError densify (LEG-3153)', () => {
  const fallback = 'Failed to load treaties.';

  it('returns fallback on TypeError network collapse', () => {
    const text = formatRegionTreatyError(new TypeError('Failed to fetch'), fallback);
    expect(text).toBe(fallback);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('maps ERR_* codes to owner-readable copy', () => {
    expect(formatRegionTreatyError(new Error('ERR_NOT_REGION_OWNER'), fallback)).toBe(
      'You are not the owner of the required region.',
    );
  });

  it('preserves non-generic Error.message detail', () => {
    expect(formatRegionTreatyError(new Error('treaty_server_detail'), fallback)).toBe(
      'treaty_server_detail',
    );
  });

  it('falls back on axios Network Error / Failed to fetch non-TypeError (LEG-3286)', () => {
    expect(formatRegionTreatyError(new Error('Network Error'), fallback)).toBe(fallback);
    expect(formatRegionTreatyError(new Error('Failed to fetch'), fallback)).toBe(fallback);
    expect(formatRegionTreatyError(new Error('   '), fallback)).toBe(fallback);
  });
});


describe('RegionTreatyPanel 403/429 densify (LEG-4018)', () => {
  it('formatRegionTreatyError maps 403/429 without raw transport strings', () => {
    const fallback = 'Failed to load treaties.';
    expect(formatRegionTreatyError(apiRequestError(403), fallback)).toBe(
      'You do not have permission to manage region treaties.',
    );
    expect(formatRegionTreatyError(apiRequestError(403, 'ERR_NOT_REGION_OWNER'), fallback)).toBe(
      'You are not the owner of the required region.',
    );
    expect(formatRegionTreatyError(apiRequestError(429), fallback)).toBe(
      'Treaty action rate limit exceeded — wait a moment and try again.',
    );
    expect(formatRegionTreatyError(apiRequestError(429), fallback)).not.toMatch(/\b429\b/);
    expect(formatRegionTreatyError(apiRequestError(403), fallback)).not.toMatch(/TypeError/i);
    expect(formatRegionTreatyError(apiRequestError(403), fallback)).not.toMatch(/Network Error/i);
  });
});

describe('RegionTreatyPanel load TypeError densify (LEG-3153)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockListMyTreaties.mockReset();
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

  it('load TypeError surfaces fallback without Failed to fetch', async () => {
    mockListMyTreaties.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      root.render(<RegionTreatyPanel regionId="region-1" regionName="Alpha" />);
    });
    await act(async () => {
      await flush();
      await flush();
    });

    const panel = container.querySelector('[data-testid="region-treaty-panel"]');
    expect(panel?.textContent).toMatch(/Failed to load treaties/i);
    expect(panel?.textContent).not.toMatch(/Failed to fetch/i);
    expect(panel?.textContent).not.toMatch(/TypeError/i);
  });
});
