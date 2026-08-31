import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { RouteOptimizationDisplay } from './RouteOptimizationDisplay';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

vi.mock('../../contexts/WebSocketContext', () => ({
  useAIUpdates: () => undefined,
}));

const axiosError = (status: number) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status },
  });

describe('RouteOptimizationDisplay scope honesty (LEG-1326)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('reports 403 as PLAYERS_VIEW denial on primary load', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<RouteOptimizationDisplay />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/PLAYERS_VIEW|Access denied/i);
  });

  it('reports 429 as admin rate-limit on primary load', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<RouteOptimizationDisplay />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    expect(screen.getByRole('alert').textContent ?? '').toMatch(/rate limit/i);
  });

  it('surfaces formatAdminApiError fallback on primary load TypeError (LEG-3058)', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<RouteOptimizationDisplay />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Failed to load routes/i);
    expect(alert).not.toMatch(/TypeError/i);
    expect(alert).not.toMatch(/Failed to fetch/i);
  });
});

describe('RouteOptimizationDisplay route-stats secondary honesty (LEG-1260)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('surfaces PLAYERS_VIEW when second route-optimization GET is 403', async () => {
    let calls = 0;
    vi.mocked(api.get).mockImplementation(async () => {
      calls += 1;
      if (calls === 1) {
        return { data: { active_optimizations: [], optimization_stats: null } };
      }
      throw axiosError(403);
    });

    render(<RouteOptimizationDisplay />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/PLAYERS_VIEW|Access denied/i);
  });

  it('surfaces admin rate-limit when second route-optimization GET is 429', async () => {
    let calls = 0;
    vi.mocked(api.get).mockImplementation(async () => {
      calls += 1;
      if (calls === 1) {
        return { data: { active_optimizations: [], optimization_stats: null } };
      }
      throw axiosError(429);
    });

    render(<RouteOptimizationDisplay />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    expect(screen.getByRole('alert').textContent ?? '').toMatch(/rate limit/i);
  });
});
