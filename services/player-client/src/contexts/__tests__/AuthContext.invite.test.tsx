// @vitest-environment jsdom
/**
 * AuthContext — region invite_code on register + OAuth invite query (LEG-31).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

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

import { AuthProvider, useAuth } from '../AuthContext';

let captured: ReturnType<typeof useAuth> | null = null;
function Consumer() {
  captured = useAuth();
  return null;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('AuthContext region-invite register', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let hrefWrites: string[];

  beforeEach(async () => {
    localStorage.clear();
    sessionStorage.clear();
    mockPost.mockReset();
    mockGet.mockReset();
    hrefWrites = [];
    vi.stubGlobal('location', {
      hostname: 'localhost',
      href: 'http://localhost:3000/',
      origin: 'http://localhost:3000',
      search: '',
    });
    Object.defineProperty(window.location, 'href', {
      configurable: true,
      set(v: string) {
        hrefWrites.push(v);
      },
      get() {
        return hrefWrites[hrefWrites.length - 1] ?? 'http://localhost:3000/';
      },
    });

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
    vi.unstubAllGlobals();
  });

  it('POSTs invite_code when a valid code is supplied', async () => {
    mockPost
      .mockResolvedValueOnce({ data: {} })
      .mockResolvedValueOnce({
        data: { access_token: 'a', refresh_token: 'r', user_id: 'u1' },
      });
    mockGet.mockResolvedValueOnce({ data: { id: 'u1', username: 'newbie' } });

    await act(async () => {
      await captured!.register('newbie', 'n@ex.com', 'password1', 'OwnerCode_9');
    });

    const registerCall = mockPost.mock.calls.find(
      (c) => typeof c[0] === 'string' && String(c[0]).includes('/auth/register'),
    );
    expect(registerCall?.[1]).toEqual({
      username: 'newbie',
      email: 'n@ex.com',
      password: 'password1',
      invite_code: 'OwnerCode_9',
    });
  });

  it('omits invite_code when empty (D10 default path)', async () => {
    mockPost
      .mockResolvedValueOnce({ data: {} })
      .mockResolvedValueOnce({
        data: { access_token: 'a', refresh_token: 'r', user_id: 'u1' },
      });
    mockGet.mockResolvedValueOnce({ data: { id: 'u1', username: 'newbie' } });

    await act(async () => {
      await captured!.register('newbie', 'n@ex.com', 'password1');
    });

    const registerCall = mockPost.mock.calls.find(
      (c) => typeof c[0] === 'string' && String(c[0]).includes('/auth/register'),
    );
    expect(registerCall?.[1]).toEqual({
      username: 'newbie',
      email: 'n@ex.com',
      password: 'password1',
    });
    expect(registerCall?.[1]).not.toHaveProperty('invite_code');
  });

  it('registerWithOAuth appends sanitized invite; loginWithOAuth does not', () => {
    captured!.registerWithOAuth('github', 'OwnerCode_9');
    expect(hrefWrites.some((u) => u.includes('register=true') && u.includes('invite=OwnerCode_9'))).toBe(
      true,
    );

    hrefWrites.length = 0;
    captured!.loginWithOAuth('github');
    expect(hrefWrites.some((u) => u.includes('invite='))).toBe(false);
  });
});
