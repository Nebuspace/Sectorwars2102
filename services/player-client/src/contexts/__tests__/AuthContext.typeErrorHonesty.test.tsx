// @vitest-environment jsdom
/**
 * LEG-3792 Soft-ORDER — AuthContext login/refresh TypeError densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mockPost = vi.fn();
const mockGet = vi.fn();
const mockRefreshAccessToken = vi.fn();

vi.mock('axios', () => ({
  default: {
    post: (...args: unknown[]) => mockPost(...args),
    get: (...args: unknown[]) => mockGet(...args),
    defaults: { headers: { common: {} as Record<string, string> } },
    interceptors: { response: { use: vi.fn(() => 1), eject: vi.fn() } },
  },
}));

vi.mock('../../services/apiClient', () => ({
  refreshAccessToken: (...args: unknown[]) => mockRefreshAccessToken(...args),
}));

import {
  AuthProvider,
  useAuth,
  AUTH_NETWORK_FALLBACKS,
  formatAuthTransportError,
} from '../AuthContext';

let captured: ReturnType<typeof useAuth> | null = null;
function Consumer() {
  captured = useAuth();
  return null;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

function assertNoTransportLeak(text: string) {
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
  expect(text).not.toMatch(/Network Error/i);
}

describe('formatAuthTransportError (LEG-3792)', () => {
  it('collapses TypeError Failed to fetch for login fallback', () => {
    const text = formatAuthTransportError(
      new TypeError('Failed to fetch'),
      AUTH_NETWORK_FALLBACKS.login,
    );
    expect(text).toBe(AUTH_NETWORK_FALLBACKS.login);
    assertNoTransportLeak(text);
  });

  it('collapses Network Error and Failed to fetch non-TypeError', () => {
    expect(formatAuthTransportError(new Error('Network Error'), AUTH_NETWORK_FALLBACKS.login)).toBe(
      AUTH_NETWORK_FALLBACKS.login,
    );
    expect(
      formatAuthTransportError(new Error('Failed to fetch'), AUTH_NETWORK_FALLBACKS.refresh),
    ).toBe(AUTH_NETWORK_FALLBACKS.refresh);
    assertNoTransportLeak(
      formatAuthTransportError(new Error('Network Error'), AUTH_NETWORK_FALLBACKS.login),
    );
  });

  it('preserves non-transport server detail', () => {
    expect(formatAuthTransportError(new Error('account_locked'), AUTH_NETWORK_FALLBACKS.login)).toBe(
      'account_locked',
    );
  });
});

describe('AuthContext login/refresh typeErrorHonesty (LEG-3792)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(async () => {
    localStorage.clear();
    mockPost.mockReset();
    mockGet.mockReset();
    mockRefreshAccessToken.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    captured = null;

    await act(async () => {
      root.render(
        <AuthProvider>
          <Consumer />
        </AuthProvider>,
      );
      await flush();
    });
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it('login TypeError surfaces player-safe fallback without raw transport text', async () => {
    mockPost.mockRejectedValue(new TypeError('Failed to fetch'));

    try {
      await captured!.login('commander', 'pw');
      throw new Error('expected login to reject');
    } catch (err) {
      const message = (err as Error).message;
      expect(message).toBe(AUTH_NETWORK_FALLBACKS.login);
      assertNoTransportLeak(message);
    }
  });

  it('login Network Error surfaces player-safe fallback without raw transport text', async () => {
    mockPost.mockRejectedValue(new Error('Network Error'));

    try {
      await captured!.login('commander', 'pw');
      throw new Error('expected login to reject');
    } catch (err) {
      const message = (err as Error).message;
      expect(message).toBe(AUTH_NETWORK_FALLBACKS.login);
      assertNoTransportLeak(message);
    }
  });

  it('refreshToken null token surfaces refresh fallback without transport leak', async () => {
    mockRefreshAccessToken.mockResolvedValue(null);

    await expect(captured!.refreshToken()).rejects.toThrow(AUTH_NETWORK_FALLBACKS.refresh);
    try {
      await captured!.refreshToken();
    } catch (err) {
      assertNoTransportLeak((err as Error).message);
    }
  });
});
