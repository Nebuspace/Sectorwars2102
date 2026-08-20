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
});
