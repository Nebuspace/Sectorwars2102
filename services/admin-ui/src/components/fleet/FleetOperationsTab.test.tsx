import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import FleetOperationsTab from './FleetOperationsTab';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

const mockStats = {
  total_fleets: 1,
  active_fleets: 1,
  fleets_in_battle: 1,
  total_ships_in_fleets: 10,
  total_firepower: 1000,
  average_fleet_size: 10,
  battles_today: 1,
  battles_this_week: 1,
  most_powerful_fleet: {
    id: 'fleet-1',
    name: 'Strike Fleet',
    team: 'Team Alpha',
    firepower: 1000,
  },
  largest_fleet: null,
};

const mockFleet = {
  id: 'fleet-1',
  team_id: 'team-1',
  team_name: 'Team Alpha',
  name: 'Strike Fleet',
  status: 'active',
  formation: 'wedge',
  total_ships: 10,
  total_firepower: 1000,
  total_shields: 500,
  total_hull: 800,
  average_speed: 5,
  morale: 75,
  supply_level: 90,
  commander_id: 'cmd-1',
  commander_name: 'Admiral',
  sector_id: 'sec-1',
  sector_name: 'Sol',
  member_count: 3,
  created_at: '2026-01-01T00:00:00Z',
  last_battle: null,
};

const mockActiveBattle = {
  id: 'battle-1',
  phase: 'combat',
  started_at: '2026-08-28T00:00:00Z',
  ended_at: null,
  attacker_fleet_id: 'fleet-1',
  attacker_fleet_name: 'Strike Fleet',
  attacker_team_name: 'Team Alpha',
  defender_fleet_id: 'fleet-2',
  defender_fleet_name: 'Defense Fleet',
  defender_team_name: 'Team Beta',
  sector_id: 'sec-1',
  sector_name: 'Sol',
  attacker_ships_initial: 10,
  defender_ships_initial: 8,
  attacker_ships_destroyed: 1,
  defender_ships_destroyed: 2,
  attacker_ships_retreated: 0,
  defender_ships_retreated: 0,
  total_damage_dealt: 500,
  winner: null,
  credits_looted: 0,
  duration: null,
};

function mockLoad() {
  vi.mocked(api.get).mockImplementation((url: string) => {
    if (url === '/api/v1/admin/fleets/stats') {
      return Promise.resolve({ data: mockStats });
    }
    if (url === '/api/v1/admin/fleets/') {
      return Promise.resolve({ data: [mockFleet] });
    }
    if (url === '/api/v1/admin/fleets/battles') {
      return Promise.resolve({ data: [mockActiveBattle] });
    }
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
}

describe('FleetOperationsTab scope errors', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
  });

  it('reports all-reject 403 as PLAYERS_VIEW, not generic Failed', async () => {
    vi.mocked(api.get).mockRejectedValue({ response: { status: 403 } });
    render(<FleetOperationsTab />);
    await waitFor(() => {
      expect(document.body.textContent).toMatch(/PLAYERS_VIEW/);
    });
    expect(document.body.textContent).not.toContain(
      'Failed to load fleet operations data.',
    );
  });

  it('reports all-reject 429 as admin rate-limit', async () => {
    vi.mocked(api.get).mockRejectedValue({ response: { status: 429 } });
    render(<FleetOperationsTab />);
    await waitFor(() => {
      expect(document.body.textContent).toMatch(/rate limit/i);
    });
  });
});

