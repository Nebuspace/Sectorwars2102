// @vitest-environment jsdom
/**
 * FirstLoginContext — TypeError/network collapse densify (LEG-3318).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const { mockGetStatus, mockStartSession } = vi.hoisted(() => ({
  mockGetStatus: vi.fn(),
  mockStartSession: vi.fn(),
}));

vi.mock('../../services/apiClient', () => ({
  default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual<typeof import('../../services/api')>('../../services/api');
  return {
    ...actual,
    firstLoginAPI: {
      getStatus: (...a: unknown[]) => mockGetStatus(...a),
      startSession: (...a: unknown[]) => mockStartSession(...a),
      claimShip: vi.fn(),
      submitDialogue: vi.fn(),
      complete: vi.fn(),
      resetSession: vi.fn(),
    },
  };
});

vi.mock('../AuthContext', () => ({
  useAuth: () => ({ user: { id: 'player-1' }, isAuthenticated: true }),
}));

import {
  FirstLoginProvider,
  useFirstLogin,
  formatFirstLoginError,
} from '../FirstLoginContext';

let captured: ReturnType<typeof useFirstLogin> | null = null;
function Consumer() {
  captured = useFirstLogin();
  return null;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('FirstLoginContext network collapse (LEG-3318)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(async () => {
    captured = null;
    mockGetStatus.mockReset();
    mockStartSession.mockReset();
    mockGetStatus.mockResolvedValue({ requires_first_login: false });
    mockStartSession.mockResolvedValue({
      session_id: 's1',
      player_id: 'p1',
      available_ships: ['SCOUT'],
      current_step: 'ship_selection',
      npc_prompt: 'Hello',
    });

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(
        <FirstLoginProvider>
          <Consumer />
        </FirstLoginProvider>,
      );
      await flush();
      await flush();
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.clearAllMocks();
  });

  it('formatFirstLoginError collapses TypeError / Network Error / Failed to fetch', () => {
    expect(formatFirstLoginError(new TypeError('Failed to fetch'), 'Failed to check first login status.')).toBe(
      'Failed to check first login status.',
    );
    expect(formatFirstLoginError(new Error('Network Error'), 'Failed to start first login session.')).toBe(
      'Failed to start first login session.',
    );
    expect(formatFirstLoginError(new Error('Failed to fetch'), 'Failed to start first login session.')).toBe(
      'Failed to start first login session.',
    );
    const statusText = formatFirstLoginError(new TypeError('Failed to fetch'), 'Failed to check first login status.');
    expect(statusText).not.toMatch(/Failed to fetch/i);
    expect(statusText).not.toMatch(/TypeError/i);
  });

  it('checkFirstLoginStatus TypeError surfaces stable fallback without raw network tokens', async () => {
    mockGetStatus.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    await act(async () => {
      await captured!.checkFirstLoginStatus();
      await flush();
    });
    expect(captured!.error).toBe('Failed to check first login status.');
    expect(captured!.error).not.toMatch(/Failed to fetch/i);
    expect(captured!.error).not.toMatch(/TypeError/i);
  });

  it('startSession TypeError surfaces stable fallback without raw network tokens', async () => {
    mockStartSession.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    await act(async () => {
      await captured!.startSession();
      await flush();
    });
    expect(captured!.error).toBe('Failed to start first login session.');
    expect(captured!.error).not.toMatch(/Failed to fetch/i);
    expect(captured!.error).not.toMatch(/TypeError/i);
  });
});
