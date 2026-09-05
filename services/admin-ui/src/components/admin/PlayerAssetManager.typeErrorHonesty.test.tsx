import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import PlayerAssetManager from './PlayerAssetManager';
import { api } from '../../utils/auth';
import type { PlayerModel } from '../../types/playerManagement';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

const player = {
  id: 'p1',
  username: 'Trader',
  email: 't@example.com',
  credits: 100,
  turns: 10,
  current_sector_id: 1,
  status: 'active',
  team_id: null,
} as PlayerModel;

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
  expect(text).not.toMatch(/^HTTP \d+$/);
  expect(text).not.toContain('Request failed with status code');
}

const emptyShips = { data: { ships: [] } };
const emptyPlanets = { data: { planets: [] } };
const emptyPorts = { data: { ports: [] } };

function mockAssetGets(overrides: {
  ships?: unknown;
  planets?: unknown;
  ports?: unknown;
}) {
  vi.mocked(api.get).mockImplementation(async (url: string) => {
    if (url.includes('/admin/ships')) {
      if (overrides.ships instanceof Error) throw overrides.ships;
      return (overrides.ships as typeof emptyShips) ?? emptyShips;
    }
    if (url.includes('/admin/planets')) {
      if (overrides.planets instanceof Error) throw overrides.planets;
      return (overrides.planets as typeof emptyPlanets) ?? emptyPlanets;
    }
    if (url.includes('/admin/ports')) {
      if (overrides.ports instanceof Error) throw overrides.ports;
      return (overrides.ports as typeof emptyPorts) ?? emptyPorts;
    }
    throw new Error(`Unexpected GET ${url}`);
  });
}

/**
 * LEG-3455 Soft-ORDER — PlayerAssetManager TypeError/Network Error honesty densify.
 * LEG-3871 Soft-ORDER — GET-only 403/429 HTTP honesty densify (ships/planets/ports; invent=0).
 */
describe('PlayerAssetManager typeErrorHonesty densify (LEG-3455)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error to gameserver-unreachable assets fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(
      <PlayerAssetManager player={player} onClose={() => {}} onUpdate={() => {}} />,
    );

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Gameserver unreachable|network error loading player assets/i);
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
    expect(alert).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch to gameserver-unreachable assets fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(
      <PlayerAssetManager player={player} onClose={() => {}} onUpdate={() => {}} />,
    );

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Gameserver unreachable|network error loading player assets/i);
    expect(alert).not.toMatch(/TypeError/i);
    expect(alert).not.toBe('Failed to fetch');
    expect(alert).not.toMatch(/Failed to fetch/i);
  });

  it('surfaces 403 with PLAYERS_VIEW scope copy when ships GET is denied', async () => {
    mockAssetGets({ ships: axiosError(403) });

    render(
      <PlayerAssetManager player={player} onClose={() => {}} onUpdate={() => {}} />,
    );

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Access denied|PLAYERS_VIEW/i);
    expect(alert).not.toMatch(/\b403\b/);
    expect(alert).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(alert);
  });

  it('surfaces 403 with PLAYERS_VIEW scope copy when planets GET is denied', async () => {
    mockAssetGets({ planets: axiosError(403) });

    render(
      <PlayerAssetManager player={player} onClose={() => {}} onUpdate={() => {}} />,
    );

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Access denied|PLAYERS_VIEW/i);
    expect(alert).not.toMatch(/\b403\b/);
    expect(alert).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(alert);
  });

  it('surfaces 403 with PLAYERS_VIEW scope copy when ports GET is denied', async () => {
    mockAssetGets({ ports: axiosError(403) });

    render(
      <PlayerAssetManager player={player} onClose={() => {}} onUpdate={() => {}} />,
    );

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Access denied|PLAYERS_VIEW/i);
    expect(alert).not.toMatch(/\b403\b/);
    expect(alert).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(alert);
  });

  it('surfaces 429 as admin rate-limit copy on ships GET', async () => {
    mockAssetGets({ ships: axiosError(429) });

    render(
      <PlayerAssetManager player={player} onClose={() => {}} onUpdate={() => {}} />,
    );

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/rate limit/i);
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/rate limit/i);
    expect(alert).not.toMatch(/\b429\b/);
    expect(alert).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(alert);
  });
});
