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

const axiosError = (status: number) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status },
  });

describe('PlayerAssetManager scope honesty (LEG-1207)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('surfaces PLAYERS_VIEW denial on 403 instead of silent empty list', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(
      <PlayerAssetManager player={player} onClose={() => {}} onUpdate={() => {}} />
    );

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/PLAYERS_VIEW|Access denied/i);
    expect(alert).not.toMatch(/^Failed to load player assets$/);
  });

  it('surfaces admin rate-limit on 429', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(
      <PlayerAssetManager player={player} onClose={() => {}} onUpdate={() => {}} />
    );

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    expect(screen.getByRole('alert').textContent ?? '').toMatch(/rate limit/i);
  });

  it('surfaces honest fallback on non-RBAC network collapse (LEG-2962)', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(
      <PlayerAssetManager player={player} onClose={() => {}} onUpdate={() => {}} />
    );

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Gameserver unreachable|network error loading player assets/i);
    expect(alert).not.toMatch(/TypeError/i);
    expect(alert).not.toBe('Failed to fetch');
    expect(alert).not.toMatch(/^Failed to load player assets$/);
  });

  it('collapses axios-shaped Network Error to gameserver-unreachable fallback (LEG-3313)', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(
      <PlayerAssetManager player={player} onClose={() => {}} onUpdate={() => {}} />
    );

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Gameserver unreachable|network error loading player assets/i);
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
    expect(alert).not.toMatch(/^Failed to load player assets$/);
  });
});
