// @vitest-environment jsdom
/**
 * MissionPlanner — team mission dispatch/coordination panel. jsdom +
 * react-dom/client createRoot + act(), no RTL, matching the LoginForm/
 * ProposePolicyForm form-interaction seam (native value setter +
 * dispatchEvent for controlled inputs).
 *
 * Pins: the loading -> loaded transition (Promise.all over getMissions +
 * getMembers, first mission auto-selected), the create-mission form's
 * required-field alert guard + objective add/update/remove + payload
 * shaping (id/completed synthesized per objective), join/leave/start
 * mission wiring (incl. the canStartMissions + participants>0 gate on
 * Start), mission-list selection + keyboard activation, and the detail
 * pane's objectives/participants/rewards conditional rendering.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { TeamMission, TeamMember } from '../../../types/team';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const {
  mockGetMissions,
  mockGetMembers,
  mockCreateMission,
  mockJoinMission,
  mockLeaveMission,
  mockUpdateMission,
} = vi.hoisted(() => ({
  mockGetMissions: vi.fn<(teamId: string) => Promise<TeamMission[]>>(async () => []),
  mockGetMembers: vi.fn<(teamId: string) => Promise<TeamMember[]>>(async () => []),
  mockCreateMission: vi.fn<(teamId: string, data: unknown) => Promise<TeamMission>>(),
  mockJoinMission: vi.fn<(teamId: string, missionId: string) => Promise<unknown>>(async () => undefined),
  mockLeaveMission: vi.fn<(teamId: string, missionId: string) => Promise<unknown>>(async () => undefined),
  mockUpdateMission: vi.fn<(teamId: string, missionId: string, updates: unknown) => Promise<TeamMission>>(),
}));

vi.mock('../../../services/api', () => ({
  teamAPI: {
    getMissions: mockGetMissions,
    getMembers: mockGetMembers,
    createMission: mockCreateMission,
    joinMission: mockJoinMission,
    leaveMission: mockLeaveMission,
    updateMission: mockUpdateMission,
  },
}));

import { MissionPlanner } from '../MissionPlanner';

const mission = (overrides: Partial<TeamMission> = {}): TeamMission => ({
  id: 'mission-1',
  teamId: 'team-1',
  name: 'Secure Sector 99',
  description: 'Hold the line.',
  type: 'combat',
  status: 'planning',
  createdBy: 'player-leader',
  createdAt: '2026-01-01T00:00:00Z',
  objectives: [],
  participants: [],
  ...overrides,
});

const member = (overrides: Partial<TeamMember> = {}): TeamMember => ({
  id: 'member-1',
  playerId: 'player-leader',
  playerName: 'Nova',
  role: 'leader',
  joinedAt: '2026-01-01T00:00:00Z',
  contributions: { credits: 0, resources: 0, combatKills: 0 },
  online: true,
  location: { sectorId: '1', sectorName: 'Sol' },
  shipType: 'Scout',
  combatRating: 1,
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

describe('MissionPlanner', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let alertSpy: ReturnType<typeof vi.spyOn>;
  let errorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    mockGetMissions.mockReset();
    mockGetMissions.mockResolvedValue([]);
    mockGetMembers.mockReset();
    mockGetMembers.mockResolvedValue([]);
    mockCreateMission.mockReset();
    mockJoinMission.mockReset();
    mockJoinMission.mockResolvedValue(undefined);
    mockLeaveMission.mockReset();
    mockLeaveMission.mockResolvedValue(undefined);
    mockUpdateMission.mockReset();
    alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});
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
    alertSpy.mockRestore();
    errorSpy.mockRestore();
  });

  const mount = async (props: Partial<React.ComponentProps<typeof MissionPlanner>> = {}) => {
    await act(async () => {
      root.render(
        <MissionPlanner teamId="team-1" playerId="player-1" canStartMissions={false} {...props} />
      );
    });
    await flush();
  };

  it('shows a loading state before the initial fetch resolves', async () => {
    let resolveMissions: (v: TeamMission[]) => void = () => {};
    mockGetMissions.mockImplementation(() => new Promise((resolve) => { resolveMissions = resolve; }));
    await act(async () => {
      root.render(<MissionPlanner teamId="team-1" playerId="player-1" canStartMissions={false} />);
    });
    expect(container.querySelector('.mission-planner.loading')?.textContent).toBe('Loading missions...');

    await act(async () => {
      resolveMissions([]);
    });
    await flush();
  });

  it('fetches missions + members for the team and auto-selects the first mission', async () => {
    mockGetMissions.mockResolvedValue([mission({ id: 'm1', name: 'First' }), mission({ id: 'm2', name: 'Second' })]);
    await mount();

    expect(mockGetMissions).toHaveBeenCalledWith('team-1');
    expect(mockGetMembers).toHaveBeenCalledWith('team-1');
    expect(container.querySelector('.detail-header h4')?.textContent).toContain('First');
  });

  it('shows the no-missions empty state, with the create hint only when canStartMissions', async () => {
    await mount({ canStartMissions: false });
    expect(container.querySelector('.no-missions p')?.textContent).toBe('No active missions');
    expect(container.querySelector('.no-missions .hint')).toBeNull();

    await act(async () => {
      root.unmount();
    });
    root = createRoot(container);
    await mount({ canStartMissions: true });
    expect(container.querySelector('.no-missions .hint')?.textContent).toBe(
      'Create a mission to coordinate team efforts'
    );
  });

  it('only shows the Create New Mission button when canStartMissions is true', async () => {
    await mount({ canStartMissions: false });
    expect(container.querySelector('.create-mission-btn')).toBeNull();
  });

  describe('create-mission form', () => {
    const openForm = async () => {
      await mount({ canStartMissions: true });
      await act(async () => {
        (container.querySelector('.create-mission-btn') as HTMLButtonElement).click();
      });
    };

    it('alerts and does not call createMission when name or description is blank', async () => {
      await openForm();
      await act(async () => {
        (Array.from(container.querySelectorAll('.form-actions button')).find(
          (b) => b.textContent === 'Create Mission'
        ) as HTMLButtonElement).click();
      });
      expect(alertSpy).toHaveBeenCalledWith('Please fill in mission name and description');
      expect(mockCreateMission).not.toHaveBeenCalled();
    });

    it('creates a mission with synthesized objective ids/completed flags and adopts it as selected', async () => {
      mockCreateMission.mockResolvedValue(mission({ id: 'new-mission', name: 'New Op' }));
      await openForm();

      await act(async () => setValue(container.querySelector('input[type="text"]') as HTMLInputElement, 'New Op'));
      await act(async () =>
        setValue(container.querySelector('textarea') as HTMLTextAreaElement, 'Do the thing')
      );
      await act(async () => {
        (container.querySelector('.add-objective-btn') as HTMLButtonElement).click();
      });
      await act(async () =>
        setValue(container.querySelector('.objective-input input[type="text"]') as HTMLInputElement, 'Destroy the outpost')
      );

      await act(async () => {
        (Array.from(container.querySelectorAll('.form-actions button')).find(
          (b) => b.textContent === 'Create Mission'
        ) as HTMLButtonElement).click();
      });
      await flush();

      expect(mockCreateMission).toHaveBeenCalledTimes(1);
      const [teamId, payload] = mockCreateMission.mock.calls[0] as [string, any];
      expect(teamId).toBe('team-1');
      expect(payload.name).toBe('New Op');
      expect(payload.description).toBe('Do the thing');
      expect(payload.objectives).toHaveLength(1);
      expect(payload.objectives[0]).toMatchObject({ description: 'Destroy the outpost', type: 'destroy', completed: false });
      expect(payload.objectives[0].id).toMatch(/^obj-/);

      // Form closes and the new mission becomes selected.
      expect(container.querySelector('.create-mission-form')).toBeNull();
      expect(container.querySelector('.detail-header h4')?.textContent).toContain('New Op');
    });

    it('removes an objective row via its remove button', async () => {
      await openForm();
      await act(async () => {
        (container.querySelector('.add-objective-btn') as HTMLButtonElement).click();
      });
      expect(container.querySelectorAll('.objective-input').length).toBe(1);

      await act(async () => {
        (container.querySelector('.objective-input .remove-btn') as HTMLButtonElement).click();
      });
      expect(container.querySelectorAll('.objective-input').length).toBe(0);
    });

    it('cancels the form without calling createMission', async () => {
      await openForm();
      await act(async () => {
        (container.querySelector('.cancel-btn') as HTMLButtonElement).click();
      });
      expect(container.querySelector('.create-mission-form')).toBeNull();
      expect(mockCreateMission).not.toHaveBeenCalled();
    });
  });

  describe('mission list + selection', () => {
    it('selects a mission on click and marks it aria-pressed', async () => {
      mockGetMissions.mockResolvedValue([mission({ id: 'm1', name: 'Alpha' }), mission({ id: 'm2', name: 'Beta' })]);
      await mount();

      const items = container.querySelectorAll('.mission-item');
      expect(items[0].getAttribute('aria-pressed')).toBe('true');
      expect(items[1].getAttribute('aria-pressed')).toBe('false');

      await act(async () => {
        (items[1] as HTMLElement).click();
      });
      expect(container.querySelector('.detail-header h4')?.textContent).toContain('Beta');
    });

    it('selects a mission via Enter/Space keydown', async () => {
      mockGetMissions.mockResolvedValue([mission({ id: 'm1', name: 'Alpha' }), mission({ id: 'm2', name: 'Beta' })]);
      await mount();

      const second = container.querySelectorAll('.mission-item')[1] as HTMLElement;
      await act(async () => {
        second.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
      });
      expect(container.querySelector('.detail-header h4')?.textContent).toContain('Beta');
    });
  });

  describe('join / leave / start mission', () => {
    it('shows Join Mission when the player has not joined, and calls joinMission on click', async () => {
      mockGetMissions.mockResolvedValue([mission({ id: 'm1', participants: [] })]);
      await mount({ playerId: 'player-1' });

      const joinBtn = container.querySelector('.join-btn') as HTMLButtonElement;
      expect(joinBtn.textContent).toBe('Join Mission');
      await act(async () => {
        joinBtn.click();
      });
      await flush();
      expect(mockJoinMission).toHaveBeenCalledWith('team-1', 'm1');
    });

    it('shows Leave Mission when the player has already joined, and calls leaveMission on click', async () => {
      mockGetMissions.mockResolvedValue([mission({ id: 'm1', participants: ['player-1'] })]);
      await mount({ playerId: 'player-1' });

      const leaveBtn = container.querySelector('.leave-btn') as HTMLButtonElement;
      expect(leaveBtn).not.toBeNull();
      await act(async () => {
        leaveBtn.click();
      });
      await flush();
      expect(mockLeaveMission).toHaveBeenCalledWith('team-1', 'm1');
    });

    it('shows Start Mission only when canStartMissions AND at least one participant has joined', async () => {
      mockGetMissions.mockResolvedValue([mission({ id: 'm1', participants: [] })]);
      await mount({ canStartMissions: true });
      expect(container.querySelector('.start-btn')).toBeNull();
    });

    it('calls updateMission with status=active and adopts the server response on Start', async () => {
      mockGetMissions.mockResolvedValue([mission({ id: 'm1', participants: ['player-1'] })]);
      mockUpdateMission.mockResolvedValue(mission({ id: 'm1', participants: ['player-1'], status: 'active' }));
      await mount({ canStartMissions: true, playerId: 'player-1' });

      await act(async () => {
        (container.querySelector('.start-btn') as HTMLButtonElement).click();
      });
      await flush();

      expect(mockUpdateMission).toHaveBeenCalledWith('team-1', 'm1', expect.objectContaining({ status: 'active' }));
      expect(container.querySelector('.mission-status')?.textContent).toBe('ACTIVE');
    });

    it('shows the in-progress hint for an active mission the player has joined, with no join/leave/start buttons', async () => {
      mockGetMissions.mockResolvedValue([mission({ id: 'm1', status: 'active', participants: ['player-1'] })]);
      await mount({ playerId: 'player-1' });

      expect(container.querySelector('.active-hint')?.textContent).toBe('Mission in progress - complete objectives!');
      expect(container.querySelector('.join-btn')).toBeNull();
      expect(container.querySelector('.leave-btn')).toBeNull();
      expect(container.querySelector('.start-btn')).toBeNull();
    });
  });

  describe('detail pane rendering', () => {
    it('shows "No specific objectives set" when the mission has none', async () => {
      mockGetMissions.mockResolvedValue([mission({ objectives: [] })]);
      await mount();
      expect(container.querySelector('.no-objectives')?.textContent).toBe('No specific objectives set');
    });

    it('renders objectives with completed check state and progress fraction when present', async () => {
      mockGetMissions.mockResolvedValue([
        mission({
          objectives: [
            { id: 'o1', description: 'Destroy outposts', type: 'destroy', completed: true, requiredAmount: 3, currentAmount: 3 },
            { id: 'o2', description: 'Scout the perimeter', type: 'explore', completed: false },
          ],
        }),
      ]);
      await mount();

      const objs = container.querySelectorAll('.objective');
      expect(objs[0].querySelector('.objective-check')?.textContent).toBe('✓');
      expect(objs[0].querySelector('.objective-progress')?.textContent).toBe('3/3');
      expect(objs[1].querySelector('.objective-check')?.textContent).toBe('○');
      expect(objs[1].querySelector('.objective-progress')).toBeNull();
    });

    it('resolves participant names/roles from the members list and skips unknown participant ids', async () => {
      mockGetMissions.mockResolvedValue([mission({ participants: ['player-leader', 'ghost-player'] })]);
      mockGetMembers.mockResolvedValue([member({ playerId: 'player-leader', playerName: 'Nova', online: true })]);
      await mount();

      const rows = container.querySelectorAll('.participant');
      expect(rows.length).toBe(1);
      expect(rows[0].querySelector('.participant-name')?.textContent).toContain('Nova');
      expect(rows[0].querySelector('.online-dot')).not.toBeNull();
    });

    it('renders reward lines only for the reward fields that are present', async () => {
      mockGetMissions.mockResolvedValue([mission({ rewards: { credits: 5000 } })]);
      await mount();

      const rewardItems = container.querySelectorAll('.reward-item');
      expect(rewardItems.length).toBe(1);
      expect(container.querySelector('.mission-rewards')?.textContent).toContain('5,000');
    });

    it('renders no rewards section at all when the mission has no rewards', async () => {
      mockGetMissions.mockResolvedValue([mission({ rewards: undefined })]);
      await mount();
      expect(container.querySelector('.mission-rewards')).toBeNull();
    });
  });
});
