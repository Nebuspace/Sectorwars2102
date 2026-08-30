import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import FleetOperationsTab from './FleetOperationsTab';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

const sampleStats = {
  total_fleets: 1,
  active_fleets: 1,
  fleets_in_battle: 1,
  total_ships_in_fleets: 10,
  total_firepower: 5000,
  average_fleet_size: 10,
  battles_today: 1,
  battles_this_week: 3,
  most_powerful_fleet: { id: 'fleet-1', name: 'Strike Fleet', team: 'Alpha Team', firepower: 5000 },
  largest_fleet: { id: 'fleet-1', name: 'Strike Fleet', team: 'Alpha Team', ships: 10 },
};

const sampleFleet = {
  id: 'fleet-1',
  team_id: 'team-1',
  team_name: 'Alpha Team',
  name: 'Strike Fleet',
  status: 'in_battle',
  formation: 'wedge',
  total_ships: 10,
  total_firepower: 5000,
  total_shields: 3000,
  total_hull: 4000,
  average_speed: 2.5,
  morale: 75,
  supply_level: 80,
  commander_id: 'cmd-1',
  commander_name: 'Commander One',
  sector_id: 'sector-1',
  sector_name: 'Sector Alpha',
  member_count: 5,
  created_at: '2026-01-01T00:00:00Z',
  last_battle: '2026-01-02T00:00:00Z',
};

const sampleBattle = {
  id: 'battle-1',
  phase: 'combat',
  started_at: '2026-01-02T00:00:00Z',
  ended_at: null,
  attacker_fleet_id: 'fleet-1',
  attacker_fleet_name: 'Strike Fleet',
  attacker_team_name: 'Alpha Team',
  defender_fleet_id: 'fleet-2',
  defender_fleet_name: 'Defense Fleet',
  defender_team_name: 'Beta Team',
  sector_id: 'sector-1',
  sector_name: 'Sector Alpha',
  attacker_ships_initial: 10,
  defender_ships_initial: 8,
  attacker_ships_destroyed: 1,
  defender_ships_destroyed: 2,
  attacker_ships_retreated: 0,
  defender_ships_retreated: 0,
  total_damage_dealt: 1500,
  winner: null,
  credits_looted: 0,
  duration: null,
};

function mockHappyFleetGets() {
  vi.mocked(api.get).mockImplementation(async (url: string) => {
    if (url.includes('/stats')) {
      return { data: sampleStats };
    }
    if (url.endsWith('/fleets/') || url.endsWith('/fleets')) {
      return { data: [sampleFleet] };
    }
    if (url.includes('/battles')) {
      return { data: [sampleBattle] };
    }
    return { data: [] };
  });
}

async function openInterveneConfirm() {
  mockHappyFleetGets();
  render(<FleetOperationsTab />);
  await waitFor(() => {
    expect(screen.getByRole('button', { name: 'Intervene' })).toBeTruthy();
  });
  fireEvent.click(screen.getByRole('button', { name: 'Intervene' }));
  fireEvent.change(
    screen.getByPlaceholderText('Why is this intervention needed?'),
    { target: { value: 'Stuck battle needs admin override' } },
  );
}

describe('FleetOperationsTab scope errors', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
  });

  it('reports all-reject 403 as PLAYERS_VIEW, not generic Failed', async () => {
    vi.mocked(api.get).mockRejectedValue({ response: { status: 403 } });
    render(<FleetOperationsTab />);
    await waitFor(() => {
      expect(document.body.textContent).toMatch(/PLAYERS_VIEW/);
    });
    expect(document.body.textContent).not.toContain('Failed to load fleet operations data.');
  });

  it('reports all-reject 429 as admin rate-limit', async () => {
    vi.mocked(api.get).mockRejectedValue({ response: { status: 429 } });
    render(<FleetOperationsTab />);
    await waitFor(() => {
      expect(document.body.textContent).toMatch(/rate limit/i);
    });
  });
});

