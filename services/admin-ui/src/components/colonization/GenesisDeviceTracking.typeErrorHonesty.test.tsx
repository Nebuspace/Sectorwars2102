import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { GenesisDeviceTracking } from './GenesisDeviceTracking';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));


const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
  expect(text).not.toMatch(/^HTTP \d+$/);
  expect(text).not.toContain('Request failed with status code');
}

/**
 * LEG-3428 Soft-ORDER — GenesisDeviceTracking TypeError/Network Error honesty densify.
 * LEG-3887 Soft-ORDER — 403/429 HTTP honesty densify.
 */
describe('GenesisDeviceTracking typeErrorHonesty densify (LEG-3428)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('collapses axios Network Error to gameserver-unreachable fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<GenesisDeviceTracking />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Gameserver unreachable|network error fetching Genesis device/i);
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
    expect(alert).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch to gameserver-unreachable fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<GenesisDeviceTracking />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Gameserver unreachable|network error fetching Genesis device/i);
    expect(alert).not.toMatch(/TypeError/i);
    expect(alert).not.toBe('Failed to fetch');
  });

  it('surfaces 403 with REGIONS_VIEW scope copy when genesis GET is denied', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<GenesisDeviceTracking />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Access denied|REGIONS_VIEW/i);
    expect(alert).not.toMatch(/\b403\b/);
    expect(alert).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(alert);
  });

  it('surfaces 429 as admin rate-limit copy on genesis GET', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<GenesisDeviceTracking />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/rate limit/i);
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/rate limit/i);
    expect(alert).not.toMatch(/\b429\b/);
    expect(alert).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(alert);
  });

});
