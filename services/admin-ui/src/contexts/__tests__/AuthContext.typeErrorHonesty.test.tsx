import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import React from 'react';
import {
  AuthProvider,
  useAuth,
  surfaceAuthError,
  AUTH_NETWORK_FALLBACKS,
} from '../AuthContext';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    interceptors: {
      response: {
        use: vi.fn(() => 0),
        eject: vi.fn(),
      },
    },
  },
}));

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
}

function captureAuthError(run: () => Promise<unknown>): Promise<string> {
  return run().then(
    () => {
      throw new Error('expected auth call to reject');
    },
    (err: unknown) => (err instanceof Error ? err.message : String(err)),
  );
}

function httpErr(status: number) {
  return Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: {} },
  });
}

/**
 * LEG-3789 Soft-ORDER — AuthContext TypeError/Network Error densify.
 */
describe('AuthContext surfaceAuthError formatter (LEG-3789)', () => {
  it('collapses TypeError Failed to fetch for login fallback', () => {
    try {
      surfaceAuthError(new TypeError('Failed to fetch'), AUTH_NETWORK_FALLBACKS.login);
    } catch (err) {
      const text = (err as Error).message;
      expect(text).toBe(AUTH_NETWORK_FALLBACKS.login);
      assertNoTransportLeak(text);
      return;
    }
    throw new Error('expected surfaceAuthError to throw');
  });

  it('collapses Network Error for refresh fallback', () => {
    try {
      surfaceAuthError(new Error('Network Error'), AUTH_NETWORK_FALLBACKS.refresh);
    } catch (err) {
      const text = (err as Error).message;
      expect(text).toBe(AUTH_NETWORK_FALLBACKS.refresh);
      assertNoTransportLeak(text);
      return;
    }
    throw new Error('expected surfaceAuthError to throw');
  });

  it('collapses Failed to fetch for verifyMFA fallback', () => {
    try {
      surfaceAuthError(new Error('Failed to fetch'), AUTH_NETWORK_FALLBACKS.verifyMFA);
    } catch (err) {
      const text = (err as Error).message;
      expect(text).toBe(AUTH_NETWORK_FALLBACKS.verifyMFA);
      assertNoTransportLeak(text);
      return;
    }
    throw new Error('expected surfaceAuthError to throw');
  });

  it('preserves intentional invalid-credentials copy', () => {
    expect(() =>
      surfaceAuthError(new Error('Invalid username or password'), AUTH_NETWORK_FALLBACKS.login),
    ).toThrow('Invalid username or password');
  });
});

describe('AuthContext typeErrorHonesty densify (LEG-3789)', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
    localStorage.clear();
    vi.mocked(api.get).mockReset();
    vi.mocked(api.interceptors.response.use).mockReturnValue(0);
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.spyOn(console, 'warn').mockImplementation(() => {});
  });

  it('login TypeError surfaces operator-safe fallback without raw transport text', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'));

    const { result } = renderHook(() => useAuth(), {
      wrapper: ({ children }) => <AuthProvider>{children}</AuthProvider>,
    });

    await waitFor(() => expect(result.current.isLoading).toBe(false));

    const message = await captureAuthError(() =>
      result.current.login('admin', 'hunter2'),
    );
    expect(message).toBe(AUTH_NETWORK_FALLBACKS.login);
    assertNoTransportLeak(message);
  });

  it('login Network Error surfaces operator-safe fallback without raw transport text', async () => {
    fetchMock.mockRejectedValue(new Error('Network Error'));

    const { result } = renderHook(() => useAuth(), {
      wrapper: ({ children }) => <AuthProvider>{children}</AuthProvider>,
    });

    await waitFor(() => expect(result.current.isLoading).toBe(false));

    const message = await captureAuthError(() =>
      result.current.login('admin', 'hunter2'),
    );
    expect(message).toBe(AUTH_NETWORK_FALLBACKS.login);
    assertNoTransportLeak(message);
  });

  it('verifyMFA TypeError surfaces operator-safe fallback without raw transport text', async () => {
    const { result } = renderHook(() => useAuth(), {
      wrapper: ({ children }) => <AuthProvider>{children}</AuthProvider>,
    });

    await waitFor(() => expect(result.current.isLoading).toBe(false));

    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'));

    const message = await captureAuthError(() =>
      result.current.verifyMFA('123456', 'session-token'),
    );
    expect(message).toBe(AUTH_NETWORK_FALLBACKS.verifyMFA);
    assertNoTransportLeak(message);
  });

  it('refreshToken Network Error surfaces operator-safe fallback without raw transport text', async () => {
    localStorage.setItem('refreshToken', 'refresh-token');
    localStorage.setItem('accessToken', 'access-token');
    vi.mocked(api.get).mockRejectedValue({ response: { status: 401 } });

    const { result } = renderHook(() => useAuth(), {
      wrapper: ({ children }) => <AuthProvider>{children}</AuthProvider>,
    });

    await waitFor(() => expect(result.current.isLoading).toBe(false));

    localStorage.setItem('refreshToken', 'refresh-token');
    fetchMock.mockRejectedValue(new Error('Network Error'));

    const message = await captureAuthError(() => result.current.refreshToken());
    expect(message).toBe(AUTH_NETWORK_FALLBACKS.refresh);
    assertNoTransportLeak(message);
  });

  it('initial auth/me 429 preserves tokens without clearing auth data (LEG-3824)', async () => {
    localStorage.setItem('accessToken', 'access-token');
    localStorage.setItem('refreshToken', 'refresh-token');
    vi.mocked(api.get).mockRejectedValue(httpErr(429));

    const warnSpy = vi.spyOn(console, 'warn');

    const { result } = renderHook(() => useAuth(), {
      wrapper: ({ children }) => <AuthProvider>{children}</AuthProvider>,
    });

    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(localStorage.getItem('accessToken')).toBe('access-token');
    expect(localStorage.getItem('refreshToken')).toBe('refresh-token');
    expect(result.current.user).toBeNull();
    expect(warnSpy).toHaveBeenCalledWith(
      'Rate limit hit during auth check - will retry in a moment',
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('refreshToken fetch 429 rejects and clears stored tokens (LEG-3824 invent=0)', async () => {
    localStorage.setItem('refreshToken', 'refresh-token');
    localStorage.setItem('accessToken', 'access-token');
    vi.mocked(api.get).mockRejectedValue(httpErr(401));

    const { result } = renderHook(() => useAuth(), {
      wrapper: ({ children }) => <AuthProvider>{children}</AuthProvider>,
    });

    await waitFor(() => expect(result.current.isLoading).toBe(false));

    localStorage.setItem('refreshToken', 'refresh-token');
    localStorage.setItem('accessToken', 'access-token');
    fetchMock.mockResolvedValue({
      ok: false,
      status: 429,
      text: async () => 'Too Many Requests',
    });

    const message = await captureAuthError(() => result.current.refreshToken());
    expect(message).toBe('Server returned status 429');
    expect(localStorage.getItem('accessToken')).toBeNull();
    expect(localStorage.getItem('refreshToken')).toBeNull();
  });
});
