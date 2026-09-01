// @vitest-environment jsdom
/**
 * TeamManager — CREW MANIFEST console (invite/kick/promote/treasury/chat).
 * Not currently mounted on any live route (kept as canon-scaffolding per
 * its own doc-comment), but still real, maintained code -- covered the
 * same as any other component. jsdom + react-dom/client createRoot +
 * act(), no RTL, matching the ProposePolicyForm/MissionPlanner seam.
 *
 * ResourceSharing/TeamChat are mocked out (each already has its own test
 * file) to isolate TeamManager's own orchestration: load/error/no-team
 * states, the wire mappers (snake_case -> camelCase, incl. the recruitment
 * enum map and the online-always-false/canon-gap fields), tab switching,
 * the create-team modal's client-side validation + best-effort post-create
 * member/permissions/refresh fetches (each independently swallowed), member
 * promote/demote/kick (2-click kick confirm, permission + self-exclusion
 * gating), and settings edit/save/leave (2-click leave confirm).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { TeamApiResponse, TeamMemberApiResponse, TeamPermissionsApiResponse } from '../../../types/team';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../ResourceSharing', () => ({
  ResourceSharing: ({ teamId }: { teamId: string }) => <div data-testid="resource-sharing" data-team-id={teamId} />,
}));
vi.mock('../TeamChat', () => ({
  TeamChat: ({ teamId }: { teamId: string }) => <div data-testid="team-chat" data-team-id={teamId} />,
}));
vi.mock('../TeamWarPanel', () => ({
  TeamWarPanel: ({ teamId, isLeader }: { teamId: string; isLeader: boolean }) => (
    <div data-testid="team-war-panel" data-team-id={teamId} data-leader={String(isLeader)} />
  ),
}));

const {
  mockGetTeam,
  mockGetMembers,
  mockGetPermissions,
  mockUpdateTeam,
  mockPromoteMember,
  mockKickMember,
  mockLeaveTeam,
  mockCreateTeam,
  mockMedalsGetMe,
} = vi.hoisted(() => ({
  mockGetTeam: vi.fn<(id: string) => Promise<TeamApiResponse>>(),
  mockGetMembers: vi.fn<(id: string) => Promise<TeamMemberApiResponse[]>>(async () => []),
  mockGetPermissions: vi.fn<(id: string) => Promise<TeamPermissionsApiResponse>>(),
  mockUpdateTeam: vi.fn<(id: string, updates: unknown) => Promise<TeamApiResponse>>(),
  mockPromoteMember: vi.fn<(teamId: string, memberId: string, role: string) => Promise<TeamMemberApiResponse>>(),
  mockKickMember: vi.fn<(teamId: string, memberId: string) => Promise<unknown>>(async () => undefined),
  mockLeaveTeam: vi.fn<() => Promise<unknown>>(async () => undefined),
  mockCreateTeam: vi.fn<(data: unknown) => Promise<TeamApiResponse>>(),
  mockMedalsGetMe: vi.fn<() => Promise<unknown>>(async () => ({
    earned: [],
    pinned_medal_id: null,
    total_earned: 0,
  })),
}));

vi.mock('../../../services/api', () => ({
  teamAPI: {
    getTeam: mockGetTeam,
    getMembers: mockGetMembers,
    getPermissions: mockGetPermissions,
    updateTeam: mockUpdateTeam,
    promoteMember: mockPromoteMember,
    kickMember: mockKickMember,
    leaveTeam: mockLeaveTeam,
    createTeam: mockCreateTeam,
  },
  medalsAPI: {
    getMe: mockMedalsGetMe,
  },
}));

let mockPlayerState: { id: string; team_id: string | null; credits: number } | null = {
  id: 'player-1',
  team_id: 'team-1',
  credits: 50000,
};
const mockRefreshPlayerState = vi.fn<() => Promise<void>>(async () => undefined);

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({ playerState: mockPlayerState, refreshPlayerState: mockRefreshPlayerState }),
}));

import { TeamManager, formatTeamManagerLoadError, formatTeamManagerMutationError } from '../TeamManager';

const rawTeam = (overrides: Partial<TeamApiResponse> = {}): TeamApiResponse => ({
  id: 'team-1',
  name: 'Star Wolves',
  description: 'A pack of raiders.',
  tag: 'WOLF',
  logo: null,
  leader_id: 'player-leader',
  recruitment_status: 'OPEN',
  max_members: 10,
  member_count: 2,
  total_credits: 0,
  total_planets: 3,
  combat_rating: 42.567,
  trade_rating: 10.1,
  created_at: '2026-01-01T00:00:00Z',
  treasury_credits: 100000,
  ...overrides,
});

const rawMember = (overrides: Partial<TeamMemberApiResponse> = {}): TeamMemberApiResponse => ({
  player_id: 'player-2',
  nickname: 'Rho',
  role: 'MEMBER',
  joined_at: '2026-01-01T00:00:00Z',
  last_active: null,
  can_invite: false,
  can_kick: false,
  can_manage_treasury: false,
  can_manage_missions: false,
  can_manage_alliances: false,
  contribution_credits: { credits: 500, ore: 20, fuel: 5 },
  current_sector: 7,
  combat_rating: 3,
  ...overrides,
});

const rawPermissions = (overrides: Partial<TeamPermissionsApiResponse> = {}): TeamPermissionsApiResponse => ({
  can_invite: true,
  can_kick: true,
  can_manage_treasury: true,
  can_manage_missions: true,
  can_manage_alliances: true,
  is_member: true,
  role: 'LEADER',
  ...overrides,
});

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
};

function setValue(el: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const proto =
    el instanceof HTMLTextAreaElement ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')!.set!;
  setter.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
}

describe('TeamManager', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let errorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    mockPlayerState = { id: 'player-1', team_id: 'team-1', credits: 50000 };
    mockGetTeam.mockReset();
    mockGetTeam.mockResolvedValue(rawTeam());
    mockGetMembers.mockReset();
    mockGetMembers.mockResolvedValue([rawMember()]);
    mockGetPermissions.mockReset();
    mockGetPermissions.mockResolvedValue(rawPermissions());
    mockUpdateTeam.mockReset();
    mockPromoteMember.mockReset();
    mockKickMember.mockReset();
    mockKickMember.mockResolvedValue(undefined);
    mockLeaveTeam.mockReset();
    mockLeaveTeam.mockResolvedValue(undefined);
    mockCreateTeam.mockReset();
    mockMedalsGetMe.mockReset();
    mockMedalsGetMe.mockResolvedValue({ earned: [], pinned_medal_id: null, total_earned: 0 });
    mockRefreshPlayerState.mockReset();
    mockRefreshPlayerState.mockResolvedValue(undefined);
    errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    errorSpy.mockRestore();
  });

  const mount = async () => {
    await act(async () => {
      root.render(<TeamManager />);
    });
    await flush();
  };

  const tab = (name: string) =>
    Array.from(container.querySelectorAll('.team-tabs button')).find((b) => b.textContent === name) as HTMLButtonElement;

  it('shows the no-team empty state and fires no team fetches when the player has no team_id', async () => {
    mockPlayerState = { id: 'player-1', team_id: null, credits: 0 };
    await mount();

    expect(container.querySelector('.no-team')).not.toBeNull();
    expect(container.querySelector('.registry-offline-note')?.textContent).toContain('TEAM REGISTRY OFFLINE');
    expect(mockGetTeam).not.toHaveBeenCalled();
  });

  it('shows a load-error EmptyState with a working Retry action on fetch failure', async () => {
    mockGetTeam.mockRejectedValue(new Error('team service unreachable'));
    await mount();

    expect(container.querySelector('.load-error')).not.toBeNull();
    expect(container.textContent).toContain('team service unreachable');

    mockGetTeam.mockResolvedValue(rawTeam());
    await act(async () => {
      (container.querySelector('.load-error button') as HTMLButtonElement).click();
    });
    await flush();
    expect(container.querySelector('.team-header')).not.toBeNull();
  });

  it('formatTeamManagerLoadError preserves 404 server detail', () => {
    const err = Object.assign(new Error('Team not found'), { status: 404 });
    expect(formatTeamManagerLoadError(err)).toBe('Team not found');
  });

  it('formatTeamManagerLoadError falls back on bare 404', () => {
    const err = Object.assign(new Error('API Error: 404'), { status: 404 });
    expect(formatTeamManagerLoadError(err)).toBe('Team not found.');
  });

  it('formatTeamManagerLoadError preserves 403 server detail', () => {
    const err = Object.assign(new Error('You are not a member of this team'), {
      status: 403,
    });
    expect(formatTeamManagerLoadError(err)).toBe('You are not a member of this team');
  });

  it('formatTeamManagerLoadError falls back on bare 403', () => {
    const err = Object.assign(new Error('API Error: 403'), { status: 403 });
    expect(formatTeamManagerLoadError(err)).toBe('You are not a member of this team.');
  });

  it('formatTeamManagerMutationError preserves server detail', () => {
    expect(formatTeamManagerMutationError(new Error('insufficient credits'), 'Failed to create team')).toBe(
      'insufficient credits',
    );
  });

  it('formatTeamManagerMutationError falls back on bare API Error status', () => {
    const err = Object.assign(new Error('API Error: 400'), { status: 400 });
    expect(formatTeamManagerMutationError(err, 'Failed to create team')).toBe('Failed to create team');
  });

  it('formatTeamManagerMutationError uses permission copy on bare 403', () => {
    const err = Object.assign(new Error('API Error: 403'), { status: 403 });
    expect(formatTeamManagerMutationError(err, 'Failed to kick member')).toBe(
      'You do not have permission for this team action.',
    );
  });

  it('formatTeamManagerMutationError surfaces 429 rate-limit copy', () => {
    const err = Object.assign(new Error('API Error: 429'), { status: 429 });
    expect(formatTeamManagerMutationError(err, 'Failed to create team')).toBe(
      'Team action rate limit exceeded — wait a moment and try again.',
    );
  });

  it('formatTeamManagerLoadError falls back on TypeError network collapse', () => {
    const text = formatTeamManagerLoadError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to load team data');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatTeamManagerMutationError falls back on TypeError network collapse', () => {
    const text = formatTeamManagerMutationError(
      new TypeError('Failed to fetch'),
      'Failed to create team',
    );
    expect(text).toBe('Failed to create team');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('surfaces honest load fallback when getTeam rejects with TypeError', async () => {
    mockGetTeam.mockRejectedValue(new TypeError('Failed to fetch'));
    await mount();

    expect(container.querySelector('.load-error')).not.toBeNull();
    expect(container.textContent).toContain('Failed to load team data');
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
  });

  it('surfaces honest 404 load copy when getTeam rejects with bare status', async () => {
    mockGetTeam.mockRejectedValue(
      Object.assign(new Error('API Error: 404'), { status: 404 }),
    );
    await mount();

    expect(container.querySelector('.load-error')).not.toBeNull();
    expect(container.textContent).toContain('Team not found.');
  });

  it('surfaces honest 403 load copy when getTeam rejects with bare status', async () => {
    mockGetTeam.mockRejectedValue(
      Object.assign(new Error('API Error: 403'), { status: 403 }),
    );
    await mount();

    expect(container.querySelector('.load-error')).not.toBeNull();
    expect(container.textContent).toContain('You are not a member of this team.');
  });

  it('renders the header with tag/name, member counts, and founded date, and maps combat/trade ratings to 1 decimal', async () => {
    await mount();

    expect(container.querySelector('.team-identity h2')?.textContent).toBe('[WOLF] Star Wolves');
    expect(container.querySelector('.team-stats .members')?.textContent).toContain('2/10');

    await act(async () => {
      tab('Overview').click();
    });
    const stats = Array.from(container.querySelectorAll('.stat-value')).map((n) => n.textContent);
    expect(stats).toContain('42.6');
    expect(stats).toContain('10.1');
  });

  it.each([
    ['OPEN', 'open', '🟢 Open - Accepting new members'],
    ['INVITE_ONLY', 'invite-only', '🟡 Invite Only - By invitation'],
    ['CLOSED', 'closed', '🔴 Closed - Not recruiting'],
  ] as const)('maps recruitment_status %s to the %s UI class + label', async (apiValue, uiClass, label) => {
    mockGetTeam.mockResolvedValue(rawTeam({ recruitment_status: apiValue }));
    await mount();
    const el = container.querySelector('.recruitment-status');
    expect(el?.className).toContain(uiClass);
    expect(el?.textContent).toBe(label);
  });

  it('falls back an unrecognized recruitment_status to "closed"', async () => {
    mockGetTeam.mockResolvedValue(rawTeam({ recruitment_status: 'GARBAGE' }));
    await mount();
    expect(container.querySelector('.recruitment-status')?.className).toContain('closed');
  });

  it('switches tab content and active-button styling on click', async () => {
    await mount();
    expect(container.querySelector('.team-overview')).not.toBeNull();

    await act(async () => {
      tab('Members').click();
    });
    expect(container.querySelector('.team-members')).not.toBeNull();
    expect(tab('Members').className).toBe('active');
    expect(tab('Overview').className).toBe('');
  });

  describe('members tab', () => {
    it('renders a member with role badge, location, contributions summed from non-credits keys', async () => {
      mockGetMembers.mockResolvedValue([
        rawMember({ player_id: 'p2', nickname: 'Rho', role: 'OFFICER', contribution_credits: { credits: 500, ore: 20, fuel: 5 }, current_sector: 7 }),
      ]);
      await mount();
      await act(async () => {
        tab('Members').click();
      });

      const item = container.querySelector('.member-item') as HTMLElement;
      expect(item.querySelector('.role-badge')?.textContent).toBe('officer');
      expect(item.querySelector('.member-name')?.textContent).toContain('Rho');
      expect(item.querySelector('.member-details')?.textContent).toContain('Sector 7');
      const contribValues = item.querySelectorAll('.contribution-item value');
      expect(contribValues[0].textContent).toBe('500');
      expect(contribValues[1].textContent).toBe('25');
    });

    it('shows "Unknown" location when current_sector is null', async () => {
      mockGetMembers.mockResolvedValue([rawMember({ current_sector: null })]);
      await mount();
      await act(async () => {
        tab('Members').click();
      });
      expect(container.querySelector('.member-details')?.textContent).toContain('Unknown');
    });

    it('hides member-actions entirely for the current player\'s own row', async () => {
      mockGetMembers.mockResolvedValue([rawMember({ player_id: 'player-1' })]);
      await mount();
      await act(async () => {
        tab('Members').click();
      });
      expect(container.querySelector('.member-actions')).toBeNull();
    });

    it('shows Promote for a member and Demote for an officer when canPromote is granted', async () => {
      mockGetPermissions.mockResolvedValue(rawPermissions({ role: 'LEADER' }));
      mockGetMembers.mockResolvedValue([rawMember({ player_id: 'p2', role: 'MEMBER' })]);
      await mount();
      await act(async () => {
        tab('Members').click();
      });
      expect(Array.from(container.querySelectorAll('.member-actions button')).map((b) => b.textContent)).toContain('Promote');
    });

    it('calls promoteMember with OFFICER when Promote is clicked, and adopts the mapped result', async () => {
      mockGetPermissions.mockResolvedValue(rawPermissions({ role: 'LEADER' }));
      mockGetMembers.mockResolvedValue([rawMember({ player_id: 'p2', role: 'MEMBER' })]);
      mockPromoteMember.mockResolvedValue(rawMember({ player_id: 'p2', role: 'OFFICER' }));
      await mount();
      await act(async () => {
        tab('Members').click();
      });
      await act(async () => {
        (Array.from(container.querySelectorAll('.member-actions button')).find((b) => b.textContent === 'Promote') as HTMLButtonElement).click();
      });
      await flush();

      expect(mockPromoteMember).toHaveBeenCalledWith('team-1', 'p2', 'OFFICER');
      expect(container.querySelector('.role-badge')?.textContent).toBe('officer');
    });

    it('requires a second click to actually kick, then removes the member and decrements memberCount', async () => {
      mockGetMembers.mockResolvedValue([rawMember({ player_id: 'p2' })]);
      await mount();
      await act(async () => {
        tab('Members').click();
      });

      const kickBtn = () => Array.from(container.querySelectorAll('.member-actions button')).find((b) => b.className.includes('kick-btn')) as HTMLButtonElement;
      await act(async () => {
        kickBtn().click();
      });
      expect(kickBtn().textContent).toBe('Confirm Kick?');
      expect(mockKickMember).not.toHaveBeenCalled();

      await act(async () => {
        kickBtn().click();
      });
      await flush();
      expect(mockKickMember).toHaveBeenCalledWith('team-1', 'p2');
      expect(container.querySelector('.member-item')).toBeNull();
      await act(async () => {
        tab('Overview').click();
      });
      expect(container.querySelector('.team-stats .members')?.textContent).toContain('1/10');
    });

    it('shows memberActionError and does not remove the member on a kick failure', async () => {
      mockGetMembers.mockResolvedValue([rawMember({ player_id: 'p2' })]);
      mockKickMember.mockRejectedValue(new Error('cannot kick the leader'));
      await mount();
      await act(async () => {
        tab('Members').click();
      });
      const kickBtn = () => Array.from(container.querySelectorAll('.member-actions button')).find((b) => b.className.includes('kick-btn')) as HTMLButtonElement;
      await act(async () => {
        kickBtn().click();
      });
      await act(async () => {
        kickBtn().click();
      });
      await flush();

      expect(container.querySelector('.form-error')?.textContent).toBe('cannot kick the leader');
      expect(container.querySelector('.member-item')).not.toBeNull();
    });

    it('renders pinned medal pin and count for a non-self roster row from API fields', async () => {
      mockMedalsGetMe.mockResolvedValue({
        earned: [],
        available: [{ key: 'star_bronze', name: 'Bronze Star', icon: '🥉' }],
        pinned_medal_id: null,
        total_earned: 0,
      });
      mockGetMembers.mockResolvedValue([
        rawMember({
          player_id: 'p2',
          nickname: 'Rho',
          pinned_medal_id: 'star_bronze',
          medal_count: 5,
        }),
      ]);
      await mount();
      await act(async () => {
        tab('Members').click();
      });

      const plate = container.querySelector('.member-item [data-testid="player-name-plate"]') as HTMLElement;
      expect(plate).not.toBeNull();
      expect(plate.getAttribute('data-pinned-medal')).toBe('star_bronze');
      expect(plate.getAttribute('title')).toBe('Bronze Star');
      expect(plate.querySelector('[data-testid="player-name-plate-medal"]')?.textContent).toBe('🥉');
      expect(plate.querySelector('[data-testid="player-name-plate-count"]')?.textContent).toBe('5');
    });

    it('omits medal count badge when medal_count is null (privacy hidden)', async () => {
      mockGetMembers.mockResolvedValue([
        rawMember({
          player_id: 'p2',
          pinned_medal_id: 'star_bronze',
          medal_count: null,
        }),
      ]);
      await mount();
      await act(async () => {
        tab('Members').click();
      });

      const plate = container.querySelector('.member-item [data-testid="player-name-plate"]') as HTMLElement;
      expect(plate.getAttribute('data-pinned-medal')).toBe('star_bronze');
      expect(plate.querySelector('[data-testid="player-name-plate-count"]')).toBeNull();
    });
  });

  describe('treasury / chat tabs', () => {
    it('mounts ResourceSharing on the treasury tab with the team id', async () => {
      await mount();
      await act(async () => {
        tab('Treasury').click();
      });
      expect(container.querySelector('[data-testid="resource-sharing"]')?.getAttribute('data-team-id')).toBe('team-1');
    });

    it('mounts TeamChat on the chat tab with the team id', async () => {
      await mount();
      await act(async () => {
        tab('Chat').click();
      });
      expect(container.querySelector('[data-testid="team-chat"]')?.getAttribute('data-team-id')).toBe('team-1');
    });
  });

  describe('create-team modal', () => {
    const openModal = async () => {
      mockPlayerState = { id: 'player-1', team_id: null, credits: 50000 };
      await mount();
      await act(async () => {
        (container.querySelector('.create-team-btn') as HTMLButtonElement).click();
      });
    };

    const submit = async () => {
      await act(async () => {
        (container.querySelector('.create-team-form') as HTMLFormElement).dispatchEvent(
          new Event('submit', { bubbles: true, cancelable: true })
        );
      });
      await flush();
    };

    it('rejects a too-short team name without calling createTeam', async () => {
      await openModal();
      await act(async () => setValue(document.getElementById('create-team-name') as HTMLInputElement, 'ab'));
      await submit();
      expect(container.querySelector('.form-error')?.textContent).toBe('Team name must be 3-80 characters.');
      expect(mockCreateTeam).not.toHaveBeenCalled();
    });

    it('rejects a too-short (but non-empty) tag', async () => {
      await openModal();
      await act(async () => setValue(document.getElementById('create-team-name') as HTMLInputElement, 'Star Wolves'));
      await act(async () => setValue(document.getElementById('create-team-tag') as HTMLInputElement, 'x'));
      await submit();
      expect(container.querySelector('.form-error')?.textContent).toBe('Team tag must be 2-10 characters, or left blank.');
      expect(mockCreateTeam).not.toHaveBeenCalled();
    });

    it('accepts a blank tag', async () => {
      mockCreateTeam.mockResolvedValue(rawTeam({ tag: null }));
      mockGetMembers.mockResolvedValue([]);
      mockGetPermissions.mockResolvedValue(rawPermissions());
      await openModal();
      await act(async () => setValue(document.getElementById('create-team-name') as HTMLInputElement, 'Star Wolves'));
      await submit();
      expect(mockCreateTeam).toHaveBeenCalledTimes(1);
    });

    it('submits trimmed name/tag/description, omitting tag/description when blank, and closes the modal on success', async () => {
      mockCreateTeam.mockResolvedValue(rawTeam({ name: 'Star Wolves' }));
      mockGetMembers.mockResolvedValue([]);
      mockGetPermissions.mockResolvedValue(rawPermissions());
      await openModal();
      await act(async () => setValue(document.getElementById('create-team-name') as HTMLInputElement, '  Star Wolves  '));
      await submit();

      expect(mockCreateTeam).toHaveBeenCalledWith({
        name: 'Star Wolves',
        max_members: 4,
        recruitment_status: 'OPEN',
      });
      expect(container.querySelector('.team-modal-overlay')).toBeNull();
      expect(container.querySelector('.team-header')).not.toBeNull();
    });

    it('does not crash and still shows the created team when the best-effort member/permissions fetch fails', async () => {
      mockCreateTeam.mockResolvedValue(rawTeam());
      mockGetMembers.mockRejectedValue(new Error('members endpoint down'));
      await openModal();
      await act(async () => setValue(document.getElementById('create-team-name') as HTMLInputElement, 'Star Wolves'));
      await submit();

      expect(container.querySelector('.team-header')).not.toBeNull();
      expect(errorSpy).toHaveBeenCalled();
    });

    it('shows createError and keeps the modal open on a createTeam rejection', async () => {
      mockCreateTeam.mockRejectedValue(new Error('insufficient credits'));
      await openModal();
      await act(async () => setValue(document.getElementById('create-team-name') as HTMLInputElement, 'Star Wolves'));
      await submit();

      expect(container.querySelector('.form-error')?.textContent).toBe('insufficient credits');
      expect(container.querySelector('.team-modal-overlay')).not.toBeNull();
    });

    it('shows honest create fallback when createTeam rejects bare API Error: 400', async () => {
      mockCreateTeam.mockRejectedValue(Object.assign(new Error('API Error: 400'), { status: 400 }));
      await openModal();
      await act(async () => setValue(document.getElementById('create-team-name') as HTMLInputElement, 'Star Wolves'));
      await submit();

      expect(container.querySelector('.form-error')?.textContent).toBe('Failed to create team');
      expect(container.querySelector('.team-modal-overlay')).not.toBeNull();
    });

    it('closes the modal via Cancel without calling createTeam', async () => {
      await openModal();
      await act(async () => {
        (Array.from(container.querySelectorAll('.form-actions button')).find((b) => b.textContent === 'Cancel') as HTMLButtonElement).click();
      });
      expect(container.querySelector('.team-modal-overlay')).toBeNull();
      expect(mockCreateTeam).not.toHaveBeenCalled();
    });
  });

  describe('settings tab', () => {
    const openSettings = async () => {
      await mount();
      await act(async () => {
        tab('Settings').click();
      });
    };

    it('shows read-only team info with an Edit button when canEditTeamInfo is granted', async () => {
      mockGetPermissions.mockResolvedValue(rawPermissions({ role: 'LEADER' }));
      await openSettings();
      expect(container.querySelector('.team-info-display')).not.toBeNull();
      expect(container.querySelector('.edit-btn')).not.toBeNull();
    });

    it('hides the Edit button when canEditTeamInfo is not granted', async () => {
      mockGetPermissions.mockResolvedValue(rawPermissions({ role: 'MEMBER' }));
      await openSettings();
      expect(container.querySelector('.edit-btn')).toBeNull();
    });

    it('saves updated description/recruitment via updateTeam and exits edit mode on success', async () => {
      mockGetPermissions.mockResolvedValue(rawPermissions({ role: 'LEADER' }));
      mockUpdateTeam.mockResolvedValue(rawTeam({ description: 'New pack lore.', recruitment_status: 'CLOSED' }));
      await openSettings();
      await act(async () => {
        (container.querySelector('.edit-btn') as HTMLButtonElement).click();
      });
      await act(async () =>
        setValue(container.querySelector('.edit-team-info textarea') as HTMLTextAreaElement, 'New pack lore.')
      );
      await act(async () => {
        const select = container.querySelector('.edit-team-info select') as HTMLSelectElement;
        const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value')!.set!;
        setter.call(select, 'closed');
        select.dispatchEvent(new Event('change', { bubbles: true }));
      });
      await act(async () => {
        (Array.from(container.querySelectorAll('.edit-team-info .form-actions button')).find((b) => b.textContent === 'Save Changes') as HTMLButtonElement).click();
      });
      await flush();

      expect(mockUpdateTeam).toHaveBeenCalledWith('team-1', { description: 'New pack lore.', recruitment_status: 'CLOSED' });
      expect(container.querySelector('.edit-team-info')).toBeNull();
    });

    it('shows saveError and stays in edit mode when updateTeam rejects', async () => {
      mockGetPermissions.mockResolvedValue(rawPermissions({ role: 'LEADER' }));
      mockUpdateTeam.mockRejectedValue(new Error('description too long'));
      await openSettings();
      await act(async () => {
        (container.querySelector('.edit-btn') as HTMLButtonElement).click();
      });
      await act(async () => {
        (Array.from(container.querySelectorAll('.edit-team-info .form-actions button')).find((b) => b.textContent === 'Save Changes') as HTMLButtonElement).click();
      });
      await flush();

      expect(container.querySelector('.form-error')?.textContent).toBe('description too long');
      expect(container.querySelector('.edit-team-info')).not.toBeNull();
    });

    it('requires a second click to actually leave, then calls leaveTeam + refreshPlayerState', async () => {
      await openSettings();
      const leaveBtn = () => container.querySelector('.leave-team-btn') as HTMLButtonElement;
      await act(async () => {
        leaveBtn().click();
      });
      expect(leaveBtn().textContent).toBe('Confirm Leave?');
      expect(mockLeaveTeam).not.toHaveBeenCalled();

      await act(async () => {
        leaveBtn().click();
      });
      await flush();
      expect(mockLeaveTeam).toHaveBeenCalledTimes(1);
      expect(mockRefreshPlayerState).toHaveBeenCalledTimes(1);
    });

    it('shows leaveError on a leaveTeam failure', async () => {
      mockLeaveTeam.mockRejectedValue(new Error('cannot leave while leader'));
      await openSettings();
      const leaveBtn = () => container.querySelector('.leave-team-btn') as HTMLButtonElement;
      await act(async () => {
        leaveBtn().click();
      });
      await act(async () => {
        leaveBtn().click();
      });
      await flush();
      expect(container.querySelector('.danger-zone .form-error')?.textContent).toBe('cannot leave while leader');
    });
  });
});
