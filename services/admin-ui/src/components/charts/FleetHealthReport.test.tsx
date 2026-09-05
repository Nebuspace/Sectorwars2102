import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import FleetHealthReport from './FleetHealthReport';
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

describe('FleetHealthReport scope honesty (LEG-1208)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('reports 403 as PLAYERS_VIEW denial, not bare Failed', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<FleetHealthReport />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/PLAYERS_VIEW|Access denied/i);
    expect(alert).not.toBe('Failed to load fleet health report');
  });

  it('reports 429 as admin rate-limit', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<FleetHealthReport />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    expect(screen.getByRole('alert').textContent ?? '').toMatch(/rate limit/i);
  });

  it('reports TypeError Failed to fetch as gameserver-unreachable fallback, not raw TypeError', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<FleetHealthReport />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(
      /Gameserver unreachable — network error fetching fleet health report/i
    );
    expect(alert).not.toMatch(/Failed to fetch/i);
    expect(alert).not.toMatch(/TypeError/i);
  });
});
