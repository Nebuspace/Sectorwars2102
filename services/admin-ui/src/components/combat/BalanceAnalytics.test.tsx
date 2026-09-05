import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import BalanceAnalytics from './BalanceAnalytics';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

const axiosError = (status: number) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status },
  });

describe('BalanceAnalytics (LEG-1099 scope errors)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('reports a 403 as a scope problem, not gameserver-down', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<BalanceAnalytics />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/PLAYERS_VIEW|players view|Access denied/i);
    expect(alert).not.toMatch(/gameserver is running/i);
  });

  it('reports a 429 as an admin rate-limit, not a generic load failure', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<BalanceAnalytics />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/rate limit/i);
    expect(alert).not.toContain('HTTP 429');
  });

  it('reports TypeError Failed to fetch as balance-analytics fallback, not raw TypeError', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<BalanceAnalytics />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(
      /Failed to load balance analytics\. Please check if the gameserver is running\./i
    );
    expect(alert).not.toMatch(/Failed to fetch/i);
    expect(alert).not.toMatch(/TypeError/i);
  });

  it('collapses axios-shaped Network Error to balance-analytics fallback (LEG-3334)', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<BalanceAnalytics />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(
      /Failed to load balance analytics\. Please check if the gameserver is running\./i
    );
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
    expect(alert).not.toContain('Failed to fetch');
    expect(alert).not.toContain('TypeError');
  });

  it('collapses non-TypeError Failed to fetch to balance-analytics fallback (LEG-3334)', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Failed to fetch'));

    render(<BalanceAnalytics />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(
      /Failed to load balance analytics\. Please check if the gameserver is running\./i
    );
    expect(alert).not.toBe('Failed to fetch');
    expect(alert).not.toContain('Failed to fetch');
    expect(alert).not.toContain('Network Error');
    expect(alert).not.toContain('TypeError');
  });
});
