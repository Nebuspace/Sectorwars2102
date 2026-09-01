import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import CentralNexusManager from './CentralNexusManager';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: { get: vi.fn() },
}));

describe('CentralNexusManager (LEG-212 shared api)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/nexus/status')) {
        return {
          data: {
            exists: true,
            status: 'active',
            total_sectors: 10,
            total_ports: 2,
            total_planets: 3,
          },
        };
      }
      if (String(url).includes('/nexus/clusters')) {
        return { data: [] };
      }
      if (String(url).includes('/nexus/stats')) {
        return {
          data: {
            total_sectors: 10,
            total_ports: 2,
            total_planets: 3,
            total_warp_gates: 1,
            active_players: null,
            daily_traffic: null,
            clusters: [],
          },
        };
      }
      return { data: {} };
    });
  });

  it('loads nexus surfaces via shared api', async () => {
    render(<CentralNexusManager />);
    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/nexus/status');
    });
    const urls = vi.mocked(api.get).mock.calls.map(([u]) => String(u));
    expect(urls).toEqual(
      expect.arrayContaining([
        '/api/v1/nexus/status',
        '/api/v1/nexus/clusters',
        '/api/v1/nexus/stats',
      ])
    );
    expect(screen.getByRole('heading', { name: 'Central Nexus Management' })).toBeTruthy();
  });

  it('surfaces scope denial on 403 cluster load', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/nexus/clusters')) {
        throw Object.assign(new Error('HTTP 403'), {
          response: { status: 403, data: { detail: 'Missing scope admin.universe.view' } },
        });
      }
      if (String(url).includes('/nexus/status')) {
        return { data: { exists: false, status: 'not_generated', total_sectors: 0, total_ports: 0, total_planets: 0 } };
      }
      return { data: {} };
    });

    render(<CentralNexusManager />);

    await waitFor(() => {
      expect(screen.getByText(/Missing scope admin\.universe\.view/i)).toBeTruthy();
    });
  });

  it('shows rate-limit copy on 429 cluster load', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/nexus/clusters')) {
        throw Object.assign(new Error('HTTP 429'), {
          response: { status: 429, data: {} },
        });
      }
      if (String(url).includes('/nexus/status')) {
        return { data: { exists: false, status: 'not_generated', total_sectors: 0, total_ports: 0, total_planets: 0 } };
      }
      return { data: {} };
    });

    render(<CentralNexusManager />);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
  });

  it('surfaces honest fallback on cluster load TypeError/network collapse (LEG-3004)', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/nexus/clusters')) {
        throw new TypeError('Failed to fetch');
      }
      if (String(url).includes('/nexus/status')) {
        return { data: { exists: false, status: 'not_generated', total_sectors: 0, total_ports: 0, total_planets: 0 } };
      }
      return { data: {} };
    });

    render(<CentralNexusManager />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to load clusters/i)).toBeTruthy();
    });

    const text = screen.getByText(/Failed to load clusters/i).textContent ?? '';
    expect(text).not.toMatch(/TypeError/i);
    expect(text).not.toMatch(/Failed to fetch/i);
  });

  it('surfaces scope denial on 403 stats load', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/nexus/stats')) {
        throw Object.assign(new Error('HTTP 403'), {
          response: { status: 403, data: { detail: 'Missing scope admin.universe.view' } },
        });
      }
      if (String(url).includes('/nexus/status')) {
        return { data: { exists: false, status: 'not_generated', total_sectors: 0, total_ports: 0, total_planets: 0 } };
      }
      if (String(url).includes('/nexus/clusters')) {
        return { data: [] };
      }
      return { data: {} };
    });

    render(<CentralNexusManager />);

    await waitFor(() => {
      expect(screen.getByText(/Missing scope admin\.universe\.view/i)).toBeTruthy();
    });
  });

  it('shows rate-limit copy on 429 stats load', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/nexus/stats')) {
        throw Object.assign(new Error('HTTP 429'), {
          response: { status: 429, data: {} },
        });
      }
      if (String(url).includes('/nexus/status')) {
        return { data: { exists: false, status: 'not_generated', total_sectors: 0, total_ports: 0, total_planets: 0 } };
      }
      if (String(url).includes('/nexus/clusters')) {
        return { data: [] };
      }
      return { data: {} };
    });

    render(<CentralNexusManager />);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
  });

  it('surfaces scope denial on 403 status load (LEG-2716)', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/nexus/status')) {
        throw Object.assign(new Error('HTTP 403'), {
          response: { status: 403, data: { detail: 'Missing scope admin.universe.view' } },
        });
      }
      if (String(url).includes('/nexus/clusters')) {
        return { data: [] };
      }
      if (String(url).includes('/nexus/stats')) {
        return {
          data: {
            total_sectors: 10,
            total_ports: 2,
            total_planets: 3,
            total_warp_gates: 1,
            active_players: null,
            daily_traffic: null,
            clusters: [],
          },
        };
      }
      return { data: {} };
    });

    render(<CentralNexusManager />);

    await waitFor(() => {
      expect(screen.getByText(/Missing scope admin\.universe\.view/i)).toBeTruthy();
    });
  });

  it('shows rate-limit copy on 429 status load (LEG-2716)', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/nexus/status')) {
        throw Object.assign(new Error('HTTP 429'), {
          response: { status: 429, data: {} },
        });
      }
      if (String(url).includes('/nexus/clusters')) {
        return { data: [] };
      }
      if (String(url).includes('/nexus/stats')) {
        return {
          data: {
            total_sectors: 10,
            total_ports: 2,
            total_planets: 3,
            total_warp_gates: 1,
            active_players: null,
            daily_traffic: null,
            clusters: [],
          },
        };
      }
      return { data: {} };
    });

    render(<CentralNexusManager />);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
  });
});

