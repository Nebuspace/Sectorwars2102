// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockNearest = vi.fn();
vi.mock('../../../services/api', () => ({
  miningAPI: {
    getNearestAmRefinery: (...a: unknown[]) => mockNearest(...a),
  },
}));

import NearestAmRefineryOverlay, {
  formatNearestAmRefineryError,
} from '../NearestAmRefineryOverlay';


describe('formatNearestAmRefineryError (LEG-3245)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatNearestAmRefineryError(new TypeError('Failed to fetch'));
    expect(text).toBe('Nearest AM refinery lookup failed');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch non-TypeError (LEG-3298)', () => {
    expect(formatNearestAmRefineryError(new Error('Network Error'))).toBe('Nearest AM refinery lookup failed');
    expect(formatNearestAmRefineryError(new Error('Failed to fetch'))).toBe('Nearest AM refinery lookup failed');
    expect(formatNearestAmRefineryError(new Error('   '))).toBe('Nearest AM refinery lookup failed');
    expect(formatNearestAmRefineryError(new Error('sector offline'))).toBe('sector offline');
  });

  it('preserves non-TypeError Error messages', () => {
    expect(formatNearestAmRefineryError(new Error('network down'))).toBe('network down');
  });
});

describe('NearestAmRefineryOverlay', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockNearest.mockReset();
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

  const flush = async () => {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  it('renders name, sector, hops, and ore buy_price from tip payload', async () => {
    mockNearest.mockResolvedValue({
      found: true,
      station: { id: 'st-1', name: 'AM Outpost 7', sector_id: 88 },
      hop_distance: 3,
      ore_buy_price: 42,
      reason: null,
    });
    await act(async () => {
      root.render(<NearestAmRefineryOverlay />);
    });
    await flush();
    expect(mockNearest).toHaveBeenCalled();
    const found = container.querySelector('[data-testid="am-refinery-found"]');
    expect(found?.textContent).toContain('AM Outpost 7');
    expect(found?.textContent).toContain('sector 88');
    expect(found?.textContent).toContain('3 hop');
    expect(found?.textContent).toContain('42 cr');
  });

  it('honest empty when found is false', async () => {
    mockNearest.mockResolvedValue({
      found: false,
      station: null,
      hop_distance: null,
      ore_buy_price: null,
      reason: 'none_reachable',
    });
    await act(async () => {
      root.render(<NearestAmRefineryOverlay />);
    });
    await flush();
    const empty = container.querySelector('[data-testid="am-refinery-empty"]');
    expect(empty?.textContent).toContain('No AM refinery reachable');
    expect(empty?.textContent).toContain('none_reachable');
  });

  it('surfaces request failure', async () => {
    mockNearest.mockRejectedValue(new Error('network down'));
    await act(async () => {
      root.render(<NearestAmRefineryOverlay />);
    });
    await flush();
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('network down');
  });

  it('load TypeError surfaces fallback without Failed to fetch / TypeError (LEG-3245)', async () => {
    mockNearest.mockRejectedValue(new TypeError('Failed to fetch'));
    await act(async () => {
      root.render(<NearestAmRefineryOverlay />);
    });
    await flush();
    const err = container.querySelector('[role="alert"]');
    expect(err?.textContent).toBe('Nearest AM refinery lookup failed');
    expect(err?.textContent).not.toMatch(/Failed to fetch/i);
    expect(err?.textContent).not.toMatch(/TypeError/i);
  });
});
