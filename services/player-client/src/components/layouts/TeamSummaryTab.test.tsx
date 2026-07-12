// @vitest-environment jsdom
/**
 * TeamSummaryTab — the StatusBar dossier "Crew" tab (WO-UI5-DOSSIER
 * sub-part #1). Mirrors RankDisplay.test.tsx's seam (jsdom +
 * react-dom/client createRoot + act(), no RTL in this project).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const mockGetTeam = vi.fn();
const mockGetPermissions = vi.fn();

vi.mock('../../services/api', () => ({
  teamAPI: {
    getTeam: (...a: unknown[]) => mockGetTeam(...a),
    getPermissions: (...a: unknown[]) => mockGetPermissions(...a),
  },
}));

let mockPlayerState: { team_id?: string } | null = null;

vi.mock('../../contexts/GameContext', () => ({
  useGame: () => ({ playerState: mockPlayerState }),
}));

import TeamSummaryTab from './TeamSummaryTab';

const TEAM_RESPONSE = {
  id: 'team-1',
  name: 'Star Runners',
  description: 'A trading crew',
  tag: 'STR',
  logo: null,
  leader_id: 'player-9',
  recruitment_status: 'OPEN',
  max_members: 10,
  member_count: 4,
  total_credits: 50000,
  total_planets: 2,
  combat_rating: 12.5,
  trade_rating: 40.2,
  created_at: '2026-01-01T00:00:00Z',
  treasury_credits: 5000,
};

const PERMISSIONS_RESPONSE = {
  can_invite: true,
  can_kick: false,
  can_manage_treasury: false,
  can_manage_missions: false,
  can_manage_alliances: false,
  is_member: true,
  role: 'OFFICER',
};

describe('TeamSummaryTab', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGetTeam.mockReset();
    mockGetPermissions.mockReset();
    mockPlayerState = null;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  const mount = async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <TeamSummaryTab />
        </MemoryRouter>
      );
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  it('renders the No Team empty state with zero API calls when the player has no team_id', async () => {
    mockPlayerState = { team_id: undefined };
    await mount();

    expect(container.querySelector('.sb-crew-error')).toBeNull();
    expect(container.textContent).toContain('No Team');
    expect(mockGetTeam).not.toHaveBeenCalled();
    expect(mockGetPermissions).not.toHaveBeenCalled();
  });

  it('renders team identity, role, and ratings from real team + permissions data', async () => {
    mockPlayerState = { team_id: 'team-1' };
    mockGetTeam.mockResolvedValue(TEAM_RESPONSE);
    mockGetPermissions.mockResolvedValue(PERMISSIONS_RESPONSE);
    await mount();

    expect(mockGetTeam).toHaveBeenCalledWith('team-1');
    expect(mockGetPermissions).toHaveBeenCalledWith('team-1');
    const nameEl = container.querySelector('.sb-crew-name');
    expect(nameEl?.textContent).toBe('[STR] Star Runners');
    // Pixel a11y fix: semantic heading, not a plain div.
    expect(nameEl?.tagName).toBe('H2');

    const values = Array.from(container.querySelectorAll('.sb-identity-v')).map((el) => el.textContent);
    expect(values).toContain('Officer');
    expect(values).toContain('4/10');
    expect(values).toContain('2');
    expect(values).toContain('12.5');
    expect(values).toContain('40.2');
  });

  it('shows an error state instead of crashing when the fetch fails', async () => {
    mockPlayerState = { team_id: 'team-1' };
    mockGetTeam.mockRejectedValue(new Error('Network down'));
    mockGetPermissions.mockResolvedValue(PERMISSIONS_RESPONSE);
    await mount();

    const errorEl = container.querySelector('.sb-crew-error');
    expect(errorEl?.textContent).toBe('Network down');
    // Pixel a11y fix: announce the error on appear.
    expect(errorEl?.getAttribute('role')).toBe('alert');
  });

  it('shows a status/aria-live loading state before the fetch resolves', async () => {
    mockPlayerState = { team_id: 'team-1' };
    let resolveFn: (v: unknown) => void = () => {};
    mockGetTeam.mockReturnValue(new Promise((r) => { resolveFn = r; }));
    mockGetPermissions.mockResolvedValue(PERMISSIONS_RESPONSE);

    await act(async () => {
      root.render(
        <MemoryRouter>
          <TeamSummaryTab />
        </MemoryRouter>
      );
    });

    const loadingEl = container.querySelector('.sb-crew-loading');
    expect(loadingEl?.textContent).toBe('Loading…');
    // Pixel a11y fix: screen readers must be told this is a live status.
    expect(loadingEl?.getAttribute('role')).toBe('status');
    expect(loadingEl?.getAttribute('aria-live')).toBe('polite');

    await act(async () => {
      resolveFn(TEAM_RESPONSE);
    });
  });
});
