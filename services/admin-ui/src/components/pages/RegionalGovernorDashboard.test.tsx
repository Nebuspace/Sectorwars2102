import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
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

function httpErr(status: number, detail?: string) {
  return Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });
}

const member = {
  player_id: 'p1',
  username: 'alice',
  membership_type: 'citizen',
  reputation_score: 100,
  local_rank: '',
  voting_power: 1.5,
  joined_at: '2026-01-01T00:00:00Z',
  last_visit: '2026-01-02T00:00:00Z',
  total_visits: 3,
};

function mockRegionalGets(includeMembers = false) {
  vi.mocked(api.get).mockImplementation(async (url: string) => {
    if (url === '/api/v1/regions/my-region') return { data: region };
    if (url.endsWith('/stats')) return { data: {} };
    if (url.endsWith('/policies')) return { data: [] };
    if (url.endsWith('/elections')) return { data: [] };
    if (url.endsWith('/treaties')) return { data: [] };
    if (url.endsWith('/members')) return { data: includeMembers ? [member] : [] };
    return { data: {} };
  });
}

describe('RegionalGovernorDashboard (LEG-213)', () => {
  beforeEach(() => {
    vi.mocked(useAuth).mockReturnValue({ user: { is_admin: false } } as any);
    vi.mocked(api.get).mockReset();
    vi.mocked(api.put).mockReset();
    vi.mocked(api.post).mockReset();
    vi.mocked(api.patch).mockReset();
    mockRegionalGets(false);
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

  it('shows scope-aware copy on 403 load of my-region', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url === '/api/v1/regions/my-region') throw httpErr(403);
      if (url.endsWith('/stats')) return { data: {} };
      if (url.endsWith('/policies')) return { data: [] };
      if (url.endsWith('/elections')) return { data: [] };
      if (url.endsWith('/treaties')) return { data: [] };
      if (url.endsWith('/members')) return { data: [] };
      return { data: {} };
    });

    render(<RegionalGovernorDashboard />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/admin\.regions|region owner/i);
    });
  });

  it('surfaces scope denial on 403 stats load (LEG-2747)', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url === '/api/v1/regions/my-region') return { data: region };
      if (url === '/api/v1/regions/my-region/stats') {
        throw httpErr(403, 'Missing scope admin.regions.view');
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
        /Missing scope admin\.regions\.view/i,
      );
    });
    expect(screen.queryByText('Failed to load regional stats')).toBeNull();
  });

  it('shows rate-limit copy on 429 stats load (LEG-2747)', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url === '/api/v1/regions/my-region') return { data: region };
      if (url === '/api/v1/regions/my-region/stats') {
        throw httpErr(429);
      }
      if (url.endsWith('/policies')) return { data: [] };
      if (url.endsWith('/elections')) return { data: [] };
      if (url.endsWith('/treaties')) return { data: [] };
      if (url.endsWith('/members')) return { data: [] };
      return { data: {} };
    });

    render(<RegionalGovernorDashboard />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/rate limit/i);
    });
    expect(screen.queryByText('Failed to load regional stats')).toBeNull();
  });

  it('surfaces honest fallback on stats load TypeError/network collapse (LEG-3032)', async () => {
    // Outer loadRegionalData 'Failed to load regional data' catch is unreachable when
    // per-loader try/catch swallows — densify the live stats path (invent=0).
    vi.spyOn(console, 'error').mockImplementation(() => {});
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
      expect(screen.getByRole('alert').textContent).toMatch(/Failed to load regional stats/i);
    });
    const msg = screen.getByRole('alert').textContent ?? '';
    expect(msg).not.toMatch(/Failed to fetch/i);
    expect(msg).not.toMatch(/TypeError/i);
  });

  it('shows admin rate-limit copy on 429 economy save', async () => {
    render(<RegionalGovernorDashboard />);
    await waitFor(() => expect(screen.getByText('Sol Reach')).toBeTruthy());

    vi.mocked(api.put).mockRejectedValueOnce(httpErr(429));

    fireEvent.click(screen.getByRole('button', { name: /economy/i }));

    const saveBtn = await waitFor(() => {
      const buttons = screen.getAllByRole('button');
      const match = buttons.find((b) => /save|update/i.test(b.textContent || ''));
      if (!match) throw new Error('save button not found');
      return match;
    });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/rate limit/i);
    });
  });

  it('shows scope-aware copy on 403 economy save', async () => {
    render(<RegionalGovernorDashboard />);
    await waitFor(() => expect(screen.getByText('Sol Reach')).toBeTruthy());

    vi.mocked(api.put).mockRejectedValueOnce(httpErr(403));

    fireEvent.click(screen.getByRole('button', { name: /economy/i }));

    const saveBtn = await waitFor(() => {
      const buttons = screen.getAllByRole('button');
      const match = buttons.find((b) => /save|update/i.test(b.textContent || ''));
      if (!match) throw new Error('save button not found');
      return match;
    });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(
        /region owner or admin\.regions scope|Access denied/i,
      );
    });
  });

  it('shows scope-aware copy on 403 governance save (LEG-2783)', async () => {
    render(<RegionalGovernorDashboard />);
    await waitFor(() => expect(screen.getByText('Sol Reach')).toBeTruthy());

    vi.mocked(api.put).mockRejectedValueOnce(httpErr(403));

    fireEvent.click(screen.getByRole('button', { name: /governance/i }));
    fireEvent.click(screen.getByRole('button', { name: /update governance/i }));

    await waitFor(() => {
      expect(vi.mocked(api.put)).toHaveBeenCalledWith(
        '/api/v1/regions/my-region/governance',
        expect.objectContaining({ governance_type: expect.any(String) }),
      );
    });
    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(
        /region owner or admin\.regions scope|Access denied/i,
      );
    });
  });

  it('shows admin rate-limit copy on 429 governance save (LEG-2783)', async () => {
    render(<RegionalGovernorDashboard />);
    await waitFor(() => expect(screen.getByText('Sol Reach')).toBeTruthy());

    vi.mocked(api.put).mockRejectedValueOnce(httpErr(429));

    fireEvent.click(screen.getByRole('button', { name: /governance/i }));
    fireEvent.click(screen.getByRole('button', { name: /update governance/i }));

    await waitFor(() => {
      expect(vi.mocked(api.put)).toHaveBeenCalledWith(
        '/api/v1/regions/my-region/governance',
        expect.objectContaining({ governance_type: expect.any(String) }),
      );
    });
    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/rate limit/i);
    });
  });

  it('shows scope-aware copy on 403 createPolicy (LEG-2893)', async () => {
    render(<RegionalGovernorDashboard />);
    await waitFor(() => expect(screen.getByText('Sol Reach')).toBeTruthy());

    vi.mocked(api.post).mockRejectedValueOnce(httpErr(403));

    fireEvent.click(screen.getByRole('button', { name: 'Policies' }));
    fireEvent.click(screen.getAllByRole('button', { name: 'Create Policy' })[0]!);
    fireEvent.click(screen.getAllByRole('button', { name: 'Create Policy' })[1]!);

    await waitFor(() => {
      expect(vi.mocked(api.post)).toHaveBeenCalledWith(
        '/api/v1/regions/my-region/policies',
        expect.objectContaining({ policy_type: expect.any(String) }),
      );
    });
    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(
        /region owner or admin\.regions scope|Access denied/i,
      );
    });
    expect(screen.queryByText('Failed to create policy')).toBeNull();
  });

  it('shows admin rate-limit copy on 429 createPolicy (LEG-2893)', async () => {
    render(<RegionalGovernorDashboard />);
    await waitFor(() => expect(screen.getByText('Sol Reach')).toBeTruthy());

    vi.mocked(api.post).mockRejectedValueOnce(httpErr(429));

    fireEvent.click(screen.getByRole('button', { name: 'Policies' }));
    fireEvent.click(screen.getAllByRole('button', { name: 'Create Policy' })[0]!);
    fireEvent.click(screen.getAllByRole('button', { name: 'Create Policy' })[1]!);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/rate limit/i);
    });
    expect(screen.queryByText('Failed to create policy')).toBeNull();
  });

  it('shows scope-aware copy on 403 updateMemberDials (LEG-2893)', async () => {
    mockRegionalGets(true);
    render(<RegionalGovernorDashboard />);
    await waitFor(() => expect(screen.getByText('Sol Reach')).toBeTruthy());

    fireEvent.click(screen.getByRole('button', { name: 'Members' }));
    await waitFor(() => expect(screen.getByText('alice')).toBeTruthy());

    vi.mocked(api.patch).mockRejectedValueOnce(httpErr(403));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(vi.mocked(api.patch)).toHaveBeenCalledWith(
        '/api/v1/regions/my-region/members/p1',
        expect.objectContaining({ voting_power: expect.any(Number) }),
      );
    });
    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(
        /region owner or admin\.regions scope|Access denied/i,
      );
    });
    expect(screen.queryByText('Failed to update member dials')).toBeNull();
  });

  it('shows admin rate-limit copy on 429 updateMemberDials (LEG-2893)', async () => {
    mockRegionalGets(true);
    render(<RegionalGovernorDashboard />);
    await waitFor(() => expect(screen.getByText('Sol Reach')).toBeTruthy());

    fireEvent.click(screen.getByRole('button', { name: 'Members' }));
    await waitFor(() => expect(screen.getByText('alice')).toBeTruthy());

    vi.mocked(api.patch).mockRejectedValueOnce(httpErr(429));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/rate limit/i);
    });
    expect(screen.queryByText('Failed to update member dials')).toBeNull();
  });

  it('shows scope-aware copy on 403 startElection (LEG-2893)', async () => {
    render(<RegionalGovernorDashboard />);
    await waitFor(() => expect(screen.getByText('Sol Reach')).toBeTruthy());

    vi.mocked(api.post).mockRejectedValueOnce(httpErr(403));

    fireEvent.click(screen.getByRole('button', { name: 'Elections' }));
    fireEvent.click(screen.getByRole('button', { name: /Start Governor Election/i }));

    await waitFor(() => {
      expect(vi.mocked(api.post)).toHaveBeenCalledWith(
        '/api/v1/regions/my-region/elections',
        expect.objectContaining({ position: 'governor' }),
      );
    });
    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(
        /region owner or admin\.regions scope|Access denied/i,
      );
    });
    expect(screen.queryByText('Failed to start election')).toBeNull();
  });

  it('shows admin rate-limit copy on 429 startElection (LEG-2893)', async () => {
    render(<RegionalGovernorDashboard />);
    await waitFor(() => expect(screen.getByText('Sol Reach')).toBeTruthy());

    vi.mocked(api.post).mockRejectedValueOnce(httpErr(429));

    fireEvent.click(screen.getByRole('button', { name: 'Elections' }));
    fireEvent.click(screen.getByRole('button', { name: /Start Governor Election/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/rate limit/i);
    });
    expect(screen.queryByText('Failed to start election')).toBeNull();
  });

  it('collapses axios-shaped Network Error on regional stats load (LEG-3356)', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
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
      expect(screen.getByRole('alert').textContent).toMatch(/Failed to load regional stats/i);
    });
    const msg = screen.getByRole('alert').textContent ?? '';
    expect(msg).not.toBe('Network Error');
    expect(msg).not.toContain('Network Error');
    expect(msg).not.toMatch(/TypeError/i);
  });

  it('collapses axios-shaped Network Error on economy save mutation (LEG-3356)', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<RegionalGovernorDashboard />);
    await waitFor(() => expect(screen.getByText('Sol Reach')).toBeTruthy());

    vi.mocked(api.put).mockRejectedValueOnce(new Error('Network Error'));

    fireEvent.click(screen.getByRole('button', { name: /economy/i }));

    const saveBtn = await waitFor(() => {
      const buttons = screen.getAllByRole('button');
      const match = buttons.find((b) => /save|update/i.test(b.textContent || ''));
      if (!match) throw new Error('save button not found');
      return match;
    });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Failed to update economic configuration/i);
    });
    const msg = screen.getByRole('alert').textContent ?? '';
    expect(msg).not.toBe('Network Error');
    expect(msg).not.toContain('Network Error');
    expect(msg).not.toMatch(/TypeError/i);
  });
});

