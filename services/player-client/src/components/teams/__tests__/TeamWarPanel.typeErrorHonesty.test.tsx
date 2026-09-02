// @vitest-environment jsdom
/**
 * LEG-3075 / LEG-3362 — TeamWarPanel TypeError + axios Network Error densify.
 * Load/action must not surface raw Failed to fetch / Network Error / TypeError.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { WarEntryApiResponse } from '../../../types/team';
import {
  formatTeamWarLoadError,
  formatTeamWarActionError,
  TeamWarPanel,
} from '../TeamWarPanel';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { mockListWars } = vi.hoisted(() => ({
  mockListWars: vi.fn<(teamId: string, status?: string) => Promise<WarEntryApiResponse[]>>(),
}));

vi.mock('../../../services/api', () => ({
  teamAPI: {
    listWars: mockListWars,
    declareWar: vi.fn(),
    ceasefire: vi.fn(),
  },
}));

vi.mock('../../../services/websocket', () => ({
  default: {
    onTeamWarVictory: vi.fn(() => () => undefined),
  },
}));

describe('TeamWarPanel TypeError densify (LEG-3075)', () => {
  it('formatTeamWarLoadError falls back on TypeError network collapse', () => {
    const text = formatTeamWarLoadError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to load wars');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatTeamWarActionError falls back on TypeError network collapse', () => {
    const text = formatTeamWarActionError(new TypeError('Failed to fetch'));
    expect(text).toBe('Action failed');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatTeamWarLoad/Action fall back on axios Network Error / Failed to fetch (LEG-3362)', () => {
    expect(formatTeamWarLoadError(new Error('Network Error'))).toBe('Failed to load wars');
    expect(formatTeamWarLoadError(new Error('Failed to fetch'))).toBe('Failed to load wars');
    expect(formatTeamWarActionError(new Error('Network Error'))).toBe('Action failed');
    expect(formatTeamWarActionError(new Error('Failed to fetch'))).toBe('Action failed');
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatTeamWarLoadError(new Error('wars_unavailable'))).toBe('wars_unavailable');
    expect(formatTeamWarActionError(new Error('ceasefire_denied'))).toBe('ceasefire_denied');
  });
});

describe('TeamWarPanel network collapse DOM (LEG-3362)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockListWars.mockReset();
    mockListWars.mockRejectedValue(new Error('Network Error'));
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

  it('does not render raw Network Error when listWars rejects', async () => {
    await act(async () => {
      root.render(<TeamWarPanel teamId="team-1" isLeader={false} />);
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    const alert = container.querySelector('[data-testid="team-war-load-error"]');
    expect(alert).not.toBeNull();
    expect(alert?.textContent).toBe('Failed to load wars');
    expect(alert?.textContent).not.toMatch(/Network Error/i);
  });
});


describe('TeamWarPanel 403/429 densify (LEG-4091)', () => {
  const apiRequestError = (status: number, message?: string) => {
    const err = new Error(message ?? `API Error: ${status}`);
    (err as { status?: number }).status = status;
    return err;
  };
  it('surfaces 403/429 without raw status codes', () => {
    expect(formatTeamWarLoadError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatTeamWarLoadError(apiRequestError(403, 'wars_denied'))).toBe('wars_denied');
    expect(formatTeamWarLoadError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatTeamWarActionError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatTeamWarActionError(apiRequestError(403, 'ceasefire_denied'))).toBe(
      'ceasefire_denied',
    );
    expect(formatTeamWarActionError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatTeamWarActionError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatTeamWarActionError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});
