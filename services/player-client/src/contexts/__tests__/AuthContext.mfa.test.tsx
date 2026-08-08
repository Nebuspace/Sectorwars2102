// @vitest-environment jsdom
/**
 * AuthContext — MFA-required login flow (WO-FIX-MFA-BYPASS-LOGIN-ROUTES).
 *
 * Pins login()'s new requires_mfa handling: a 200 response carrying
 * `requires_mfa: true` (and no tokens) throws a typed MFARequiredError
 * instead of "succeeding" with no auth state, and a retry with mfaCode
 * populated completes the login exactly like the non-MFA path. Mirrors
 * AuthContext.welcomeBack.test.tsx's real-provider technique: mount the REAL
 * AuthProvider, capture useAuth() via a Consumer, drive it through axios
 * mocks (jsdom, no live server).
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

vi.mock('../../services/apiClient', () => ({
  refreshAccessToken: vi.fn(),
}));

import { AuthProvider, useAuth, MFARequiredError } from '../AuthContext';

let captured: ReturnType<typeof useAuth> | null = null;
function Consumer() {
  captured = useAuth();
  return null;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('AuthContext MFA-required login flow', () => {
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

  it('throws MFARequiredError and stores no tokens when the account has MFA enabled and no code was sent', async () => {
    mockPost.mockResolvedValueOnce({
      data: {
        access_token: '',
        refresh_token: '',
        token_type: 'bearer',
        user_id: 'u1',
        requires_mfa: true,
        mfa_enabled: true,
      },
    });

    let caught: unknown = null;
    await act(async () => {
      try {
        await captured!.login('commander', 'pw');
      } catch (err) {
        caught = err;
      }
    });

    expect(caught).toBeInstanceOf(MFARequiredError);
    expect(localStorage.getItem('accessToken')).toBeNull();
    expect(localStorage.getItem('refreshToken')).toBeNull();
    expect(captured!.isAuthenticated).toBe(false);
    // /auth/me is never reached -- requires_mfa short-circuits before it.
    expect(mockGet).not.toHaveBeenCalled();
  });

  it('sends mfa_code and completes login on retry with a valid code', async () => {
    mockPost.mockResolvedValueOnce({
      data: {
        access_token: '',
        refresh_token: '',
        user_id: 'u1',
        requires_mfa: true,
        mfa_enabled: true,
      },
    });
    await act(async () => {
      try {
        await captured!.login('commander', 'pw');
      } catch {
        // expected MFARequiredError
      }
    });

    mockPost.mockResolvedValueOnce({
      data: { access_token: 'tok-a', refresh_token: 'tok-r', user_id: 'u1', requires_mfa: false },
    });
    mockGet.mockResolvedValueOnce({ data: { id: 'u1', username: 'commander' } });

    await act(async () => {
      await captured!.login('commander', 'pw', '123456');
    });

    // Second call carried the code in the JSON body.
    const secondCallBody = mockPost.mock.calls[1][1];
    expect(secondCallBody).toMatchObject({ username: 'commander', password: 'pw', mfa_code: '123456' });
    expect(captured!.isAuthenticated).toBe(true);
    expect(localStorage.getItem('accessToken')).toBe('tok-a');
  });

  it('a non-MFA account logs in unchanged (no mfa_code sent, no requires_mfa in response)', async () => {
    mockPost.mockResolvedValueOnce({
      data: { access_token: 'tok-a', refresh_token: 'tok-r', user_id: 'u1' },
    });
    mockGet.mockResolvedValueOnce({ data: { id: 'u1', username: 'commander' } });

    await act(async () => {
      await captured!.login('commander', 'pw');
    });

    const firstCallBody = mockPost.mock.calls[0][1];
    expect(firstCallBody).not.toHaveProperty('mfa_code');
    expect(captured!.isAuthenticated).toBe(true);
    expect(localStorage.getItem('accessToken')).toBe('tok-a');
  });
});
