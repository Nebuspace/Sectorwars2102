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
  created_at: '2026-01-01T00:00:00Z',
};

/**
 * LEG-3701 Soft-ORDER — PlanetDetailModal TypeError/Network Error honesty densify.
 */
describe('PlanetDetailModal typeErrorHonesty densify (LEG-3701)', () => {
  beforeEach(() => {
    vi.mocked(api.patch).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on planet save without leaking raw transport text', async () => {
    vi.mocked(api.patch).mockRejectedValue(new Error('Network Error'));

    render(
      <PlanetDetailModal
        isOpen
        planet={planet as any}
        onClose={() => {}}
        mode="edit"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => {
      expect(screen.getByText(/Failed to save planet changes/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to save planet changes/i).textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on planet save without leaking transport text', async () => {
    vi.mocked(api.patch).mockRejectedValue(new TypeError('Failed to fetch'));

    render(
      <PlanetDetailModal
        isOpen
        planet={planet as any}
        onClose={() => {}}
        mode="edit"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => {
      expect(screen.getByText(/Failed to save planet changes/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to save planet changes/i).textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });
});
