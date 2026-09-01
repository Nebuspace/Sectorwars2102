import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import FleetHealthReport from './FleetHealthReport';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

/**
 * LEG-3162 Soft-ORDER — FleetHealthReport TypeError/network honesty.
 * formatAdminApiError collapses fetch TypeError to gameserver-unreachable fallback.
 */
describe('FleetHealthReport TypeError densify (LEG-3162)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('reports TypeError Failed to fetch as gameserver-unreachable fallback, not raw TypeError', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<FleetHealthReport />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(
      /Gameserver unreachable — network error fetching fleet health report/i,
    );
    expect(alert).not.toMatch(/Failed to fetch/i);
    expect(alert).not.toMatch(/TypeError/i);
  });
});
