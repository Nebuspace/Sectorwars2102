// @vitest-environment jsdom
/**
 * LEG-3750 Soft-ORDER — StationSecurityBanner TypeError/network densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const getSecurityStatus = vi.fn();

vi.mock('../../../services/api', () => ({
  stationSecurityAPI: {
    getSecurityStatus: (...args: unknown[]) => getSecurityStatus(...args),
  },
}));

import StationSecurityBanner, {
  formatStationSecurityBannerLoadError,
  STATION_SECURITY_BANNER_LOAD_FALLBACK,
} from '../StationSecurityBanner';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('formatStationSecurityBannerLoadError (LEG-3750)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatStationSecurityBannerLoadError(new TypeError('Failed to fetch'));
    expect(text).toBe(STATION_SECURITY_BANNER_LOAD_FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatStationSecurityBannerLoadError(new Error('Network Error'))).toBe(
      STATION_SECURITY_BANNER_LOAD_FALLBACK,
    );
    expect(formatStationSecurityBannerLoadError(new Error('Failed to fetch'))).toBe(
      STATION_SECURITY_BANNER_LOAD_FALLBACK,
    );
  });

  it('preserves non-generic server detail when not transport collapse', () => {
    expect(formatStationSecurityBannerLoadError(new Error('station_security_offline'))).toBe(
      'station_security_offline',
    );
  });
});

describe('StationSecurityBanner load transport collapse densify (LEG-3750)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    getSecurityStatus.mockReset();
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

  it('network rejection surfaces role=alert fallback without raw transport text', async () => {
    getSecurityStatus.mockRejectedValue(new Error('Network Error'));

    await act(async () => {
      root.render(<StationSecurityBanner stationId="st-1" />);
    });
    await act(async () => {
      await flush();
    });

    const alert = container.querySelector('[data-testid="station-security-banner-error"]');
    expect(alert?.getAttribute('role')).toBe('alert');
    expect(alert?.textContent).toBe(STATION_SECURITY_BANNER_LOAD_FALLBACK);
    expect(container.textContent).not.toMatch(/Network Error/i);
  });

  it('TypeError Failed to fetch surfaces role=alert fallback without raw transport text', async () => {
    getSecurityStatus.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      root.render(<StationSecurityBanner stationId="st-1" />);
    });
    await act(async () => {
      await flush();
    });

    const alert = container.querySelector('[role="alert"]');
    expect(alert?.textContent).toBe(STATION_SECURITY_BANNER_LOAD_FALLBACK);
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
  });
});
