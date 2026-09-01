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

/**
 * LEG-3663 Soft-ORDER — CentralNexusManager TypeError/Network Error densify.
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
});