describe('CentralNexusManager TypeError densify (LEG-3190)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('surfaces honest fallback on status load TypeError without raw transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/nexus/status')) {
        throw new TypeError('Failed to fetch');
      }
      if (String(url).includes('/nexus/clusters')) {
        return { data: [] };
      }
      if (String(url).includes('/nexus/stats')) {
        return {
          data: {
            total_sectors: 0,
            total_ports: 0,
            total_planets: 0,
            total_warp_gates: 0,
            active_players: null,
            daily_traffic: null,
            clusters: [],
          },
        };
      }
      return { data: {} };
    });

    render(<CentralNexusManager />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to load nexus status/i)).toBeTruthy();
    });

    const text = document.body.textContent ?? '';
    expect(text).not.toMatch(/TypeError/i);
    expect(text).not.toMatch(/Failed to fetch/i);
  });

  it('surfaces honest fallback on clusters load TypeError without raw transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/nexus/clusters')) {
        throw new TypeError('Failed to fetch');
      }
      if (String(url).includes('/nexus/status')) {
        return {
          data: {
            exists: false,
            status: 'not_generated',
            total_sectors: 0,
            total_ports: 0,
            total_planets: 0,
          },
        };
      }
      return { data: {} };
    });

    render(<CentralNexusManager />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to load clusters/i)).toBeTruthy();
    });

    const text = document.body.textContent ?? '';
    expect(text).not.toMatch(/TypeError/i);
    expect(text).not.toMatch(/Failed to fetch/i);
  });

  it('surfaces honest fallback on stats load TypeError without raw transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/nexus/stats')) {
        throw new TypeError('Failed to fetch');
      }
      if (String(url).includes('/nexus/status')) {
        return {
          data: {
            exists: false,
            status: 'not_generated',
            total_sectors: 0,
            total_ports: 0,
            total_planets: 0,
          },
        };
      }
      if (String(url).includes('/nexus/clusters')) {
        return { data: [] };
      }
      return { data: {} };
    });

    render(<CentralNexusManager />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to load nexus stats/i)).toBeTruthy();
    });

    const text = document.body.textContent ?? '';
    expect(text).not.toMatch(/TypeError/i);
    expect(text).not.toMatch(/Failed to fetch/i);
  });
});

describe('CentralNexusManager Network Error densify (LEG-3357)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios-shaped Network Error on status load without raw transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/nexus/status')) {
        throw new Error('Network Error');
      }
      if (String(url).includes('/nexus/clusters')) {
        return { data: [] };
      }
      if (String(url).includes('/nexus/stats')) {
        return {
          data: {
            total_sectors: 0,
            total_ports: 0,
            total_planets: 0,
            total_warp_gates: 0,
            active_players: null,
            daily_traffic: null,
            clusters: [],
          },
        };
      }
      return { data: {} };
    });

    render(<CentralNexusManager />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to load nexus status/i)).toBeTruthy();
    });

    const text = document.body.textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses axios-shaped Network Error on stats load without raw transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/nexus/stats')) {
        throw new Error('Network Error');
      }
      if (String(url).includes('/nexus/status')) {
        return {
          data: {
            exists: false,
            status: 'not_generated',
            total_sectors: 0,
            total_ports: 0,
            total_planets: 0,
          },
        };
      }
      if (String(url).includes('/nexus/clusters')) {
        return { data: [] };
      }
      return { data: {} };
    });

    render(<CentralNexusManager />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to load nexus stats/i)).toBeTruthy();
    });

    const text = document.body.textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
    expect(text).not.toMatch(/TypeError/i);
  });
});
