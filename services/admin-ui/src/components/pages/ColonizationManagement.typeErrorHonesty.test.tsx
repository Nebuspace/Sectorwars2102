import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ColonizationManagement } from './ColonizationManagement';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
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

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
}

const planetaryPayload = {
  planets: [
    {
      id: 'pl-1',
      name: 'Kepler Prime',
      sectorId: 'sec-1',
      sectorName: 'Nexus',
      type: 'Terran',
      size: 'Medium',
      atmosphere: 'Breathable',
      temperature: 18,
      gravity: 1.0,
      resources: { energy: 10, minerals: 20, water: 30, rareMaterials: 1 },
      habitability: 72,
      population: 1000,
      maxPopulation: 5000,
      colonies: 1,
      infrastructure: { spaceports: 1, defenses: 2, factories: 1, research: 1 },
      ownership: {
        playerId: 'p1',
        playerName: 'Colonist',
        contested: false,
      },
      discovered: true,
      colonizable: false,
      hasGenesisDevice: false,
    },
  ],
  stats: {
    totalPlanets: 1,
    discoveredPlanets: 1,
    colonizedPlanets: 1,
    contestedPlanets: 0,
    totalPopulation: 1000,
    averageHabitability: 72,
    resourceDistribution: {
      energy: 10,
      minerals: 20,
      water: 30,
      rareMaterials: 1,
    },
  },
  terraformingProjects: [],
};

/**
 * LEG-3756 Soft-ORDER — ColonizationManagement tab-shell TypeError/Network Error densify.
 */
describe('ColonizationManagement typeErrorHonesty densify (LEG-3756)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
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

  it('surfaces 403 with colonization scope hint on default Colony Overview tab load', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<ColonizationManagement />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Access denied|colonization\.view|scope/i);
    expect(text).not.toMatch(/HTTP 403/i);
    expect(text).not.toContain('Failed to load colonies data');
    assertNoTransportLeak(text);
  });

  it('surfaces 429 as admin rate-limit copy on default Colony Overview tab load', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<ColonizationManagement />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/rate limit/i);
    expect(text).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(text);
  });

  it('surfaces formatAdminApiError-friendly copy on Planetary Management force tick POST 403', async () => {
    const user = userEvent.setup();
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/colonization/planets')) {
        return { data: planetaryPayload };
      }
      throw axiosError(404);
    });
    vi.mocked(api.post).mockRejectedValue(axiosError(403));

    render(<ColonizationManagement />);

    await user.click(screen.getByRole('button', { name: /Planetary Management/i }));

    await waitFor(() => {
      expect(screen.getByText('Kepler Prime')).toBeTruthy();
    });

    await user.click(screen.getByText('Kepler Prime'));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Force Production Tick' })).toBeTruthy();
    });

    await user.click(screen.getByRole('button', { name: 'Force Production Tick' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/planets/pl-1/tick');
    });

    const alert = screen.getByRole('alert');
    expect(alert.textContent).toMatch(/GALAXY_MANAGE|Access denied/i);
    expect(alert.textContent).not.toMatch(/HTTP 403/i);
    expect(alert.textContent).not.toContain('not implemented');
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
