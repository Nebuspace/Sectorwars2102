import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { ColonyOverview } from './ColonyOverview';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: { get: vi.fn() },
}));

vi.mock('../../hooks/useResourceCatalog', () => ({
  useResourceCatalog: () => ({
    catalog: [],
    loading: false,
    getLabel: (n: string) => n,
    getIcon: () => '📦',
  }),
}));

describe('ColonyOverview (LEG-212 shared api)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.get).mockResolvedValue({
      data: {
        colonies: [
          {
            id: 'c1',
            name: 'Outpost Alpha',
            planet_id: 'pl1',
            planet_name: 'Alpha',
            sector_id: 's1',
            sector_name: 'Sol',
            owner_id: 'p1',
            owner_name: 'Ada',
            population: 100,
            max_population: 500,
            morale: 70,
            habitability_score: 80,
            status: 'active',
            resources: { energy: 1, minerals: 2, food: 3, water: 4 },
            buildings: {},
          },
        ],
      },
    });
  });

  it('loads colonies via shared api', async () => {
    render(<ColonyOverview />);
    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/colonies');
    });
    expect(screen.getByText('Colony Overview')).toBeTruthy();
  });
  it('reports a 403 as colonization scope denial, never bare Failed to load colonies', async () => {
    vi.mocked(api.get).mockRejectedValue({ response: { status: 403 } });

    render(<ColonyOverview />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Access denied|colonization\.view|scope/i);
    });
    expect(screen.getByRole('alert').textContent).not.toContain('Failed to load colonies data');
  });

  it('reports a 429 as an admin rate-limit', async () => {
    vi.mocked(api.get).mockRejectedValue({ response: { status: 429 } });

    render(<ColonyOverview />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/rate limit/i);
    });
  });

  it('surfaces honest fallback on non-RBAC network collapse (LEG-2947)', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<ColonyOverview />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Failed to load colonies/i);
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses axios Network Error on overview load to honest fallback (LEG-3516)', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<ColonyOverview />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Failed to load colonies/i);
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toMatch(/Network Error/i);
  });
});
