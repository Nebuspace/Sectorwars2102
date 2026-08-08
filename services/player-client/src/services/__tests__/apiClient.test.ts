// @vitest-environment jsdom
/**
 * apiClient — JWT attach + single-flight 401 refresh (WO-TESTCOV-PLAYER-API-CLIENT).
 *
 * Module-level refresh lock means each case reloads the module via resetModules.
 */
import axios, { type AxiosAdapter, type InternalAxiosRequestConfig } from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

type FakeResponse = {
  status: number;
  data: unknown;
  headers?: Record<string, string>;
};

type ApiClientMod = typeof import('../apiClient');

describe('apiClient', () => {
  let apiClient: ApiClientMod['default'];
  let getAccessToken: ApiClientMod['getAccessToken'];
  let refreshAccessToken: ApiClientMod['refreshAccessToken'];
  let route: (config: InternalAxiosRequestConfig) => Promise<FakeResponse>;
  let refreshPost: ReturnType<typeof vi.fn>;
  let createSpy: ReturnType<typeof vi.spyOn>;
  const hrefSetter = vi.fn();

  async function loadModule() {
    vi.resetModules();
    const mod = await import('../apiClient');
    apiClient = mod.default;
    getAccessToken = mod.getAccessToken;
    refreshAccessToken = mod.refreshAccessToken;
  }

  beforeEach(async () => {
    localStorage.clear();
    delete axios.defaults.headers.common['Authorization'];
    hrefSetter.mockReset();
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: {
        origin: 'http://localhost:5173',
        href: '/',
      },
    });
    Object.defineProperty(window.location, 'href', {
      configurable: true,
      get: () => '/',
      set: hrefSetter,
    });

    refreshPost = vi.fn();
    const realCreate = axios.create.bind(axios);
    createSpy = vi.spyOn(axios, 'create').mockImplementation((config?: Parameters<typeof axios.create>[0]) => {
      // Refresh path uses a pristine `axios.create()` with no config.
      if (config === undefined) {
        return { post: refreshPost } as unknown as ReturnType<typeof axios.create>;
      }
      return realCreate(config);
    });

    await loadModule();

    route = async () => ({ status: 200, data: {} });
    const adapter: AxiosAdapter = async (config) => {
      const result = await route(config as InternalAxiosRequestConfig);
      if (result.status >= 400) {
        throw new axios.AxiosError(
          `Request failed with status code ${result.status}`,
          String(result.status),
          config,
          {},
          {
            data: result.data,
            status: result.status,
            statusText: '',
            headers: result.headers ?? {},
            config,
          },
        );
      }
      return {
        data: result.data,
        status: result.status,
        statusText: 'OK',
        headers: result.headers ?? {},
        config,
      };
    };
    apiClient.defaults.adapter = adapter;
  });

  afterEach(() => {
    createSpy.mockRestore();
  });

  it('getAccessToken reads localStorage', () => {
    expect(getAccessToken()).toBeNull();
    localStorage.setItem('accessToken', 'tok-a');
    expect(getAccessToken()).toBe('tok-a');
  });

  it('attaches Bearer token from localStorage on requests', async () => {
    localStorage.setItem('accessToken', 'live-token');
    let seenAuth: string | undefined;
    route = async (config) => {
      seenAuth = config.headers?.Authorization as string | undefined;
      return { status: 200, data: { ok: true } };
    };

    const res = await apiClient.get('/api/v1/ping');
    expect(res.data).toEqual({ ok: true });
    expect(seenAuth).toBe('Bearer live-token');
  });

  it('refreshes once on 401 and retries the original request', async () => {
    localStorage.setItem('accessToken', 'stale');
    localStorage.setItem('refreshToken', 'refresh-1');
    refreshPost.mockResolvedValue({
      data: { access_token: 'fresh', refresh_token: 'refresh-2' },
    });

    let calls = 0;
    route = async (config) => {
      calls += 1;
      if (calls === 1) return { status: 401, data: { detail: 'expired' } };
      expect(config.headers?.Authorization).toBe('Bearer fresh');
      return { status: 200, data: { recovered: true } };
    };

    const res = await apiClient.get('/api/v1/secure');
    expect(res.data).toEqual({ recovered: true });
    expect(refreshPost).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem('accessToken')).toBe('fresh');
    expect(localStorage.getItem('refreshToken')).toBe('refresh-2');
    expect(axios.defaults.headers.common['Authorization']).toBe('Bearer fresh');
  });

  it('single-flights concurrent 401s through one refresh', async () => {
    localStorage.setItem('accessToken', 'stale');
    localStorage.setItem('refreshToken', 'refresh-1');

    let resolveRefresh!: (v: unknown) => void;
    const refreshGate = new Promise((r) => {
      resolveRefresh = r;
    });
    refreshPost.mockImplementation(() =>
      refreshGate.then(() => ({
        data: { access_token: 'fresh', refresh_token: 'refresh-2' },
      })),
    );

    route = async (config) => {
      const auth = String(config.headers?.Authorization ?? '');
      if (!auth.includes('fresh')) {
        return { status: 401, data: {} };
      }
      return { status: 200, data: { url: String(config.url) } };
    };

    try {
      const p1 = apiClient.get('/a');
      const p2 = apiClient.get('/b');
      await vi.waitFor(() => expect(refreshPost).toHaveBeenCalledTimes(1));
      resolveRefresh(undefined);
      const [r1, r2] = await Promise.all([p1, p2]);
      expect(r1.data).toEqual({ url: '/a' });
      expect(r2.data).toEqual({ url: '/b' });
      expect(refreshPost).toHaveBeenCalledTimes(1);
    } finally {
      resolveRefresh?.(undefined);
    }
  });

  it('refreshAccessToken returns null and clears storage when refresh fails', async () => {
    localStorage.setItem('accessToken', 'stale');
    localStorage.setItem('refreshToken', 'dead');
    localStorage.setItem('userId', 'u1');
    refreshPost.mockRejectedValue(new Error('refresh dead'));

    const token = await refreshAccessToken();
    expect(token).toBeNull();
    expect(localStorage.getItem('accessToken')).toBeNull();
    expect(localStorage.getItem('refreshToken')).toBeNull();
    expect(localStorage.getItem('userId')).toBeNull();
  });

  it('redirects to / when 401 refresh fails', async () => {
    localStorage.setItem('accessToken', 'stale');
    localStorage.setItem('refreshToken', 'dead');
    refreshPost.mockRejectedValue(new Error('refresh dead'));
    route = async () => ({ status: 401, data: {} });

    await expect(apiClient.get('/api/v1/secure')).rejects.toBeTruthy();
    expect(hrefSetter).toHaveBeenCalledWith('/');
  });

  it('refreshAccessToken returns null when no refresh token is stored', async () => {
    expect(await refreshAccessToken()).toBeNull();
    expect(refreshPost).not.toHaveBeenCalled();
  });
});
