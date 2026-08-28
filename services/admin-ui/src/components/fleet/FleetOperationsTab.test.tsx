import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import FleetOperationsTab from './FleetOperationsTab';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

const axiosError = (status: number) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: {} },
  });

const sampleStats = {
  total_fleets: 1,
  active_fleets: 1,
  fleets_in_battle: 1,
  total_ships_in_fleets: 10,
  total_firepower: 500,
  average_fleet_size: 10,
  battles_today: 1,
  battles_this_week: 1,
  most_powerful_fleet: { id: 'fleet-1', name: 'Alpha', team: 'Team A', firepower: 500 },
  largest_fleet: { id: 'fleet-1', name: 'Alpha', team: 'Team A', ships: 10 },
};

const sampleFleet = {
  id: 'fleet-1',
  team_id: 'team-1',
  team_name: 'Team A',
  name: 'Alpha Fleet',
  status: 'active',
  formation: 'wedge',
  total_ships: 10,
  total_firepower: 500,
  total_shields: 200,
  total_hull: 300,
  average_speed: 5,
  morale: 75,
  supply_level: 80,
  commander_id: null,
  commander_name: null,
  sector_id: 'sector-1',
  sector_name: 'Sol',
  member_count: 3,
  created_at: '2026-08-01T00:00:00Z',
  last_battle: null,
};

const sampleActiveBattle = {
  id: 'battle-1',
  phase: 'engagement',
  started_at: '2026-08-28T00:00:00Z',
  ended_at: null,
  attacker_fleet_id: 'fleet-1',
  attacker_fleet_name: 'Alpha Fleet',
  attacker_team_name: 'Team A',
  defender_fleet_id: 'fleet-2',
  defender_fleet_name: 'Beta Fleet',
  defender_team_name: 'Team B',
  sector_id: 'sector-1',
  sector_name: 'Sol',
  attacker_ships_initial: 10,
  defender_ships_initial: 8,
  attacker_ships_destroyed: 1,
  defender_ships_destroyed: 2,
  attacker_ships_retreated: 0,
  defender_ships_retreated: 0,
  total_damage_dealt: 150,
  winner: null,
  credits_looted: 0,
  duration: null,
};

function mockSuccessfulLoad() {
  vi.mocked(api.get).mockImplementation(async (url: string) => {
    if (url.includes('/stats')) return { data: sampleStats };
    if (url.endsWith('/battles')) return { data: [sampleActiveBattle] };
    if (url.includes('/fleets/')) return { data: [sampleFleet] };
    return { data: [] };
  });
}

async function openInterveneForm() {
  render(<FleetOperationsTab />);
  await waitFor(() => {
    expect(screen.getByRole('button', { name: 'Intervene' })).toBeInTheDocument();
  });
  fireEvent.click(screen.getByRole('button', { name: 'Intervene' }));
  fireEvent.change(screen.getByPlaceholderText('Why is this intervention needed?'), {
    target: { value: 'Stuck battle needs admin resolution.' },
  });
}

async function openFleetMoraleForm() {
  render(<FleetOperationsTab />);
  await waitFor(() => {
    expect(screen.getByRole('button', { name: 'Manage' })).toBeInTheDocument();
  });
  fireEvent.click(screen.getByRole('button', { name: 'Manage' }));
  fireEvent.change(screen.getByPlaceholderText('Why is this action needed?'), {
    target: { value: 'Morale adjustment for testing.' },
  });
}

async function openFleetDissolveForm() {
  render(<FleetOperationsTab />);
  await waitFor(() => {
    expect(screen.getByRole('button', { name: 'Manage' })).toBeInTheDocument();
  });
  fireEvent.click(screen.getByRole('button', { name: 'Manage' }));
  fireEvent.change(screen.getByDisplayValue('Adjust Morale'), {
    target: { value: 'dissolve' },
  });
  fireEvent.change(screen.getByPlaceholderText('Why is this action needed?'), {
    target: { value: 'Force dissolve for testing.' },
  });
}

describe('FleetOperationsTab scope errors', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.mocked(api.patch).mockReset();
    vi.mocked(api.delete).mockReset();
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

describe('FleetOperationsTab battle intervene POST (LEG-2642)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    mockSuccessfulLoad();
  });

  it('shows COMBAT_INTERVENE copy on intervene POST 403', async () => {
    vi.mocked(api.post).mockRejectedValue(axiosError(403));
    await openInterveneForm();

    fireEvent.click(screen.getByRole('button', { name: 'Confirm Intervention' }));

    await waitFor(() => {
      expect(document.body.textContent).toMatch(/COMBAT_INTERVENE/);
    });
    expect(document.body.textContent).not.toContain('Failed to apply battle intervention.');
  });

  it('shows admin rate-limit copy on intervene POST 429', async () => {
    vi.mocked(api.post).mockRejectedValue(axiosError(429));
    await openInterveneForm();

    fireEvent.click(screen.getByRole('button', { name: 'Confirm Intervention' }));

    await waitFor(() => {
      expect(document.body.textContent).toMatch(/rate limit/i);
    });
  });
});

describe('FleetOperationsTab fleet mutations (LEG-2643)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.patch).mockReset();
    vi.mocked(api.delete).mockReset();
    mockSuccessfulLoad();
  });

  it('shows COMBAT_INTERVENE copy on morale PATCH 403', async () => {
    vi.mocked(api.patch).mockRejectedValue(axiosError(403));
    await openFleetMoraleForm();

    fireEvent.click(screen.getByRole('button', { name: 'Confirm Morale Change' }));

    await waitFor(() => {
      expect(document.body.textContent).toMatch(/COMBAT_INTERVENE/);
    });
    expect(document.body.textContent).not.toContain('Failed to adjust fleet morale.');
  });

  it('shows admin rate-limit copy on morale PATCH 429', async () => {
    vi.mocked(api.patch).mockRejectedValue(axiosError(429));
    await openFleetMoraleForm();

    fireEvent.click(screen.getByRole('button', { name: 'Confirm Morale Change' }));

    await waitFor(() => {
      expect(document.body.textContent).toMatch(/rate limit/i);
    });
  });

  it('shows COMBAT_INTERVENE copy on force-dissolve DELETE 403', async () => {
    vi.mocked(api.delete).mockRejectedValue(axiosError(403));
    await openFleetDissolveForm();

    fireEvent.click(screen.getByRole('button', { name: 'Confirm Dissolve' }));

    await waitFor(() => {
      expect(document.body.textContent).toMatch(/COMBAT_INTERVENE/);
    });
    expect(document.body.textContent).not.toContain('Failed to dissolve fleet.');
  });

  it('shows admin rate-limit copy on force-dissolve DELETE 429', async () => {
    vi.mocked(api.delete).mockRejectedValue(axiosError(429));
    await openFleetDissolveForm();

    fireEvent.click(screen.getByRole('button', { name: 'Confirm Dissolve' }));

    await waitFor(() => {
      expect(document.body.textContent).toMatch(/rate limit/i);
    });
  });
});
