import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MarketPredictionInterface } from './MarketPredictionInterface';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

vi.mock('../../contexts/WebSocketContext', () => ({
  useAIUpdates: () => undefined,
}));

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
}

/**
 * LEG-3743 Soft-ORDER — MarketPredictionInterface TypeError/network + HTTP honesty densify.
 */
describe('MarketPredictionInterface typeErrorHonesty densify (LEG-3743)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on predictions load without leaking transport text', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<MarketPredictionInterface />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Failed to load predictions/i);
    assertNoTransportLeak(alert);
  });

  it('collapses TypeError Failed to fetch on predictions load without leaking transport text', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<MarketPredictionInterface />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Failed to load predictions/i);
    assertNoTransportLeak(alert);
  });

  it('surfaces 401 as authentication-required copy on predictions load', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(401));

    render(<MarketPredictionInterface />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Authentication required/i);
    expect(alert).toMatch(/log in as an admin user/i);
    assertNoTransportLeak(alert);
  });

  it('surfaces 403 with PLAYERS_VIEW scope hint when no server detail', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/accuracy')) {
        return { data: [] };
      }
      throw axiosError(403);
    });

    render(<MarketPredictionInterface />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/PLAYERS_VIEW/i);
    expect(alert).toMatch(/market predictions/i);
    assertNoTransportLeak(alert);
  });

  it('surfaces server detail on 403 when provided', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/accuracy')) {
        return { data: [] };
      }
      throw axiosError(403, 'Missing scope admin.players.view (PLAYERS_VIEW)');
    });

    render(<MarketPredictionInterface />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    expect(screen.getByRole('alert').textContent ?? '').toMatch(
      /Missing scope admin\.players\.view \(PLAYERS_VIEW\)/i,
    );
  });

  it('surfaces 429 as admin rate-limit copy on predictions load', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<MarketPredictionInterface />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/rate limit/i);
    assertNoTransportLeak(alert);
  });

  it('does not leak transport text when accuracy poll collapses but predictions succeed', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/accuracy')) {
        throw new TypeError('Failed to fetch');
      }
      return { data: [] };
    });

    render(<MarketPredictionInterface />);

    await waitFor(() => {
      expect(screen.getByText(/Active Predictions/i)).toBeTruthy();
    });

    expect(screen.queryByRole('alert')).toBeNull();
    assertNoTransportLeak(document.body.textContent ?? '');
  });

  it('surfaces PLAYERS_VIEW on accuracy 403 when predictions succeed', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/accuracy')) {
        throw axiosError(403);
      }
      return { data: [] };
    });

    render(<MarketPredictionInterface />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/PLAYERS_VIEW|Access denied|accuracy/i);
    assertNoTransportLeak(alert);
  });

  it('surfaces admin rate-limit on accuracy 429 when predictions succeed', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/accuracy')) {
        throw axiosError(429);
      }
      return { data: [] };
    });

    render(<MarketPredictionInterface />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    expect(screen.getByRole('alert').textContent ?? '').toMatch(/rate limit/i);
  });
});
