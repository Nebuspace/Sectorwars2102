// @vitest-environment jsdom
/**
 * PortOfficeTeamPanel — LEG-4120 team bind + member-share wire.
 */
import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const getTeamOwnershipStatus = vi.fn();
const bindStationToTeam = vi.fn();
const setTeamMemberShare = vi.fn();
const refreshPlayerState = vi.fn();

let mockPlayerState: { id?: string; team_id?: string | null } | null = {
  id: 'owner-1',
  team_id: 'team-1',
};

vi.mock('../../../services/api', () => ({
  portOwnershipAPI: {
    getTeamOwnershipStatus: (...args: unknown[]) => getTeamOwnershipStatus(...args),
    bindStationToTeam: (...args: unknown[]) => bindStationToTeam(...args),
    setTeamMemberShare: (...args: unknown[]) => setTeamMemberShare(...args),
  },
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: mockPlayerState,
    refreshPlayerState,
  }),
}));

import PortOfficeTeamPanel, {
  formatTeamOwnershipError,
} from '../PortOfficeTeamPanel';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('formatTeamOwnershipError (LEG-4120)', () => {
  const fallback = 'Team ownership action failed. Please try again.';

  it('densifies TypeError without transport strings', () => {
    expect(formatTeamOwnershipError(new TypeError('Failed to fetch'), fallback)).toBe(fallback);
    expect(formatTeamOwnershipError(new TypeError('Failed to fetch'), fallback)).not.toMatch(
      /TypeError/i,
    );
  });

  it('surfaces 403/429 without raw status codes', () => {
    expect(formatTeamOwnershipError(apiRequestError(403), fallback)).toMatch(/permission/i);
    expect(formatTeamOwnershipError(apiRequestError(403), fallback)).not.toMatch(/\b403\b/);
    expect(formatTeamOwnershipError(apiRequestError(429), fallback)).toMatch(/rate limit/i);
    expect(formatTeamOwnershipError(apiRequestError(429), fallback)).not.toMatch(/\b429\b/);
  });
});

describe('PortOfficeTeamPanel', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    getTeamOwnershipStatus.mockReset();
    bindStationToTeam.mockReset();
    setTeamMemberShare.mockReset();
    refreshPlayerState.mockReset();
    refreshPlayerState.mockResolvedValue(undefined);
    mockPlayerState = { id: 'owner-1', team_id: 'team-1' };
    getTeamOwnershipStatus.mockResolvedValue({
      station_id: 'st-1',
      mode: 'solo',
      team_id: null,
      member_share_pct: null,
      owner_id: 'owner-1',
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it('loads solo status and binds to team with share pct', async () => {
    bindStationToTeam.mockResolvedValue({
      message: 'Station Alpha bound to team team-1',
      mode: 'team',
      team_id: 'team-1',
      member_share_pct: 15,
    });
    getTeamOwnershipStatus
      .mockResolvedValueOnce({
        station_id: 'st-1',
        mode: 'solo',
        team_id: null,
        member_share_pct: null,
        owner_id: 'owner-1',
      })
      .mockResolvedValueOnce({
        station_id: 'st-1',
        mode: 'team',
        team_id: 'team-1',
        member_share_pct: 15,
        owner_id: 'owner-1',
      });

    await act(async () => {
      root.render(<PortOfficeTeamPanel stationId="st-1" stationName="Alpha" />);
      await flush();
    });

    expect(getTeamOwnershipStatus).toHaveBeenCalledWith('st-1');
    expect(container.querySelector('[data-testid="po-team-bind"]')).toBeTruthy();

    const submit = container.querySelector(
      '[data-testid="po-team-bind-submit"]',
    ) as HTMLButtonElement;
    await act(async () => {
      submit.click();
      await flush();
      await flush();
    });

    // Default bind share is 10%; range change is UI-only — assert tip body shape.
    expect(bindStationToTeam).toHaveBeenCalledWith('st-1', 'team-1', 10);
  });

  it('posts member-share on team-owned station', async () => {
    getTeamOwnershipStatus.mockResolvedValue({
      station_id: 'st-1',
      mode: 'team',
      team_id: 'team-1',
      member_share_pct: 10,
      owner_id: 'owner-1',
    });
    setTeamMemberShare.mockResolvedValue({
      message: 'Team member share at Alpha set to 25%',
      mode: 'team',
      member_share_pct: 25,
    });

    await act(async () => {
      root.render(<PortOfficeTeamPanel stationId="st-1" stationName="Alpha" />);
      await flush();
    });

    expect(container.querySelector('[data-testid="po-team-member-share"]')).toBeTruthy();
    const submit = container.querySelector(
      '[data-testid="po-team-share-submit"]',
    ) as HTMLButtonElement;
    await act(async () => {
      submit.click();
      await flush();
      await flush();
    });

    // Status seeded member_share_pct=10; assert POST body shape.
    expect(setTeamMemberShare).toHaveBeenCalledWith('st-1', 10);
  });

  it('shows densified 403 on bind failure', async () => {
    bindStationToTeam.mockRejectedValue(apiRequestError(403));

    await act(async () => {
      root.render(<PortOfficeTeamPanel stationId="st-1" stationName="Alpha" />);
      await flush();
    });

    const submit = container.querySelector(
      '[data-testid="po-team-bind-submit"]',
    ) as HTMLButtonElement;
    await act(async () => {
      submit.click();
      await flush();
      await flush();
    });

    const alert = container.querySelector('[data-testid="po-team-msg"]');
    expect(alert?.textContent).toMatch(/permission/i);
    expect(alert?.textContent).not.toMatch(/\b403\b/);
  });
});
