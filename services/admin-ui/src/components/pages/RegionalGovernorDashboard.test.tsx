import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import RegionalGovernorDashboard from './RegionalGovernorDashboard';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
  },
}));

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { is_admin: false } }),
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

describe('RegionalGovernorDashboard (LEG-213)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url === '/api/v1/regions/my-region') return { data: region };
      if (url.endsWith('/stats')) return { data: {} };
      if (url.endsWith('/policies')) return { data: [] };
      if (url.endsWith('/elections')) return { data: [] };
      if (url.endsWith('/treaties')) return { data: [] };
      if (url.endsWith('/members')) return { data: [] };
      return { data: {} };
    });
  });

  it('loads region surfaces via shared api with no raw Bearer fetch', async () => {
    render(<RegionalGovernorDashboard />);

    await waitFor(() => {
      expect(screen.getByText('Sol Reach')).toBeTruthy();
    });

    const urls = vi.mocked(api.get).mock.calls.map(([u]) => String(u));
    expect(urls).toEqual(
      expect.arrayContaining([
        '/api/v1/regions/my-region',
        '/api/v1/regions/my-region/stats',
        '/api/v1/regions/my-region/policies',
        '/api/v1/regions/my-region/elections',
        '/api/v1/regions/my-region/treaties',
        '/api/v1/regions/my-region/members',
      ])
    );
    expect(screen.getByText('Regional Governor Dashboard')).toBeTruthy();
  });
});