describe('BeaconSectorCap (LEG-1014)', () => {
  beforeEach(() => {
    vi.mocked(useAuth).mockReturnValue({ user: { is_admin: true } } as any);
    vi.mocked(api.get).mockReset();
    vi.mocked(api.patch).mockReset();
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url === '/api/v1/regions/my-region') return { data: region };
      if (url.endsWith('/stats')) return { data: {} };
      if (url.endsWith('/policies')) return { data: [] };
      if (url.endsWith('/elections')) return { data: [] };
      if (url.endsWith('/treaties')) return { data: [] };
      if (url.endsWith('/members')) return { data: [] };
      if (url.endsWith('/beacon-sector-cap')) return { data: { region_id: 'reg-1', beacon_sector_cap: 15, default_cap: 10, max_cap: 50, configured_raw: 15 } };
      return { data: {} };
    });
  });

  it('fetches beacon-sector-cap for admin after region loads', async () => {
    render(<RegionalGovernorDashboard />);
    await waitFor(() => {
      const calls = vi.mocked(api.get).mock.calls.map(([u]) => String(u));
      expect(calls.some(u => u.endsWith('/beacon-sector-cap'))).toBe(true);
    });
  });

  it('calls PATCH beacon-sector-cap on save', async () => {
    vi.mocked(api.patch).mockResolvedValue({ data: { region_id: 'reg-1', beacon_sector_cap: 20, default_cap: 10, max_cap: 50, configured_raw: 20 } });
    render(<RegionalGovernorDashboard />);
    await waitFor(() => screen.getByText('Sol Reach'));
    const econTab = screen.getAllByText('Economy').find(el => el.className.includes('tab-button'));
    if (econTab) fireEvent.click(econTab);
    const btn = await waitFor(() => screen.getByText('Save Cap'));
    fireEvent.click(btn);
    await waitFor(() => {
      expect(vi.mocked(api.patch)).toHaveBeenCalledWith(
        expect.stringContaining('/beacon-sector-cap'),
        expect.objectContaining({ beacon_sector_cap: expect.any(Number) })
      );
    });
  });

  it('surfaces formatAdminApiError on beacon PATCH 403 (LEG-2601)', async () => {
    vi.mocked(api.patch).mockRejectedValue(
      httpErr(403, 'Missing scope admin.regions.manage'),
    );
    render(<RegionalGovernorDashboard />);
    await waitFor(() => screen.getByText('Sol Reach'));
    const econTab = screen.getAllByText('Economy').find(el => el.className.includes('tab-button'));
    if (econTab) fireEvent.click(econTab);
    fireEvent.click(await waitFor(() => screen.getByText('Save Cap')));

    await waitFor(() => {
      expect(vi.mocked(api.patch)).toHaveBeenCalledWith(
        expect.stringContaining('/beacon-sector-cap'),
        expect.objectContaining({ beacon_sector_cap: expect.any(Number) }),
      );
    });
    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Missing scope admin\.regions\.manage/i);
    });
  });

  it('surfaces rate-limit copy on beacon PATCH 429 (LEG-2601)', async () => {
    vi.mocked(api.patch).mockRejectedValue(httpErr(429));
    render(<RegionalGovernorDashboard />);
    await waitFor(() => screen.getByText('Sol Reach'));
    const econTab = screen.getAllByText('Economy').find(el => el.className.includes('tab-button'));
    if (econTab) fireEvent.click(econTab);
    fireEvent.click(await waitFor(() => screen.getByText('Save Cap')));

    await waitFor(() => {
      expect(vi.mocked(api.patch)).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/rate limit/i);
    });
  });
});

