import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { ColonizationManagement } from './ColonizationManagement';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

vi.mock('../../hooks/useResourceCatalog', () => ({
  useResourceCatalog: () => ({
    catalog: [],
    loading: false,
    getLabel: (n: string) => n,
    getIcon: () => '📦',
  }),
}));

vi.mock('react-chartjs-2', () => ({
  Line: () => <div data-testid="line-chart" />,
  Doughnut: () => <div data-testid="doughnut-chart" />,
  Bar: () => <div data-testid="bar-chart" />,
  Radar: () => <div data-testid="radar-chart" />,
}));

/**
 * LEG-3756 Soft-ORDER — ColonizationManagement tab-shell TypeError/Network Error densify.
 */
describe('ColonizationManagement typeErrorHonesty densify (LEG-3756)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on default Colony Overview tab load', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<ColonizationManagement />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Failed to load colonies/i);
    });

    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on default Colony Overview tab load', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<ColonizationManagement />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Failed to load colonies/i);
    });

    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses transport errors when switching to Production Monitoring tab', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<ColonizationManagement />);

    fireEvent.click(screen.getByRole('button', { name: /Production Monitoring/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Gameserver unreachable|network error fetching production/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });
});
