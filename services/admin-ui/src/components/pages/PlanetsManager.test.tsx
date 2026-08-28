import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import PlanetsManager from './PlanetsManager';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

vi.mock('../universe/PlanetDetailModal', () => ({
  default: () => null,
}));

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

const samplePlanet = {
  id: 'p1',
  name: 'Test Planet',
  sector_id: 's1',
  sector_name: 'Alpha',
  planet_type: 'terran',
  population: 100,
  max_population: 1000,
  defense_level: 10,
  created_at: '2026-01-01T00:00:00Z',
};

describe('PlanetsManager scope errors (LEG-966)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.delete).mockReset();
  });

  it('surfaces scope denial on 403 load', async () => {
    vi.mocked(api.get).mockRejectedValue(
      axiosError(403, 'Missing scope admin.universe.planets'),
    );

    render(<PlanetsManager />);

    await waitFor(() => {
      expect(screen.getByText(/Missing scope admin\.universe\.planets/i)).toBeTruthy();
    });
  });

  it('shows rate-limit copy on 429 load', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<PlanetsManager />);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
  });
});

describe('PlanetsManager delete mutation errors (LEG-2618)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.delete).mockReset();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    vi.mocked(api.get).mockResolvedValue({
      data: { planets: [samplePlanet], total_count: 1 },
    });
  });

  async function readyPlanetList() {
    render(<PlanetsManager />);
    await waitFor(() => {
      expect(screen.getByText('Test Planet')).toBeTruthy();
    });
  }

  it('surfaces formatAdminApiError on delete DELETE 403', async () => {
    vi.mocked(api.delete).mockRejectedValue(axiosError(403));
    await readyPlanetList();

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() => {
      expect(api.delete).toHaveBeenCalledWith('/api/v1/admin/planets/p1');
    });
    const errorText = screen.getByText(/admin universe planet management scopes required|Access denied/i)
      .textContent;
    expect(errorText).toBeTruthy();
    expect(screen.queryByText(/Failed to delete planet "Test Planet"/)).toBeNull();
  });

  it('surfaces rate-limit copy on delete DELETE 429', async () => {
    vi.mocked(api.delete).mockRejectedValue(axiosError(429));
    await readyPlanetList();

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() => {
      expect(api.delete).toHaveBeenCalled();
    });
    expect(screen.getByText(/rate limit/i)).toBeTruthy();
    expect(screen.queryByText(/Failed to delete planet "Test Planet"/)).toBeNull();
  });
});
