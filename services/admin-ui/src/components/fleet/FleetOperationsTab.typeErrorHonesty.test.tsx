import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import FleetOperationsTab from './FleetOperationsTab';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
}

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

/**
 * LEG-3438 Soft-ORDER — FleetOperationsTab TypeError/Network Error honesty densify.
 */
describe('FleetOperationsTab typeErrorHonesty densify (LEG-3438)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on load to fleet-ops fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));
    render(<FleetOperationsTab />);
    await waitFor(() => {
      expect(screen.getByText(/Failed to load fleet operations data/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to load fleet operations data/i).textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on load to fleet-ops fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));
    render(<FleetOperationsTab />);
    await waitFor(() => {
      expect(screen.getByText(/Failed to load fleet operations data/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to load fleet operations data/i).textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('surfaces 403 with PLAYERS_VIEW scope hint when fleet load is denied', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));
    render(<FleetOperationsTab />);
    await waitFor(() => {
      expect(document.body.textContent).toMatch(/PLAYERS_VIEW|Access denied/i);
    });
    const text = document.body.textContent ?? '';
    expect(text).not.toContain('Failed to load fleet operations data.');
    expect(text).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(text);
  });

  it('surfaces 429 as admin rate-limit copy on fleet load', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));
    render(<FleetOperationsTab />);
    await waitFor(() => {
      expect(document.body.textContent).toMatch(/rate limit/i);
    });
    const text = document.body.textContent ?? '';
    expect(text).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(text);
  });

  it('surfaces 403 with COMBAT_INTERVENE scope hint on intervention POST', async () => {
    await openInterveneConfirm();
    vi.mocked(api.post).mockRejectedValue(axiosError(403));

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
      expect(document.body.textContent).toMatch(/COMBAT_INTERVENE|Access denied/i);
    });
    const text = document.body.textContent ?? '';
    expect(text).not.toContain('Failed to apply battle intervention.');
    assertNoTransportLeak(text);
  });
});
