// @vitest-environment jsdom
/**
 * LEG-3752 Soft-ORDER — MissionPlanner TypeError/network densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const getMissions = vi.fn();
const getMembers = vi.fn();
const joinMission = vi.fn();

vi.mock('../../../services/api', () => ({
  teamAPI: {
    getMissions: (...args: unknown[]) => getMissions(...args),
    getMembers: (...args: unknown[]) => getMembers(...args),
    createMission: vi.fn(),
    joinMission: (...args: unknown[]) => joinMission(...args),
    leaveMission: vi.fn(),
    updateMission: vi.fn(),
  },
}));

import {
  MissionPlanner,
  formatMissionPlannerLoadError,
  formatMissionPlannerMutationError,
  MISSION_PLANNER_LOAD_FALLBACK,
} from '../MissionPlanner';

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
};

describe('MissionPlanner TypeError densify (LEG-3752)', () => {
  it('formatMissionPlannerLoadError falls back on TypeError network collapse', () => {
    const text = formatMissionPlannerLoadError(new TypeError('Failed to fetch'));
    expect(text).toBe(MISSION_PLANNER_LOAD_FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatMissionPlannerMutationError falls back on TypeError network collapse', () => {
    const text = formatMissionPlannerMutationError(
      new TypeError('Failed to fetch'),
      'Failed to create mission',
    );
    expect(text).toBe('Failed to create mission');
    expect(text).not.toMatch(/Failed to fetch/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatMissionPlannerLoadError(new Error('Network Error'))).toBe(MISSION_PLANNER_LOAD_FALLBACK);
    expect(formatMissionPlannerLoadError(new Error('Failed to fetch'))).toBe(MISSION_PLANNER_LOAD_FALLBACK);
    expect(formatMissionPlannerMutationError(new Error('Network Error'), 'Failed to join mission')).toBe(
      'Failed to join mission',
    );
  });

  it('preserves non-generic server detail when not transport collapse', () => {
    expect(formatMissionPlannerMutationError(new Error('mission_locked'), 'Failed to start mission')).toBe(
      'mission_locked',
    );
  });
});

describe('MissionPlanner load transport collapse densify (LEG-3752)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    getMissions.mockReset();
    getMembers.mockReset();
    joinMission.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it('load rejection surfaces .mp-error role=alert fallback without raw transport text', async () => {
    getMissions.mockRejectedValue(new Error('Network Error'));
    getMembers.mockResolvedValue([]);

    await act(async () => {
      root.render(
        <MissionPlanner teamId="team-1" playerId="player-1" canStartMissions={false} />,
      );
    });
    await flush();

    const alert = container.querySelector('.mp-error[role="alert"]');
    expect(alert?.textContent).toBe(MISSION_PLANNER_LOAD_FALLBACK);
    expect(container.textContent).not.toMatch(/Network Error/i);
  });

  it('join mission TypeError surfaces mutation fallback without raw transport text', async () => {
    getMissions.mockResolvedValue([
      {
        id: 'm1',
        name: 'Secure Sector',
        description: 'Hold the line',
        type: 'defense',
        status: 'planning',
        participants: [],
        objectives: [],
        createdBy: 'player-2',
        createdAt: '2026-01-01T00:00:00Z',
      },
    ]);
    getMembers.mockResolvedValue([
      { playerId: 'player-2', playerName: 'Leader', role: 'LEADER', online: true },
    ]);
    joinMission.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      root.render(
        <MissionPlanner teamId="team-1" playerId="player-1" canStartMissions={false} />,
      );
    });
    await flush();

    const joinBtn = container.querySelector('.join-btn') as HTMLButtonElement;
    expect(joinBtn).toBeTruthy();
    await act(async () => {
      joinBtn.click();
    });
    await flush();

    const alert = container.querySelector('.mp-error[role="alert"]');
    expect(alert?.textContent).toBe('Failed to join mission');
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
  });
});

describe('MissionPlanner 403/429 densify (LEG-4081)', () => {
  const apiRequestError = (status: number, message?: string) => {
    const err = new Error(message ?? `API Error: ${status}`);
    (err as { status?: number }).status = status;
    return err;
  };
  it('load + mutation formatters surface 403/429 without raw status codes', () => {
    expect(formatMissionPlannerLoadError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatMissionPlannerLoadError(apiRequestError(403, 'missions_denied'))).toBe(
      'missions_denied',
    );
    expect(formatMissionPlannerLoadError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatMissionPlannerLoadError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(
      formatMissionPlannerMutationError(apiRequestError(403), 'Failed to join mission'),
    ).toMatch(/permission/i);
    expect(
      formatMissionPlannerMutationError(apiRequestError(429), 'Failed to join mission'),
    ).toMatch(/rate limit/i);
    expect(formatMissionPlannerLoadError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});
