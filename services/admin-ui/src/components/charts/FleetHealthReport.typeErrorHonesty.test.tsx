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

/**
 * LEG-3542 Soft-ORDER — axios-shaped Network Error densify (invent=0).
 * Tip already collapses via formatAdminApiError; assert Error('Network Error') /
 * Error('Failed to fetch') never leak raw transport text.
 */
describe('FleetHealthReport axios Network Error densify (LEG-3542)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios-shaped Network Error to gameserver-unreachable fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<FleetHealthReport />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(
      /Gameserver unreachable — network error fetching fleet health report/i,
    );
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
    expect(alert).not.toMatch(/TypeError/i);
  });

  it('collapses Error Failed to fetch to gameserver-unreachable fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Failed to fetch'));

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
