// @vitest-environment jsdom
/**
 * StationSecurityBanner — LEG-3105 on-dock security tier banner.
 */
import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const getSecurityStatus = vi.fn();

vi.mock('../../../services/api', () => ({
  stationSecurityAPI: {
    getSecurityStatus: (...args: unknown[]) => getSecurityStatus(...args),
  },
}));

import StationSecurityBanner, {
  formatSecurityTierLabel,
  formatStationSecurityBanner,
} from '../StationSecurityBanner';

describe('formatSecurityTierLabel', () => {
  it('title-cases known tiers and maps none', () => {
    expect(formatSecurityTierLabel('premium')).toBe('Premium');
    expect(formatSecurityTierLabel('basic')).toBe('Basic');
    expect(formatSecurityTierLabel('none')).toBe('None');
  });
});

describe('formatStationSecurityBanner', () => {
  it('renders the settled tier', () => {
    expect(
      formatStationSecurityBanner({
        station_id: 'st-1',
        tier: 'premium',
      }),
    ).toBe('Security tier: Premium');
  });

  it('notes a pending upgrade', () => {
    expect(
      formatStationSecurityBanner({
        station_id: 'st-1',
        tier: 'basic',
        pending_upgrade_to: 'standard',
      }),
    ).toBe('Security tier: Basic (upgrading to Standard)');
  });
});

describe('StationSecurityBanner', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    getSecurityStatus.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it('renders nothing without a station id', async () => {
    await act(async () => {
      root.render(<StationSecurityBanner />);
    });
    expect(container.querySelector('[data-testid="station-security-banner"]')).toBeNull();
    expect(getSecurityStatus).not.toHaveBeenCalled();
  });

  it('fetches and shows the security tier when docked', async () => {
    getSecurityStatus.mockResolvedValue({
      station_id: 'st-1',
      tier: 'standard',
    });

    await act(async () => {
      root.render(<StationSecurityBanner stationId="st-1" />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(getSecurityStatus).toHaveBeenCalledWith('st-1');
    const banner = container.querySelector('[data-testid="station-security-banner"]');
    expect(banner?.textContent).toBe('Security tier: Standard');
  });

  it('hides on fetch failure without breaking the dock shell', async () => {
    getSecurityStatus.mockRejectedValue(new Error('API Error: 404'));

    await act(async () => {
      root.render(<StationSecurityBanner stationId="st-missing" />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(container.querySelector('[data-testid="station-security-banner"]')).toBeNull();
  });
});