describe('FleetOperationsTab battle intervene POST fleetActError (LEG-2642)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    mockLoad();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  async function submitIntervention(user: ReturnType<typeof userEvent.setup>) {
    render(<FleetOperationsTab />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Intervene' })).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: 'Intervene' }));
    await user.type(
      screen.getByLabelText(/Reason \(min 10 characters\)/i),
      'Admin override for stuck battle',
    );
    await user.click(screen.getByRole('button', { name: 'Confirm Intervention' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/fleets/battles/battle-1/intervene',
        expect.objectContaining({
          action: 'end_battle',
          reason: 'Admin override for stuck battle',
        }),
      );
    });
  }

  it('surfaces COMBAT_INTERVENE copy on intervene POST 403', async () => {
    vi.mocked(api.post).mockRejectedValue(axiosError(403));
    const user = userEvent.setup();
    await submitIntervention(user);

    await waitFor(() => {
      expect(screen.getByText(/COMBAT_INTERVENE/i)).toBeInTheDocument();
    });
    expect(
      screen.queryByText('Failed to apply battle intervention.'),
    ).not.toBeInTheDocument();
  });

  it('surfaces rate-limit copy on intervene POST 429', async () => {
    vi.mocked(api.post).mockRejectedValue(axiosError(429));
    const user = userEvent.setup();
    await submitIntervention(user);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeInTheDocument();
    });
    expect(
      screen.queryByText('Failed to apply battle intervention.'),
    ).not.toBeInTheDocument();
  });
});

describe('FleetOperationsTab morale/dissolve mutation fleetActError (LEG-2643)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.patch).mockReset();
    vi.mocked(api.delete).mockReset();
    mockLoad();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  async function submitMorale(user: ReturnType<typeof userEvent.setup>) {
    render(<FleetOperationsTab />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Manage' })).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: 'Manage' }));
    await user.type(
      screen.getByLabelText(/Reason \(min 10 characters\)/i),
      'Morale correction for audit',
    );
    await user.click(screen.getByRole('button', { name: 'Confirm Morale Change' }));

    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledWith(
        '/api/v1/admin/fleets/fleet-1/morale',
        null,
        expect.objectContaining({
          params: expect.objectContaining({
            morale: 75,
            reason: 'Morale correction for audit',
          }),
        }),
      );
    });
  }

  async function submitDissolve(user: ReturnType<typeof userEvent.setup>) {
    render(<FleetOperationsTab />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Manage' })).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: 'Manage' }));
    await user.selectOptions(screen.getByLabelText('Action'), 'dissolve');
    await user.type(
      screen.getByLabelText(/Reason \(min 10 characters\)/i),
      'Dissolve abandoned fleet',
    );
    await user.click(screen.getByRole('button', { name: 'Confirm Dissolve' }));

    await waitFor(() => {
      expect(api.delete).toHaveBeenCalledWith(
        '/api/v1/admin/fleets/fleet-1/force-dissolve',
        expect.objectContaining({
          data: { reason: 'Dissolve abandoned fleet' },
        }),
      );
    });
  }

  it('surfaces COMBAT_INTERVENE copy on morale PATCH 403', async () => {
    vi.mocked(api.patch).mockRejectedValue(axiosError(403));
    const user = userEvent.setup();
    await submitMorale(user);

    await waitFor(() => {
      expect(screen.getByText(/COMBAT_INTERVENE/i)).toBeInTheDocument();
    });
    expect(screen.queryByText('Failed to adjust fleet morale.')).not.toBeInTheDocument();
  });

  it('surfaces rate-limit copy on morale PATCH 429', async () => {
    vi.mocked(api.patch).mockRejectedValue(axiosError(429));
    const user = userEvent.setup();
    await submitMorale(user);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeInTheDocument();
    });
    expect(screen.queryByText('Failed to adjust fleet morale.')).not.toBeInTheDocument();
  });

  it('surfaces COMBAT_INTERVENE copy on force-dissolve DELETE 403', async () => {
    vi.mocked(api.delete).mockRejectedValue(axiosError(403));
    const user = userEvent.setup();
    await submitDissolve(user);

    await waitFor(() => {
      expect(screen.getByText(/COMBAT_INTERVENE/i)).toBeInTheDocument();
    });
    expect(screen.queryByText('Failed to dissolve fleet.')).not.toBeInTheDocument();
  });

  it('surfaces rate-limit copy on force-dissolve DELETE 429', async () => {
    vi.mocked(api.delete).mockRejectedValue(axiosError(429));
    const user = userEvent.setup();
    await submitDissolve(user);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeInTheDocument();
    });
    expect(screen.queryByText('Failed to dissolve fleet.')).not.toBeInTheDocument();
  });
});