describe('RegionalGovernorDashboard region terminate (LEG-3206)', () => {
  beforeEach(() => {
    vi.mocked(useAuth).mockReturnValue({ user: { is_admin: true } } as any);
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    mockRegionalGets(false);
  });

  it('shows admin danger zone and posts terminate with reason + confirm header', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url === '/api/v1/regions/my-region') return { data: region };
      if (url.endsWith('/stats')) return { data: {} };
      if (url.endsWith('/policies')) return { data: [] };
      if (url.endsWith('/elections')) return { data: [] };
      if (url.endsWith('/treaties')) return { data: [] };
      if (url.endsWith('/members')) return { data: [] };
      if (url.includes('/terminate-preview')) {
        return {
          data: {
            regionId: region.id,
            regionName: region.name,
            displayName: region.display_name,
            status: 'active',
            regionType: 'player_owned',
            planetCount: 1,
            stationCount: 0,
            sectorCount: 12,
            playerStakeholderCount: 0,
            terminable: true,
          },
        };
      }
      if (url === '/api/v1/admin/regions') return { data: { regions: [region] } };
      return { data: {} };
    });
    vi.mocked(api.post).mockResolvedValue({ data: { success: true } });

    render(<RegionalGovernorDashboard />);
    await waitFor(() => screen.getByText('Sol Reach'));

    fireEvent.click(screen.getByRole('button', { name: 'Terminate Region…' }));
    await waitFor(() => screen.getByLabelText(/Type the region name/i));

    fireEvent.change(screen.getByLabelText(/Type the region name/i), {
      target: { value: region.name },
    });
    fireEvent.change(screen.getByLabelText(/Reason \(required/i), {
      target: { value: 'nonpayment' },
    });
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Terminate Region' }));

    await waitFor(() => {
      expect(vi.mocked(api.post)).toHaveBeenCalledWith(
        `/api/v1/admin/regions/${region.id}/terminate`,
        { reason: 'nonpayment' },
        expect.objectContaining({
          headers: expect.objectContaining({ 'X-Confirm-Region-Name': region.name }),
        }),
      );
    });
  });

  it('surfaces scope denial on terminate POST 403', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url === '/api/v1/regions/my-region') return { data: region };
      if (url.endsWith('/stats')) return { data: {} };
      if (url.endsWith('/policies')) return { data: [] };
      if (url.endsWith('/elections')) return { data: [] };
      if (url.endsWith('/treaties')) return { data: [] };
      if (url.endsWith('/members')) return { data: [] };
      if (url.includes('/terminate-preview')) {
        return {
          data: {
            regionId: region.id,
            regionName: region.name,
            displayName: region.display_name,
            status: 'active',
            regionType: 'player_owned',
            planetCount: 0,
            stationCount: 0,
            sectorCount: 0,
            playerStakeholderCount: 0,
            terminable: true,
          },
        };
      }
      if (url === '/api/v1/admin/regions') return { data: { regions: [region] } };
      return { data: {} };
    });
    vi.mocked(api.post).mockRejectedValue(
      httpErr(403, 'Missing scope admin.regions.terminate'),
    );

    render(<RegionalGovernorDashboard />);
    await waitFor(() => screen.getByText('Sol Reach'));
    fireEvent.click(screen.getByRole('button', { name: 'Terminate Region…' }));
    await waitFor(() => screen.getByLabelText(/Type the region name/i));

    fireEvent.change(screen.getByLabelText(/Type the region name/i), {
      target: { value: region.name },
    });
    fireEvent.change(screen.getByLabelText(/Reason \(required/i), {
      target: { value: 'test' },
    });
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Terminate Region' }));

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Missing scope admin\.regions\.terminate/i);
    });
  });
});
