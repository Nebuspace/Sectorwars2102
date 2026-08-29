import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PlanetaryManagement } from './PlanetaryManagement';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

vi.mock('react-chartjs-2', () => ({
  Bar: () => <div data-testid="bar-chart" />,
  Radar: () => <div data-testid="radar-chart" />,
}));

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

const axiosError = (status: number) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status },
  });

describe('PlanetaryManagement (LEG-151)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('loads planetary data via shared api and hydrates without not-implemented copy', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: planetaryPayload });

    render(<PlanetaryManagement />);

    await waitFor(() => {
      expect(screen.getByText('Kepler Prime')).toBeTruthy();
    });

    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/colonization/planets');
    expect(screen.getByText('Planetary Management')).toBeTruthy();
    expect(screen.queryByText(/not implemented/i)).toBeNull();
  });

  it('reports a 403 as a scope problem, never as unimplemented', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<PlanetaryManagement />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/REGIONS_VIEW|regions view|Access denied/i);
    expect(alert).not.toContain('not implemented');
  });

  it('reports a 404 as a routing fault, never as an unbuilt endpoint', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(404));

    render(<PlanetaryManagement />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toContain('404');
    expect(alert).toMatch(/route not found|proxy/i);
    expect(alert).not.toContain('not implemented');
  });

  it('reports a 429 as an admin rate-limit, not bare HTTP 429 load failure', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<PlanetaryManagement />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/rate limit/i);
    expect(alert).not.toMatch(/Failed to load planetary data \(HTTP 429\)/);
  });

  it('surfaces honest fallback on non-RBAC network collapse (LEG-2948)', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<PlanetaryManagement />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Gameserver unreachable|network error fetching planetary/i);
    expect(alert).not.toMatch(/TypeError/i);
    expect(alert).not.toBe('Failed to fetch');
    expect(alert).not.toMatch(/Failed to load planetary data/i);
  });
});

describe('PlanetaryManagement force tick errors (LEG-2769)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.mocked(api.get).mockResolvedValue({ data: planetaryPayload });
  });

  async function openPlanetAndForceTick(user: ReturnType<typeof userEvent.setup>) {
    render(<PlanetaryManagement />);
    await waitFor(() => {
      expect(screen.getByText('Kepler Prime')).toBeTruthy();
    });

    await user.click(screen.getByText('Kepler Prime'));
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Force Production Tick' })).toBeTruthy();
    });

    await user.click(screen.getByRole('button', { name: 'Force Production Tick' }));
  }

  it('surfaces formatAdminApiError on force tick POST 403', async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockRejectedValue(axiosError(403));

    await openPlanetAndForceTick(user);

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/planets/pl-1/tick');
    });

    const alert = screen.getByRole('alert');
    expect(alert.textContent).toMatch(/GALAXY_MANAGE/i);
    expect(alert.textContent).not.toMatch(/HTTP 403/);
    expect(alert.textContent).not.toContain('not implemented');
  });

  it('shows rate-limit copy on force tick POST 429', async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockRejectedValue(axiosError(429));

    await openPlanetAndForceTick(user);

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/planets/pl-1/tick');
    });

    const alert = screen.getByRole('alert');
    expect(alert.textContent).toMatch(/rate limit/i);
    expect(alert.textContent).not.toMatch(/HTTP 429/);
  });

  it('surfaces honest fallback on force tick TypeError (LEG-2948)', async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockRejectedValue(new TypeError('Failed to fetch'));

    await openPlanetAndForceTick(user);

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/planets/pl-1/tick');
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Gameserver unreachable|network error advancing production/i);
    expect(alert).not.toMatch(/TypeError/i);
    expect(alert).not.toBe('Failed to fetch');
  });
});