describe('FleetOperationsTab battle intervene POST (LEG-2764)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('intervene POST 403 surfaces COMBAT_INTERVENE, not generic Failed', async () => {
    await openInterveneConfirm();
    vi.mocked(api.post).mockRejectedValue({ response: { status: 403 } });

    fireEvent.click(screen.getByRole('button', { name: 'Confirm Intervention' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/fleets/battles/battle-1/intervene',
        expect.objectContaining({
          action: 'end_battle',
          reason: 'Stuck battle needs admin override',
        }),
      );
    });

    await waitFor(() => {
      expect(document.body.textContent).toMatch(/COMBAT_INTERVENE/);
    });
    expect(document.body.textContent).not.toContain('Failed to apply battle intervention.');
  });

  it('intervene POST 429 surfaces admin rate-limit copy', async () => {
    await openInterveneConfirm();
    vi.mocked(api.post).mockRejectedValue({ response: { status: 429 } });

    fireEvent.click(screen.getByRole('button', { name: 'Confirm Intervention' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalled();
    });

    await waitFor(() => {
      expect(document.body.textContent).toMatch(/rate limit/i);
    });
    expect(document.body.textContent).not.toContain('Failed to apply battle intervention.');
  });
});

async function openFleetManageConfirm(action: 'morale' | 'dissolve' = 'morale') {
  mockHappyFleetGets();
  render(<FleetOperationsTab />);
  await waitFor(() => {
    expect(screen.getByRole('button', { name: 'Manage' })).toBeTruthy();
  });
  fireEvent.click(screen.getByRole('button', { name: 'Manage' }));
  if (action === 'dissolve') {
    fireEvent.change(screen.getByLabelText('Action'), {
      target: { value: 'dissolve' },
    });
  }
  fireEvent.change(screen.getByLabelText(/Reason \(min 10 characters\)/i), {
    target: {
      value:
        action === 'morale'
          ? 'Morale correction for audit'
          : 'Dissolve abandoned fleet',
    },
  });
}

describe('FleetOperationsTab morale/dissolve mutation fleetActError (LEG-2643)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.patch).mockReset();
    vi.mocked(api.delete).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('surfaces COMBAT_INTERVENE copy on morale PATCH 403', async () => {
    await openFleetManageConfirm('morale');
    vi.mocked(api.patch).mockRejectedValue({ response: { status: 403 } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm Morale Change' }));

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
    await waitFor(() => {
      expect(document.body.textContent).toMatch(/COMBAT_INTERVENE/);
    });
    expect(document.body.textContent).not.toContain('Failed to adjust fleet morale.');
  });

  it('surfaces rate-limit copy on morale PATCH 429', async () => {
    await openFleetManageConfirm('morale');
    vi.mocked(api.patch).mockRejectedValue({ response: { status: 429 } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm Morale Change' }));

    await waitFor(() => {
      expect(document.body.textContent).toMatch(/rate limit/i);
    });
    expect(document.body.textContent).not.toContain('Failed to adjust fleet morale.');
  });

  it('surfaces COMBAT_INTERVENE copy on force-dissolve DELETE 403', async () => {
    await openFleetManageConfirm('dissolve');
    vi.mocked(api.delete).mockRejectedValue({ response: { status: 403 } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm Dissolve' }));

    await waitFor(() => {
      expect(api.delete).toHaveBeenCalledWith(
        '/api/v1/admin/fleets/fleet-1/force-dissolve',
        expect.objectContaining({
          data: { reason: 'Dissolve abandoned fleet' },
        }),
      );
    });
    await waitFor(() => {
      expect(document.body.textContent).toMatch(/COMBAT_INTERVENE/);
    });
    expect(document.body.textContent).not.toContain('Failed to dissolve fleet.');
  });

  it('surfaces rate-limit copy on force-dissolve DELETE 429', async () => {
    await openFleetManageConfirm('dissolve');
    vi.mocked(api.delete).mockRejectedValue({ response: { status: 429 } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm Dissolve' }));

    await waitFor(() => {
      expect(document.body.textContent).toMatch(/rate limit/i);
    });
    expect(document.body.textContent).not.toContain('Failed to dissolve fleet.');
  });
});
