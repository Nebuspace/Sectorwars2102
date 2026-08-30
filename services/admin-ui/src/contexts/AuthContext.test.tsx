import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import { api } from '../utils/auth';
import { AuthProvider, useAuth } from './AuthContext';

vi.mock('../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    interceptors: {
      response: { use: vi.fn().mockReturnValue(1), eject: vi.fn() },
      request: { use: vi.fn().mockReturnValue(0), eject: vi.fn() },
    },
  },
}));

const mockedApi = vi.mocked(api, true);

function jsonResponse(body: unknown, ok = true) {
  return Promise.resolve({
    ok,
    text: () => Promise.resolve(JSON.stringify(body)),
    json: () => Promise.resolve(body),
  } as Response);
}

// A tiny probe component that surfaces AuthContext state as text nodes so
// tests can assert on it without reaching into React internals.
function Probe() {
  const { isAuthenticated, isLoading, user } = useAuth();
  return (
    <div>
      <span data-testid="loading">{String(isLoading)}</span>
      <span data-testid="authed">{String(isAuthenticated)}</span>
      <span data-testid="username">{user?.username ?? 'none'}</span>
    </div>
  );
}

describe('AuthContext / AuthProvider', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.mocked(api.interceptors.response.use).mockReturnValue(1);
    vi.mocked(api.interceptors.response.eject).mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('starts unauthenticated with isLoading resolving to false when no token is stored', async () => {
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );

    await waitFor(() => expect(screen.getByTestId('loading')).toHaveTextContent('false'));
    expect(screen.getByTestId('authed')).toHaveTextContent('false');
  });

  it('hydrates the user from /auth/me when a stored accessToken is valid', async () => {
    localStorage.setItem('accessToken', 'valid-token');
    vi.mocked(api.get).mockResolvedValue({
      data: { id: '1', username: 'alice', email: 'a@x.com', is_admin: true, is_active: true, last_login: null },
    } as any);

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );

    await waitFor(() => expect(screen.getByTestId('authed')).toHaveTextContent('true'));
    expect(screen.getByTestId('username')).toHaveTextContent('alice');
    expect(api.get).toHaveBeenCalledWith('/api/v1/auth/me');
  });

  it('clears auth data when the stored token is rejected and refresh has no token', async () => {
    localStorage.setItem('accessToken', 'stale-token');
    vi.mocked(api.get).mockRejectedValue({ response: { status: 401 } });

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );

    await waitFor(() => expect(screen.getByTestId('authed')).toHaveTextContent('false'));
    expect(localStorage.getItem('accessToken')).toBeNull();
  });

  it('login() stores tokens and returns requiresMFA:false on a direct success', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string) => {
        if (url.includes('/auth/login/direct')) {
          return jsonResponse({ access_token: 'AT', refresh_token: 'RT', user_id: 'u1' });
        }
        if (url.includes('/auth/me')) {
          return jsonResponse({ id: 'u1', username: 'bob', email: '', is_admin: false, is_active: true, last_login: null });
        }
        return jsonResponse({});
      })
    );

    let hookRef: ReturnType<typeof useAuth> | null = null;
    function Capture() {
      hookRef = useAuth();
      return null;
    }
    render(
      <AuthProvider>
        <Capture />
      </AuthProvider>
    );
    await waitFor(() => expect(hookRef).not.toBeNull());

    let result: any;
    await act(async () => {
      result = await hookRef!.login('bob', 'pw');
    });

    expect(result).toEqual({ requiresMFA: false });
    expect(localStorage.getItem('accessToken')).toBe('AT');
  });

  it('login() returns requiresMFA:true with the session token when the server requests it', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string) => {
        if (url.includes('/auth/login/direct')) {
          return jsonResponse({ requires_mfa: true, session_token: 'sess-xyz' });
        }
        return jsonResponse({});
      })
    );

    let hookRef: ReturnType<typeof useAuth> | null = null;
    function Capture() {
      hookRef = useAuth();
      return null;
    }
    render(
      <AuthProvider>
        <Capture />
      </AuthProvider>
    );
    await waitFor(() => expect(hookRef).not.toBeNull());

    let result: any;
    await act(async () => {
      result = await hookRef!.login('bob', 'pw');
    });

    expect(result).toEqual({ requiresMFA: true, sessionToken: 'sess-xyz' });
    // No tokens should be persisted mid-MFA-flow.
    expect(localStorage.getItem('accessToken')).toBeNull();
  });

  it('login() rejects with "Invalid username or password" on a 401', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 401, text: () => Promise.resolve('') })
    );

    let hookRef: ReturnType<typeof useAuth> | null = null;
    function Capture() {
      hookRef = useAuth();
      return null;
    }
    render(
      <AuthProvider>
        <Capture />
      </AuthProvider>
    );
    await waitFor(() => expect(hookRef).not.toBeNull());

    await expect(
      act(async () => {
        await hookRef!.login('bob', 'wrong');
      })
    ).rejects.toThrow('Invalid username or password');
  });
});
