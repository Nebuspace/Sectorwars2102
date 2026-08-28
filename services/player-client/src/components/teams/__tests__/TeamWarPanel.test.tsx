// @vitest-environment jsdom
/**
 * TeamWarPanel — list / leader declare+ceasefire / error / victory rendering.
 * Seam matches TeamManager.test.tsx (jsdom + createRoot + act, no RTL).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { WarEntryApiResponse } from '../../../types/team';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const {
  mockListWars,
  mockDeclareWar,
  mockCeasefire,
  mockOnTeamWarVictory,
} = vi.hoisted(() => ({
  mockListWars: vi.fn<(teamId: string, status?: string) => Promise<WarEntryApiResponse[]>>(),
  mockDeclareWar: vi.fn<(teamId: string, target: string, reason?: string) => Promise<unknown>>(),
  mockCeasefire: vi.fn<(teamId: string, target: string) => Promise<unknown>>(),
  mockOnTeamWarVictory: vi.fn<(cb: () => void) => () => void>(),
}));

vi.mock('../../../services/api', () => ({
  teamAPI: {
    listWars: mockListWars,
    declareWar: mockDeclareWar,
    ceasefire: mockCeasefire,
  },
}));

vi.mock('../../../services/websocket', () => ({
  default: {
    onTeamWarVictory: mockOnTeamWarVictory,
  },
}));

import { TeamWarPanel, formatTeamWarLoadError, formatTeamWarActionError } from '../TeamWarPanel';

const activeWar = (overrides: Partial<WarEntryApiResponse> = {}): WarEntryApiResponse => ({
  target_team_id: 'enemy-team-aaaaaaaa',
  declared_by: 'player-1',
  declared_at: '2026-08-01T12:00:00Z',
  reason: 'Border dispute',
  status: 'active',
  score: { us: 3, them: 1 },
  ...overrides,
});

const victoryWar = (): WarEntryApiResponse => ({
  target_team_id: 'enemy-team-bbbbbbbb',
  declared_by: 'player-1',
  declared_at: '2026-07-01T12:00:00Z',
  reason: 'Old feud',
  status: 'ceased',
  score: { us: 10, them: 4 },
  cease_reason: 'victory',
  winner_team_id: 'team-1',
  loser_team_id: 'enemy-team-bbbbbbbb',
  victory_at: '2026-07-02T12:00:00Z',
});

function setValue(el: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const proto =
    el instanceof HTMLTextAreaElement ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')!.set!;
  setter.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
}

describe('TeamWarPanel', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockListWars.mockReset();
    mockDeclareWar.mockReset();
    mockCeasefire.mockReset();
    mockOnTeamWarVictory.mockReset();
    mockOnTeamWarVictory.mockImplementation(() => () => undefined);
    mockListWars.mockResolvedValue([activeWar()]);
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

  const flush = async () => {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  const mount = async (props: { isLeader?: boolean } = {}) => {
    await act(async () => {
      root.render(
        <TeamWarPanel teamId="team-1" isLeader={props.isLeader ?? true} />,
      );
    });
    await flush();
  };

  it('lists wars with mirrored score and status', async () => {
    await mount();
    expect(mockListWars).toHaveBeenCalledWith('team-1', undefined);
    expect(container.querySelector('[data-testid="team-war-list"]')).not.toBeNull();
    expect(container.textContent).toContain('Score 3–1');
    expect(container.textContent).toContain('Active');
    expect(container.textContent).toContain('Border dispute');
  });

  it('hides declare/ceasefire controls for non-leaders', async () => {
    await mount({ isLeader: false });
    expect(container.querySelector('[data-testid="team-war-declare"]')).toBeNull();
    expect(container.querySelector('[data-testid="team-war-cease"]')).toBeNull();
    expect(container.querySelector('[data-testid="team-war-leader-only"]')).not.toBeNull();
  });

  it('shows declare controls for leaders and two-click confirms declare', async () => {
    mockDeclareWar.mockResolvedValue({
      success: true,
      message: 'War declared',
      war: activeWar({ target_team_id: 'new-enemy' }),
    });
    mockListWars
      .mockResolvedValueOnce([activeWar()])
      .mockResolvedValueOnce([activeWar(), activeWar({ target_team_id: 'new-enemy' })]);

    await mount({ isLeader: true });
    const input = container.querySelector(
      '[data-testid="team-war-target-input"]',
    ) as HTMLInputElement;
    const btn = container.querySelector(
      '[data-testid="team-war-declare-btn"]',
    ) as HTMLButtonElement;

    await act(async () => {
      setValue(input, 'new-enemy');
    });

    expect(btn.textContent).toContain('Declare war');
    await act(async () => {
      btn.click();
    });
    expect(mockDeclareWar).not.toHaveBeenCalled();
    expect(btn.textContent).toContain('Confirm declare war');

    await act(async () => {
      btn.click();
    });
    await flush();
    expect(mockDeclareWar).toHaveBeenCalledWith('team-1', 'new-enemy', '');
  });

  it('surfaces load 404 server detail', async () => {
    mockListWars.mockRejectedValue(
      Object.assign(new Error('Team not found'), { status: 404 }),
    );
    await mount();
    expect(container.querySelector('[data-testid="team-war-load-error"]')?.textContent).toBe(
      'Team not found',
    );
  });

  it('formatTeamWarLoadError falls back on bare 404 without server detail', () => {
    const err = Object.assign(new Error('API Error: 404'), { status: 404 });
    expect(formatTeamWarLoadError(err)).toBe('Failed to load wars');
  });

  it('surfaces declare 403 non-leader server detail', async () => {
    mockDeclareWar.mockRejectedValue(
      Object.assign(new Error('Only team leader can declare war'), { status: 403 }),
    );
    await mount({ isLeader: true });

    const input = container.querySelector(
      '[data-testid="team-war-target-input"]',
    ) as HTMLInputElement;
    const btn = container.querySelector(
      '[data-testid="team-war-declare-btn"]',
    ) as HTMLButtonElement;

    await act(async () => {
      setValue(input, 'enemy-team-aaaaaaaa');
    });
    await act(async () => {
      btn.click();
    });
    await act(async () => {
      btn.click();
    });
    await flush();

    expect(container.querySelector('[data-testid="team-war-action-error"]')?.textContent).toBe(
      'Only team leader can declare war',
    );
  });

  it('formatTeamWarActionError falls back when detail absent', () => {
    expect(formatTeamWarActionError(new Error('API Error: 500'))).toBe('Action failed');
  });

  it('surfaces declare error payloads', async () => {
    mockDeclareWar.mockRejectedValue(new Error('Already at war with this team'));
    await mount({ isLeader: true });

    const input = container.querySelector(
      '[data-testid="team-war-target-input"]',
    ) as HTMLInputElement;
    const btn = container.querySelector(
      '[data-testid="team-war-declare-btn"]',
    ) as HTMLButtonElement;

    await act(async () => {
      setValue(input, 'enemy-team-aaaaaaaa');
    });
    await act(async () => {
      btn.click();
    });
    await act(async () => {
      btn.click();
    });
    await flush();

    expect(container.querySelector('[data-testid="team-war-action-error"]')?.textContent)
      .toContain('Already at war with this team');
  });

  it('renders victory/ceased outcome when server provides winner fields', async () => {
    mockListWars.mockResolvedValue([victoryWar()]);
    await mount({ isLeader: true });
    expect(container.querySelector('[data-testid="team-war-outcome"]')?.textContent)
      .toMatch(/Victory/i);
    expect(container.textContent).toContain('Ceased — victory');
    expect(container.querySelector('[data-testid="team-war-cease"]')).toBeNull();
  });

  it('two-click ceasefire calls the API', async () => {
    mockCeasefire.mockResolvedValue({
      success: true,
      message: 'Ceasefire declared',
      ceased_by: 'player-1',
    });
    mockListWars
      .mockResolvedValueOnce([activeWar()])
      .mockResolvedValueOnce([]);

    await mount({ isLeader: true });
    const cease = container.querySelector(
      '[data-testid="team-war-cease"]',
    ) as HTMLButtonElement;
    await act(async () => {
      cease.click();
    });
    expect(mockCeasefire).not.toHaveBeenCalled();
    await act(async () => {
      cease.click();
    });
    await flush();
    expect(mockCeasefire).toHaveBeenCalledWith('team-1', 'enemy-team-aaaaaaaa');
  });

  it('subscribes to team_war_victory for refresh', async () => {
    await mount();
    expect(mockOnTeamWarVictory).toHaveBeenCalled();
  });
});
