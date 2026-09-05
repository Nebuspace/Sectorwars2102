import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import CentralNexusManager from './CentralNexusManager';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

const okStatus = {
  data: {
    exists: true,
    status: 'active',
    total_sectors: 1,
    total_ports: 1,
    total_planets: 1,
  },
};

const okClusters = { data: [] as unknown[] };

const okStats = {
  data: {
    total_sectors: 1,
    total_ports: 1,
    total_planets: 1,
    total_warp_gates: 0,
    active_players: null,
    daily_traffic: null,
    clusters: [],
  },
};

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
  expect(text).not.toMatch(/^HTTP \d+$/);
  expect(text).not.toContain('Request failed with status code');
}

/**
 * LEG-3663 Soft-ORDER — CentralNexusManager TypeError/Network Error densify.
 * LEG-3901 Soft-ORDER — 403/429 HTTP honesty densify.
 */
describe('CentralNexusManager typeErrorHonesty densify (LEG-3663)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('collapses axios Network Error on nexus status load without leaking raw transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/nexus/status')) {
        throw new Error('Network Error');
      }
      if (url.includes('/nexus/clusters')) return okClusters;
      if (url.includes('/nexus/stats')) return okStats;
      return { data: {} };
    });

    render(<CentralNexusManager />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Failed to load nexus status/i);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses axios Network Error on clusters load without leaking raw transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/nexus/status')) return okStatus;
      if (url.includes('/nexus/clusters')) {
        throw new Error('Network Error');
      }
      if (url.includes('/nexus/stats')) return okStats;
      return { data: {} };
    });

    render(<CentralNexusManager />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Failed to load clusters/i);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses axios Network Error on nexus stats load without leaking raw transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/nexus/status')) return okStatus;
      if (url.includes('/nexus/clusters')) return okClusters;
      if (url.includes('/nexus/stats')) {
        throw new Error('Network Error');
      }
      return { data: {} };
    });

    render(<CentralNexusManager />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Failed to load nexus stats/i);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses TypeError Failed to fetch on nexus status load without leaking transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/nexus/status')) {
        throw new TypeError('Failed to fetch');
      }
      if (url.includes('/nexus/clusters')) return okClusters;
      if (url.includes('/nexus/stats')) return okStats;
      return { data: {} };
    });

    render(<CentralNexusManager />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Failed to load nexus status/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on clusters load without leaking transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/nexus/status')) return okStatus;
      if (url.includes('/nexus/clusters')) {
        throw new TypeError('Failed to fetch');
      }
      if (url.includes('/nexus/stats')) return okStats;
      return { data: {} };
    });

    render(<CentralNexusManager />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Failed to load clusters/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on nexus stats load without leaking transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/nexus/status')) return okStatus;
      if (url.includes('/nexus/clusters')) return okClusters;
      if (url.includes('/nexus/stats')) {
        throw new TypeError('Failed to fetch');
      }
      return { data: {} };
    });

    render(<CentralNexusManager />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Failed to load nexus stats/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('surfaces 403 with nexus scope copy when status GET is denied', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/nexus/status')) {
        throw axiosError(403);
      }
      if (url.includes('/nexus/clusters')) return okClusters;
      if (url.includes('/nexus/stats')) return okStats;
      return { data: {} };
    });

    render(<CentralNexusManager />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Access denied|nexus scopes/i);
    expect(text).not.toMatch(/\b403\b/);
    expect(text).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(text);
  });

  it('surfaces 429 as admin rate-limit copy on nexus status GET', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/nexus/status')) {
        throw axiosError(429);
      }
      if (url.includes('/nexus/clusters')) return okClusters;
      if (url.includes('/nexus/stats')) return okStats;
      return { data: {} };
    });

    render(<CentralNexusManager />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/rate limit/i);
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/rate limit/i);
    expect(text).not.toMatch(/\b429\b/);
    expect(text).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(text);
  });
});
