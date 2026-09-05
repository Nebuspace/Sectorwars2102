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

describe('MarketPredictionInterface TypeError densify (LEG-3188)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('reports predictions load TypeError as scope-honest fallback via role=alert', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<MarketPredictionInterface />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Failed to load predictions/i);
    expect(alert).not.toMatch(/Failed to fetch/i);
    expect(alert).not.toMatch(/TypeError/i);
  });

  it('does not leak raw TypeError when accuracy poll collapses but predictions succeed', async () => {
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

    const text = document.body.textContent ?? '';
    expect(text).not.toMatch(/TypeError/i);
    expect(text).not.toMatch(/Failed to fetch/i);
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

describe('MarketPredictionInterface axios Network Error densify (LEG-3512)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios-shaped Network Error on predictions load', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<MarketPredictionInterface />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Failed to load predictions/i);
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
  });
});
