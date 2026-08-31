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

/**
 * LEG-3455 Soft-ORDER — PlayerAssetManager TypeError/Network Error honesty densify.
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
});
