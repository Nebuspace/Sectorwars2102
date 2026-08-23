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

const axiosError = (status: number) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status },
  });

describe('MarketPredictionInterface scope honesty (LEG-1206)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('reports 403 as PLAYERS_VIEW denial, not bare Failed to load', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<MarketPredictionInterface />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/PLAYERS_VIEW|Access denied/i);
    expect(alert).not.toMatch(/Failed to load predictions/i);
  });

  it('reports 429 as admin rate-limit', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<MarketPredictionInterface />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/rate limit/i);
  });
});

describe('MarketPredictionInterface accuracy secondary honesty (LEG-1260)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
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
