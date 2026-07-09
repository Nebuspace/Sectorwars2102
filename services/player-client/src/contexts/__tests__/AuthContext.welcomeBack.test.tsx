// @vitest-environment jsdom
/**
 * AuthContext — welcome-back signal (WO-PUX-WBACK-SURFACE).
 *
 * Pins login()'s read-only threading of the login response's new
 * `welcome_back` field into `welcomeBackSignal`/`lastWelcomeBack`: a
 * granted:true outcome bumps the (mount-baseline-0) signal exactly once and
 * stores the outcome; granted:false or a null/absent field (the OAuth path,
 * or any login where no bonus was due) is a no-op — no signal bump, no
 * stored outcome. Mirrors WebSocketContext.teammateUnderAttack.test.tsx's
 * real-provider technique: mount the REAL AuthProvider, capture useAuth()
 * via a Consumer, drive it through axios mocks (jsdom, no live server).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const mockPost = vi.fn();
const mockGet = vi.fn();

vi.mock('axios', () => ({
  default: {
    post: (...args: unknown[]) => mockPost(...args),
    get: (...args: unknown[]) => mockGet(...args),
    defaults: { headers: { common: {} as Record<string, string> } },
    interceptors: { response: { use: vi.fn(() => 1), eject: vi.fn() } },
  },
}));

// refreshAccessToken is exercised by its own suite; AuthContext only calls it
// from the 401-retry interceptor and checkAuth's refresh fallback, neither of
// which this suite's login()-only flows reach.
vi.mock('../../services/apiClient', () => ({
  refreshAccessToken: vi.fn(),
}));

import { AuthProvider, useAuth } from '../AuthContext';

let captured: ReturnType<typeof useAuth> | null = null;
function Consumer() {
  captured = useAuth();
  return null;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('AuthContext welcome-back signal', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(async () => {
    localStorage.clear();
    mockPost.mockReset();
    mockGet.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    captured = null;

    act(() => {
      root.render(React.createElement(AuthProvider, null, React.createElement(Consumer)));
    });
    await flush();
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it('starts at signal 0 with no stored outcome (mount baseline)', () => {
    expect(captured!.welcomeBackSignal).toBe(0);
    expect(captured!.lastWelcomeBack).toBeNull();
  });

  it('bumps the signal and stores the outcome on a granted login', async () => {
    mockPost.mockResolvedValueOnce({
      data: {
        access_token: 'tok-a',
        refresh_token: 'tok-r',
        user_id: 'u1',
        welcome_back: { granted: true, bonus: 400, days_inactive: 8 },
      },
    });
    mockGet.mockResolvedValueOnce({ data: { id: 'u1', username: 'commander' } });

    await act(async () => {
      await captured!.login('commander', 'pw-not-checked');
    });

    expect(captured!.welcomeBackSignal).toBe(1);
    expect(captured!.lastWelcomeBack).toEqual({ granted: true, bonus: 400, days_inactive: 8 });
    // Only the JSON login endpoint was hit -- the form fallback never fires
    // on a successful first attempt.
    expect(mockPost).toHaveBeenCalledTimes(1);
  });

  it('does not bump the signal when welcome_back is null (nothing to surface)', async () => {
    mockPost.mockResolvedValueOnce({
      data: { access_token: 'tok-a', refresh_token: 'tok-r', user_id: 'u1', welcome_back: null },
    });
    mockGet.mockResolvedValueOnce({ data: { id: 'u1', username: 'commander' } });

    await act(async () => {
      await captured!.login('commander', 'pw-not-checked');
    });

    expect(captured!.welcomeBackSignal).toBe(0);
    expect(captured!.lastWelcomeBack).toBeNull();
  });

  it('does not bump the signal when the login evaluated a bonus but granted nothing', async () => {
    mockPost.mockResolvedValueOnce({
      data: {
        access_token: 'tok-a',
        refresh_token: 'tok-r',
        user_id: 'u1',
        welcome_back: { granted: false, bonus: 0, days_inactive: 0 },
      },
    });
    mockGet.mockResolvedValueOnce({ data: { id: 'u1', username: 'commander' } });

    await act(async () => {
      await captured!.login('commander', 'pw-not-checked');
    });

    expect(captured!.welcomeBackSignal).toBe(0);
    expect(captured!.lastWelcomeBack).toBeNull();
  });

  it('does not bump the signal when welcome_back is absent entirely (legacy/unknown shape)', async () => {
    mockPost.mockResolvedValueOnce({
      data: { access_token: 'tok-a', refresh_token: 'tok-r', user_id: 'u1' },
    });
    mockGet.mockResolvedValueOnce({ data: { id: 'u1', username: 'commander' } });

    await act(async () => {
      await captured!.login('commander', 'pw-not-checked');
    });

    expect(captured!.welcomeBackSignal).toBe(0);
  });

  it('accumulates across two separate granted logins (each a genuine new grant)', async () => {
    mockPost.mockResolvedValueOnce({
      data: {
        access_token: 'tok-a', refresh_token: 'tok-r', user_id: 'u1',
        welcome_back: { granted: true, bonus: 400, days_inactive: 8 },
      },
    });
    mockGet.mockResolvedValueOnce({ data: { id: 'u1', username: 'commander' } });
    await act(async () => {
      await captured!.login('commander', 'pw-not-checked');
    });
    expect(captured!.welcomeBackSignal).toBe(1);

    mockPost.mockResolvedValueOnce({
      data: {
        access_token: 'tok-b', refresh_token: 'tok-r2', user_id: 'u1',
        welcome_back: { granted: true, bonus: 500, days_inactive: 40 },
      },
    });
    mockGet.mockResolvedValueOnce({ data: { id: 'u1', username: 'commander' } });
    await act(async () => {
      await captured!.login('commander', 'pw-not-checked');
    });

    expect(captured!.welcomeBackSignal).toBe(2);
    expect(captured!.lastWelcomeBack).toEqual({ granted: true, bonus: 500, days_inactive: 40 });
  });
});
