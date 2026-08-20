import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import PlanetDetailModal from './PlanetDetailModal';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    patch: vi.fn(),
  },
}));

const planet = {
  id: 'p1',
  name: 'Terra',
  sector_id: 's1',
  planet_type: 'TERRAN',
  population: 100,
  max_population: 1000,
  defense_level: 1,
  habitability_score: 50,
  resource_richness: 1,
  gravity: 1,
};

describe('PlanetDetailModal scope errors (LEG-1214)', () => {
  beforeEach(() => {
    vi.mocked(api.patch).mockReset();
  });

  it('surfaces admin.universe.manage on save 403', async () => {
    vi.mocked(api.patch).mockRejectedValue(
      Object.assign(new Error('HTTP 403'), { response: { status: 403, data: {} } }),
    );

    render(
      <PlanetDetailModal
        isOpen
        planet={planet as any}
        onClose={() => {}}
        mode="edit"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => {
      expect(screen.getByText(/admin\.universe\.manage|Access denied/i)).toBeTruthy();
    });
  });

  it('surfaces rate-limit on save 429', async () => {
    vi.mocked(api.patch).mockRejectedValue(
      Object.assign(new Error('HTTP 429'), { response: { status: 429, data: {} } }),
    );

    render(
      <PlanetDetailModal
        isOpen
        planet={planet as any}
        onClose={() => {}}
        mode="edit"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
  });
});
