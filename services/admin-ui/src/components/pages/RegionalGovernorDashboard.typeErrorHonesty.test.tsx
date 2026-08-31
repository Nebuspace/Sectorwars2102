import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import RegionalGovernorDashboard from './RegionalGovernorDashboard';
import { api } from '../../utils/auth';
import { useAuth } from '../../contexts/AuthContext';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
  },
}));

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: vi.fn(),
}));

const region = {
  id: 'reg-1',
  name: 'sol',
  display_name: 'Sol Reach',
  owner_id: 'p1',
  subscription_tier: 'free',
  status: 'active',
  governance_type: 'autocracy',
  tax_rate: 0.1,
  voting_threshold: 0.51,
  economic_specialization: '',
  total_sectors: 12,
  active_players_30d: 4,
  total_trade_volume: 0,
  starting_credits: 1000,
  starting_ship: 'basic',
  language_pack: {},
  aesthetic_theme: {},
  trade_bonuses: {},
};

/**
 * LEG-3483 Soft-ORDER — RegionalGovernorDashboard TypeError/Network Error honesty densify.
 */
describe('RegionalGovernorDashboard typeErrorHonesty densify (LEG-3483)', () => {
  beforeEach(() => {
    vi.mocked(useAuth).mockReturnValue({ user: { is_admin: false } } as ReturnType<
      typeof useAuth
    >);
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on regional stats load to honest fallback', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url === '/api/v1/regions/my-region') return { data: region };
      if (url === '/api/v1/regions/my-region/stats') {
        throw new Error('Network Error');
      }
      if (url.endsWith('/policies')) return { data: [] };
      if (url.endsWith('/elections')) return { data: [] };
      if (url.endsWith('/treaties')) return { data: [] };
      if (url.endsWith('/members')) return { data: [] };
      return { data: {} };
    });

    render(<RegionalGovernorDashboard />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(
        /Failed to load regional stats/i,
      );
    });
    const msg = screen.getByRole('alert').textContent ?? '';
    expect(msg).not.toBe('Network Error');
    expect(msg).not.toContain('Network Error');
    expect(msg).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on regional stats load to honest fallback', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url === '/api/v1/regions/my-region') return { data: region };
      if (url === '/api/v1/regions/my-region/stats') {
        throw new TypeError('Failed to fetch');
      }
      if (url.endsWith('/policies')) return { data: [] };
      if (url.endsWith('/elections')) return { data: [] };
      if (url.endsWith('/treaties')) return { data: [] };
      if (url.endsWith('/members')) return { data: [] };
      return { data: {} };
    });

    render(<RegionalGovernorDashboard />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(
        /Failed to load regional stats/i,
      );
    });
    const msg = screen.getByRole('alert').textContent ?? '';
    expect(msg).not.toMatch(/TypeError/i);
    expect(msg).not.toMatch(/Failed to fetch/i);
  });
});
